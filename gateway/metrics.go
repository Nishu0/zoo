package main

import (
	"net/http"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

type gatewayMetrics struct {
	registry     *prometheus.Registry
	sessions     *prometheus.GaugeVec
	reconnect    prometheus.Histogram
	screenEncode prometheus.Histogram
}

func newGatewayMetrics() *gatewayMetrics {
	metrics := &gatewayMetrics{
		registry: prometheus.NewRegistry(),
		sessions: prometheus.NewGaugeVec(prometheus.GaugeOpts{
			Name: "zoo_gateway_sessions",
			Help: "Current active guest, node, and VNC sessions held by this gateway.",
		}, []string{"type"}),
		reconnect: prometheus.NewHistogram(prometheus.HistogramOpts{
			Name:    "zoo_gateway_reconnect_seconds",
			Help:    "Time from loss of an established guest or node session until successful reconnection.",
			Buckets: []float64{0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 30, 60},
		}),
		screenEncode: prometheus.NewHistogram(prometheus.HistogramOpts{
			Name:    "zoo_screen_encode_seconds",
			Help:    "Time spent encoding framebuffer images, excluding cached image responses.",
			Buckets: []float64{0.001, 0.0025, 0.005, 0.01, 0.015, 0.02, 0.03, 0.05, 0.1, 0.25, 0.5, 1},
		}),
	}
	metrics.registry.MustRegister(metrics.sessions, metrics.reconnect, metrics.screenEncode)
	for _, kind := range []string{"guest", "node", "vnc"} {
		metrics.sessions.WithLabelValues(kind).Set(0)
	}
	return metrics
}

var nativeGatewayMetrics = newGatewayMetrics()

func gatewayMetricsHandler() http.Handler {
	return promhttp.HandlerFor(nativeGatewayMetrics.registry, promhttp.HandlerOpts{Timeout: 5 * time.Second, MaxRequestsInFlight: 2})
}

func gatewaySessionOpened(kind string) func() {
	switch kind {
	case "guest", "node", "vnc":
	default:
		panic("unsupported gateway session metric type: " + kind)
	}
	gauge := nativeGatewayMetrics.sessions.WithLabelValues(kind)
	gauge.Inc()
	var closed sync.Once
	return func() { closed.Do(gauge.Dec) }
}

func observeGatewayReconnect(elapsed time.Duration) {
	if elapsed < 0 {
		return
	}
	nativeGatewayMetrics.reconnect.Observe(elapsed.Seconds())
}

func observeScreenEncode(elapsed time.Duration) {
	if elapsed < 0 {
		return
	}
	nativeGatewayMetrics.screenEncode.Observe(elapsed.Seconds())
}
