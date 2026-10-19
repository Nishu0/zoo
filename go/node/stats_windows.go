package main

import (
	"sync"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	kernel32             = windows.NewLazySystemDLL("kernel32.dll")
	globalMemoryStatusEx = kernel32.NewProc("GlobalMemoryStatusEx")
	getSystemTimes       = kernel32.NewProc("GetSystemTimes")
)

type memoryStatusEx struct {
	length               uint32
	memoryLoad           uint32
	totalPhys            uint64
	availPhys            uint64
	totalPageFile        uint64
	availPageFile        uint64
	totalVirtual         uint64
	availVirtual         uint64
	availExtendedVirtual uint64
}

// CPU use between two reports stands in for the load average Windows doesn't have.
var (
	cpuMu               sync.Mutex
	lastIdle, lastTotal uint64
)

func fileTime(ft windows.Filetime) uint64 {
	return uint64(ft.HighDateTime)<<32 | uint64(ft.LowDateTime)
}

func cpuUse() float64 {
	var idle, kernel, user windows.Filetime
	if r, _, _ := getSystemTimes.Call(uintptr(unsafe.Pointer(&idle)), uintptr(unsafe.Pointer(&kernel)), uintptr(unsafe.Pointer(&user))); r == 0 {
		return 0
	}
	// kernel time includes idle time
	i, total := fileTime(idle), fileTime(kernel)+fileTime(user)
	cpuMu.Lock()
	defer cpuMu.Unlock()
	di, dt := i-lastIdle, total-lastTotal
	first := lastTotal == 0
	lastIdle, lastTotal = i, total
	if first || dt == 0 {
		return 0
	}
	return 1 - float64(di)/float64(dt)
}

func hostStats() stats {
	var s stats
	m := memoryStatusEx{length: uint32(unsafe.Sizeof(memoryStatusEx{}))}
	if r, _, _ := globalMemoryStatusEx.Call(uintptr(unsafe.Pointer(&m))); r != 0 {
		s.memoryTotal, s.memoryAvailable = m.totalPhys, m.availPhys
	}
	s.load = cpuUse()
	path, err := windows.UTF16PtrFromString(diskPath())
	if err == nil {
		var free, total, totalFree uint64
		if windows.GetDiskFreeSpaceEx(path, &free, &total, &totalFree) == nil {
			s.diskTotal, s.diskFree = total, free
		}
	}
	return s
}
