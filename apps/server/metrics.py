"""OpenTelemetry metrics for the default Grafana dashboard (deploy/grafana). Without OTEL_EXPORTER_OTLP_ENDPOINT
the meter is a no-op, so recording costs next to nothing.

Prometheus names, as the dashboard queries them: zoo_sandboxes, zoo_job_duration_seconds, zoo_job_wait_seconds,
zoo_tool_duration_seconds, zoo_errors_total, zoo_sandbox_ready_seconds, zoo_sandbox_creates_total,
zoo_event_delivery_seconds, zoo_pool_available, zoo_pool_refill_seconds.
"""

from collections.abc import Iterable

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation

from db.connection import db_manager

meter = metrics.get_meter("zoo")

DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600)

job_duration = meter.create_histogram(
    "zoo.job.duration", unit="s", description="Time a lifecycle job attempt ran, by kind and outcome"
)
job_wait = meter.create_histogram(
    "zoo.job.wait", unit="s", description="Time a lifecycle job waited in the queue before it ran, by kind"
)
tool_duration = meter.create_histogram(
    "zoo.tool.duration", unit="s", description="Tool call latency, by tool, channel and outcome"
)
errors = meter.create_counter("zoo.errors", unit="{error}", description="Errors, by source")
sandbox_ready = meter.create_histogram(
    "zoo.sandbox.ready", unit="s", description="Time from a create to a running sandbox, by kind and warm"
)
sandbox_creates = meter.create_counter(
    "zoo.sandbox.creates", unit="{sandbox}", description="Sandbox creates, by kind, warm and outcome"
)
event_delivery = meter.create_histogram(
    "zoo.event.delivery", unit="s", description="Time from an event's commit to its delivery, by channel"
)
pool_refill = meter.create_histogram(
    "zoo.pool.refill", unit="s", description="Time a pooled sandbox took to boot until it was ready, by kind"
)


def _sandboxes(options: CallbackOptions) -> Iterable[Observation]:
    try:
        with db_manager.session() as db:
            rows = list(db.count_sandboxes_by_state())
    except Exception:
        return []
    return [Observation(row.count, {"status": row.status, "kind": row.kind}) for row in rows]


meter.create_observable_gauge(
    "zoo.sandboxes", callbacks=[_sandboxes], unit="{sandbox}", description="Sandboxes by status and kind"
)


def _pool(options: CallbackOptions) -> Iterable[Observation]:
    try:
        with db_manager.session() as db:
            rows = [row for row in db.list_pool_sandboxes() if row.status == "idle"]
    except Exception:
        return []
    available: dict[str, int] = {}
    for row in rows:
        available[row.kind] = available.get(row.kind, 0) + 1
    return [Observation(count, {"kind": kind}) for kind, count in available.items()]


meter.create_observable_gauge(
    "zoo.pool.available", callbacks=[_pool], unit="{sandbox}", description="Ready pooled sandboxes, by kind"
)


def error(source: str, **attributes: str):
    errors.add(1, {"source": source, **attributes})
