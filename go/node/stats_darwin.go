package main

import (
	"context"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"

	"golang.org/x/sys/unix"
)

func hostStats() stats {
	var s stats
	s.memoryTotal, _ = unix.SysctlUint64("hw.memsize")
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if out, err := output(ctx, "/usr/bin/vm_stat"); err == nil {
		s.memoryAvailable = parseVMStat(string(out))
	}
	if out, err := output(ctx, "/usr/sbin/sysctl", "-n", "vm.loadavg"); err == nil {
		if fields := strings.Fields(strings.Trim(strings.TrimSpace(string(out)), "{}")); len(fields) > 0 {
			if load, err := strconv.ParseFloat(fields[0], 64); err == nil {
				s.load = load / float64(runtime.NumCPU())
			}
		}
	}
	var fs unix.Statfs_t
	if unix.Statfs(diskPath(), &fs) == nil {
		s.diskTotal = fs.Blocks * uint64(fs.Bsize)
		s.diskFree = fs.Bavail * uint64(fs.Bsize)
	}
	return s
}

var vmStatLine = regexp.MustCompile(`^(.+):\s+(\d+)\.?$`)
var pageSize = regexp.MustCompile(`page size of (\d+) bytes`)

// parseVMStat estimates available memory like Activity Monitor: free, inactive, speculative and purgeable pages.
func parseVMStat(out string) uint64 {
	size := uint64(16384)
	if m := pageSize.FindStringSubmatch(out); m != nil {
		size, _ = strconv.ParseUint(m[1], 10, 64)
	}
	var pages uint64
	for _, line := range strings.Split(out, "\n") {
		m := vmStatLine.FindStringSubmatch(strings.TrimSpace(line))
		if m == nil {
			continue
		}
		switch m[1] {
		case "Pages free", "Pages inactive", "Pages speculative", "Pages purgeable":
			n, _ := strconv.ParseUint(m[2], 10, 64)
			pages += n
		}
	}
	return pages * size
}
