package main

import (
	"os"
	"strconv"
	"strings"

	"golang.org/x/sys/unix"
)

func hostStats() stats {
	var s stats
	if data, err := os.ReadFile("/proc/meminfo"); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			fields := strings.Fields(line)
			if len(fields) < 2 {
				continue
			}
			value, _ := strconv.ParseUint(fields[1], 10, 64)
			switch fields[0] {
			case "MemTotal:":
				s.memoryTotal = value * 1024
			case "MemAvailable:":
				s.memoryAvailable = value * 1024
			}
		}
	}
	if data, err := os.ReadFile("/proc/loadavg"); err == nil {
		if fields := strings.Fields(string(data)); len(fields) > 0 {
			s.load, _ = strconv.ParseFloat(fields[0], 64)
		}
	}
	var disk unix.Statfs_t
	if unix.Statfs(diskPath(), &disk) == nil {
		s.diskTotal = disk.Blocks * uint64(disk.Bsize)
		s.diskFree = disk.Bavail * uint64(disk.Bsize)
	}
	return s
}
