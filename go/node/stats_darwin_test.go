package main

import "testing"

func TestParseVMStat(t *testing.T) {
	out := `Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               10000.
Pages active:                            200000.
Pages inactive:                           30000.
Pages speculative:                         5000.
Pages wired down:                         90000.
Pages purgeable:                           1000.
`
	if got := parseVMStat(out); got != 46000*16384 {
		t.Fatal(got)
	}
}
