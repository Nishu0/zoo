package main

import (
	"context"
	"os"
	"time"
)

const metricsEvery = 10 * time.Second

// reportMetrics pushes the sandbox's resource usage until ctx ends. The names match the monitoring API's, and the
// values follow docker stats: CPU time as a percent of one core, memory without reclaimable cache. The guest also
// counts each push as its heartbeat (the API restarts a macOS or Windows VM that goes quiet and stops drawing).
func reportMetrics(ctx context.Context, send func(any, []byte) error) {
	ticker := time.NewTicker(metricsEvery)
	defer ticker.Stop()
	cpu, at := cpuUsec(), time.Now()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
		nextCPU, now := cpuUsec(), time.Now()
		send(map[string]any{"op": "metrics", "data": collect(nextCPU-cpu, now.Sub(at))}, nil)
		cpu, at = nextCPU, now
	}
}

func collect(cpu uint64, elapsed time.Duration) map[string]any {
	data := map[string]any{"cpu_percent": 0.0, "memory_percent": 0.0}
	if elapsed > 0 {
		data["cpu_percent"] = float64(cpu) / float64(elapsed.Microseconds()) * 100
	}
	used, limit := memory()
	data["memory_usage"], data["memory_limit"] = used, limit
	if limit > 0 {
		data["memory_percent"] = float64(used) / float64(limit) * 100
	}
	data["network_rx"], data["network_tx"] = network()
	data["pids"] = pids()
	if home, err := os.UserHomeDir(); err == nil {
		if total, used, ok := disk(home); ok {
			data["disk_total"], data["disk_used"] = total, used
		}
	}
	return data
}
