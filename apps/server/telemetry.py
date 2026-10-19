import logging
import os

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from apps.server.logger import logger
from apps.server.metrics import DURATION_BUCKETS, error

tracer = trace.get_tracer("zoo")


_global_done = False


def setup_telemetry(app: FastAPI):
    """Metrics, logs and traces over OTLP with OTEL_EXPORTER_OTLP_ENDPOINT; with ZOO_METRICS_PORT, the metrics are
    also served for Prometheus at :ZOO_METRICS_PORT/metrics (the Helm chart's ServiceMonitor scrapes it)."""
    otlp = bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))
    prometheus = os.environ.get("ZOO_METRICS_PORT", "")
    if not otlp and not prometheus:
        return
    setup_global(otlp, prometheus)
    if otlp:
        FastAPIInstrumentor.instrument_app(app, excluded_urls="health")

    @app.middleware("http")
    async def count_server_errors(request, call_next):
        try:
            response = await call_next(request)
        except Exception:
            error("http", status="500")
            raise
        if response.status_code >= 500:
            error("http", status=str(response.status_code))
        return response


def setup_global(otlp: bool, prometheus: str):
    """The process-wide parts, once: `python main.py` builds the app twice (main.py runs, then uvicorn imports it)."""
    global _global_done
    if _global_done:
        return
    _global_done = True
    resource = Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", "zoo-api")})
    readers: list = []
    if prometheus:
        from opentelemetry.exporter.prometheus import PrometheusMetricReader
        from prometheus_client import start_http_server

        start_http_server(int(prometheus))
        readers.append(PrometheusMetricReader())
    if otlp:
        readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=15000))
    # durations are in seconds, so they need buckets in seconds rather than the SDK's millisecond defaults
    seconds = ExplicitBucketHistogramAggregation(DURATION_BUCKETS)
    metrics.set_meter_provider(
        MeterProvider(
            resource=resource,
            metric_readers=readers,
            views=[
                View(instrument_name="zoo.job.duration", aggregation=seconds),
                View(instrument_name="zoo.job.wait", aggregation=seconds),
                View(instrument_name="zoo.tool.duration", aggregation=seconds),
            ],
        )
    )
    if not otlp:
        return
    traces = TracerProvider(resource=resource)
    traces.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(traces)
    logs = LoggerProvider(resource=resource)
    logs.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(logs)
    handler = LoggingHandler(level=logging.INFO, logger_provider=logs)
    logger.addHandler(handler)
    logging.getLogger("uvicorn.access").addHandler(handler)
    PsycopgInstrumentor().instrument()
