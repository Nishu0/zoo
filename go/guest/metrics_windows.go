package main

import (
	"unsafe"

	"golang.org/x/sys/windows"
)

// The VM is the sandbox's own machine, so its metrics are the whole machine's.

var (
	kernel32                 = windows.NewLazySystemDLL("kernel32.dll")
	procGetSystemTimes       = kernel32.NewProc("GetSystemTimes")
	procGlobalMemoryStatusEx = kernel32.NewProc("GlobalMemoryStatusEx")
)

// memoryStatusEx is MEMORYSTATUSEX.
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

func filetime(ft windows.Filetime) uint64 {
	return uint64(ft.HighDateTime)<<32 | uint64(ft.LowDateTime)
}

// cpuUsec is the busy CPU time summed over every CPU. GetSystemTimes counts in 100 ns units, and its kernel time
// includes idle time.
func cpuUsec() uint64 {
	var idle, kernel, user windows.Filetime
	r, _, _ := procGetSystemTimes.Call(
		uintptr(unsafe.Pointer(&idle)), uintptr(unsafe.Pointer(&kernel)), uintptr(unsafe.Pointer(&user)),
	)
	if r == 0 {
		return 0
	}
	return (filetime(kernel) - filetime(idle) + filetime(user)) / 10
}

// memory is what Task Manager calls In use: physical memory that isn't available.
func memory() (used, limit uint64) {
	status := memoryStatusEx{length: uint32(unsafe.Sizeof(memoryStatusEx{}))}
	if r, _, _ := procGlobalMemoryStatusEx.Call(uintptr(unsafe.Pointer(&status))); r == 0 {
		return 0, 0
	}
	return status.totalPhys - status.availPhys, status.totalPhys
}

func pids() uint64 {
	ids := make([]uint32, 4096)
	for {
		var n uint32
		if windows.EnumProcesses(ids, &n) != nil {
			return 0
		}
		if int(n)/4 < len(ids) {
			return uint64(n / 4)
		}
		ids = make([]uint32, len(ids)*2)
	}
}

const (
	hardwareInterface = 0x1 // InterfaceAndOperStatusFlags: a physical (or the VM's synthetic) adapter
	filterInterface   = 0x2 // a filter driver's view of another adapter, whose counters would count twice
)

// network sums the hardware adapters' counters; loopback, tunnels and filter layers are left out.
func network() (rx, tx uint64) {
	var table *windows.MibIfTable2
	if windows.GetIfTable2Ex(windows.MibIfTableNormal, &table) != nil {
		return 0, 0
	}
	defer windows.FreeMibTable(unsafe.Pointer(table))
	rows := unsafe.Slice(&table.Table[0], table.NumEntries)
	for i := range rows {
		flags := rows[i].InterfaceAndOperStatusFlags
		if flags&hardwareInterface == 0 || flags&filterInterface != 0 {
			continue
		}
		rx, tx = rx+rows[i].InOctets, tx+rows[i].OutOctets
	}
	return rx, tx
}

// disk is the size and use of the volume holding path.
func disk(path string) (total, used uint64, ok bool) {
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return 0, 0, false
	}
	var available, size, free uint64
	if windows.GetDiskFreeSpaceEx(name, &available, &size, &free) != nil {
		return 0, 0, false
	}
	return size, size - free, true
}
