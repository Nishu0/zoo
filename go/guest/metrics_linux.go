package main

import (
	"bufio"
	"os"
	"strconv"
	"strings"
)

const cgroup = "/sys/fs/cgroup/"

// cpuUsec is the CPU time used so far: the cgroup's when there is one, else the whole machine's busy time (a Kata
// VM is the sandbox's own machine).
func cpuUsec() uint64 {
	if stat := keyed(cgroup + "cpu.stat"); stat != nil {
		return stat["usage_usec"]
	}
	line, _ := firstLine("/proc/stat")
	fields := strings.Fields(line)
	var busy uint64
	for i, f := range fields[min(1, len(fields)):] {
		n, _ := strconv.ParseUint(f, 10, 64)
		if i != 3 && i != 4 { // idle, iowait
			busy += n
		}
	}
	return busy * 1_000_000 / 100 // USER_HZ
}

func memory() (used, limit uint64) {
	info := meminfo()
	if current, err := readUint(cgroup + "memory.current"); err == nil {
		used = current - min(current, keyed(cgroup + "memory.stat")["inactive_file"])
		if limit, err = readUint(cgroup + "memory.max"); err != nil {
			limit = info["MemTotal"]
		}
		return used, limit
	}
	return info["MemTotal"] - info["MemAvailable"], info["MemTotal"]
}

func pids() uint64 {
	if n, err := readUint(cgroup + "pids.current"); err == nil {
		return n
	}
	entries, _ := os.ReadDir("/proc")
	var n uint64
	for _, e := range entries {
		if _, err := strconv.Atoi(e.Name()); err == nil {
			n++
		}
	}
	return n
}

// keyed reads a "name value" per line file such as cpu.stat; nil when it can't be read.
func keyed(path string) map[string]uint64 {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	values := map[string]uint64{}
	for line := range strings.Lines(string(data)) {
		if name, value, ok := strings.Cut(strings.TrimSpace(line), " "); ok {
			values[name], _ = strconv.ParseUint(value, 10, 64)
		}
	}
	return values
}

func meminfo() map[string]uint64 {
	values := map[string]uint64{}
	f, err := os.Open("/proc/meminfo")
	if err != nil {
		return values
	}
	defer f.Close()
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) >= 2 {
			n, _ := strconv.ParseUint(fields[1], 10, 64)
			values[strings.TrimSuffix(fields[0], ":")] = n * 1024
		}
	}
	return values
}

func network() (rx, tx uint64) {
	f, err := os.Open("/proc/net/dev")
	if err != nil {
		return 0, 0
	}
	defer f.Close()
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		name, stats, ok := strings.Cut(scanner.Text(), ":")
		fields := strings.Fields(stats)
		if !ok || strings.TrimSpace(name) == "lo" || len(fields) < 9 {
			continue
		}
		r, _ := strconv.ParseUint(fields[0], 10, 64)
		t, _ := strconv.ParseUint(fields[8], 10, 64)
		rx, tx = rx+r, tx+t
	}
	return rx, tx
}

func firstLine(path string) (string, error) {
	data, err := os.ReadFile(path)
	line, _, _ := strings.Cut(string(data), "\n")
	return line, err
}

func readUint(path string) (uint64, error) {
	line, err := firstLine(path)
	if err != nil {
		return 0, err
	}
	return strconv.ParseUint(strings.TrimSpace(line), 10, 64)
}
