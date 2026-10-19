package main

import (
	"os"
	"testing"
	"time"
)

func TestMetricsReadTheWholeMachine(t *testing.T) {
	cpu := cpuUsec()
	time.Sleep(200 * time.Millisecond)
	if cpuUsec() <= cpu {
		t.Fatalf("busy CPU time didn't grow: %d", cpu)
	}
	used, limit := memory()
	if limit == 0 || used == 0 || used > limit {
		t.Fatalf("memory %d of %d", used, limit)
	}
	if pids() < 10 {
		t.Fatalf("only %d processes", pids())
	}
	home, _ := os.UserHomeDir()
	if total, used, ok := disk(home); !ok || total == 0 || used > total {
		t.Fatalf("disk %d of %d (%v)", used, total, ok)
	}
	data := collect(1000, time.Second)
	for _, key := range []string{"cpu_percent", "memory_percent", "network_rx", "network_tx", "disk_total"} {
		if _, ok := data[key]; !ok {
			t.Fatalf("no %s in %v", key, data)
		}
	}
}
