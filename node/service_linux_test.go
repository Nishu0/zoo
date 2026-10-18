package main

import (
	"strings"
	"testing"
)

func TestSystemdUnitQuotesPathsAndEscapesSpecifiers(t *testing.T) {
	unit := systemdUnit("/opt/Zoo host/100%$HOME/bin/zoo-node", "/opt/Zoo host/100%$HOME")
	for _, want := range []string{`ExecStart="/opt/Zoo host/100%%$$HOME/bin/zoo-node" run --dir "/opt/Zoo host/100%%$$HOME"`, "Restart=always", "WantedBy=multi-user.target"} {
		if !strings.Contains(unit, want) {
			t.Fatalf("missing %q in %s", want, unit)
		}
	}
}

func TestLinuxHostStats(t *testing.T) {
	got := hostStats()
	if got.memoryTotal == 0 || got.diskTotal == 0 || got.memoryAvailable > got.memoryTotal || got.diskFree > got.diskTotal {
		t.Fatalf("invalid Linux host capacity: %#v", got)
	}
}
