package main

import (
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"unsafe"

	"github.com/ebitengine/purego"
	"golang.org/x/sys/unix"
)

// The VM is the sandbox's own machine, so its metrics are the whole machine's, read from the Mach host statistics
// through purego (the darwin build has no cgo).

const (
	hostCPULoadInfo = 3 // HOST_CPU_LOAD_INFO: user, system, idle and nice ticks summed over every CPU
	hostVMInfo64    = 4 // HOST_VM_INFO64: struct vm_statistics64, 38 natural_t long
	vmInfo64Count   = 38
	ticksPerSecond  = 100
)

var (
	machOnce       sync.Once
	machHost       uint32
	hostStatistics func(uint32, int32, *int32, *uint32) int32
	hostStats64    func(uint32, int32, *int32, *uint32) int32
)

func mach() bool {
	machOnce.Do(func() {
		lib, err := purego.Dlopen("/usr/lib/libSystem.B.dylib", purego.RTLD_NOW|purego.RTLD_GLOBAL)
		if err != nil {
			return
		}
		var self func() uint32
		purego.RegisterLibFunc(&self, lib, "mach_host_self")
		purego.RegisterLibFunc(&hostStatistics, lib, "host_statistics")
		purego.RegisterLibFunc(&hostStats64, lib, "host_statistics64")
		machHost = self()
	})
	return machHost != 0
}

func cpuUsec() uint64 {
	var ticks [4]int32
	count := uint32(len(ticks))
	if !mach() || hostStatistics(machHost, hostCPULoadInfo, &ticks[0], &count) != 0 {
		return 0
	}
	busy := uint64(uint32(ticks[0])) + uint64(uint32(ticks[1])) + uint64(uint32(ticks[3]))
	return busy * 1_000_000 / ticksPerSecond
}

// memory is what Activity Monitor calls Memory Used: app memory (internal minus purgeable pages), wired and
// compressed.
func memory() (used, limit uint64) {
	limit, _ = unix.SysctlUint64("hw.memsize")
	var info [vmInfo64Count]int32
	count := uint32(vmInfo64Count)
	if !mach() || hostStats64(machHost, hostVMInfo64, &info[0], &count) != 0 {
		return 0, limit
	}
	field := func(offset uintptr) uint64 {
		return uint64(*(*uint32)(unsafe.Add(unsafe.Pointer(&info[0]), offset)))
	}
	// byte offsets in vm_statistics64: wire_count 12, purgeable_count 88, compressor_page_count 128,
	// internal_page_count 140
	pages := field(140) - min(field(140), field(88)) + field(12) + field(128)
	return pages * uint64(unix.Getpagesize()), limit
}

func pids() uint64 {
	procs, err := unix.SysctlKinfoProcSlice("kern.proc.all")
	if err != nil {
		return 0
	}
	return uint64(len(procs))
}

// network sums every interface but loopback from netstat's link rows, whose byte columns are counted from the right
// because interfaces without an address print one column fewer.
func network() (rx, tx uint64) {
	out, err := exec.Command("/usr/sbin/netstat", "-ibn").Output()
	if err != nil {
		return 0, 0
	}
	for line := range strings.Lines(string(out)) {
		f := strings.Fields(line)
		if len(f) < 8 || f[0] == "lo0" || !strings.HasPrefix(f[2], "<Link#") {
			continue
		}
		r, _ := strconv.ParseUint(f[len(f)-5], 10, 64)
		t, _ := strconv.ParseUint(f[len(f)-2], 10, 64)
		rx, tx = rx+r, tx+t
	}
	return rx, tx
}
