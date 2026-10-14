package main

import (
	"os"
	"sync"
	"unsafe"

	"golang.org/x/sys/windows"
)

// conPTY is a command on a Windows pseudo console. Its output pipe stays open after the command exits, until the
// console is closed, so a goroutine closes it once the process ends and reads then see EOF.
type conPTY struct {
	in, out   *os.File
	hpc       windows.Handle
	process   windows.Handle
	processID int
	exited    chan struct{}
	code      int
	once      sync.Once
}

func loginShell() []string {
	return []string{"powershell.exe", "-NoLogo"}
}

func startConsole(argv, env []string, cwd string, cols, rows uint16) (console, error) {
	var inRead, inWrite, outRead, outWrite windows.Handle
	if err := windows.CreatePipe(&inRead, &inWrite, nil, 0); err != nil {
		return nil, err
	}
	if err := windows.CreatePipe(&outRead, &outWrite, nil, 0); err != nil {
		windows.CloseHandle(inRead)
		windows.CloseHandle(inWrite)
		return nil, err
	}
	// the console holds its own references to its ends of the pipes
	defer windows.CloseHandle(inRead)
	defer windows.CloseHandle(outWrite)
	in := os.NewFile(uintptr(inWrite), "conpty-in")
	out := os.NewFile(uintptr(outRead), "conpty-out")
	var hpc windows.Handle
	if err := windows.CreatePseudoConsole(coord(cols, rows), inRead, outWrite, 0, &hpc); err != nil {
		in.Close()
		out.Close()
		return nil, err
	}
	process, pid, err := spawn(hpc, argv, env, cwd)
	if err != nil {
		windows.ClosePseudoConsole(hpc)
		in.Close()
		out.Close()
		return nil, err
	}
	c := &conPTY{in: in, out: out, hpc: hpc, process: process, processID: pid, exited: make(chan struct{})}
	go func() {
		windows.WaitForSingleObject(process, windows.INFINITE)
		var code uint32
		windows.GetExitCodeProcess(process, &code)
		c.code = int(code)
		windows.CloseHandle(process)
		close(c.exited)
		c.closeConsole()
	}()
	return c, nil
}

func spawn(hpc windows.Handle, argv, env []string, cwd string) (windows.Handle, int, error) {
	attrs, err := windows.NewProcThreadAttributeList(1)
	if err != nil {
		return 0, 0, err
	}
	defer attrs.Delete()
	// the attribute's value is the console handle itself, not a pointer to it
	value := *(*unsafe.Pointer)(unsafe.Pointer(&hpc))
	if err := attrs.Update(windows.PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, value, unsafe.Sizeof(hpc)); err != nil {
		return 0, 0, err
	}
	si := windows.StartupInfoEx{ProcThreadAttributeList: attrs.List()}
	si.Cb = uint32(unsafe.Sizeof(si))
	cmdline, err := windows.UTF16PtrFromString(windows.ComposeCommandLine(argv))
	if err != nil {
		return 0, 0, err
	}
	var dir *uint16
	if cwd != "" {
		if dir, err = windows.UTF16PtrFromString(cwd); err != nil {
			return 0, 0, err
		}
	}
	// NUL-separated entries ending in an empty one; UTF16FromString refuses a NUL, so encode entry by entry
	var block []uint16
	for _, e := range env {
		s, err := windows.UTF16FromString(e)
		if err != nil {
			return 0, 0, err
		}
		block = append(block, s...)
	}
	block = append(block, 0)
	if len(env) == 0 {
		block = append(block, 0)
	}
	var pi windows.ProcessInformation
	flags := uint32(windows.EXTENDED_STARTUPINFO_PRESENT | windows.CREATE_UNICODE_ENVIRONMENT)
	if err := windows.CreateProcess(nil, cmdline, nil, nil, false, flags, &block[0], dir, &si.StartupInfo, &pi); err != nil {
		return 0, 0, err
	}
	windows.CloseHandle(pi.Thread)
	return pi.Process, int(pi.ProcessId), nil
}

func coord(cols, rows uint16) windows.Coord {
	return windows.Coord{X: int16(cols), Y: int16(rows)}
}

func (c *conPTY) Read(p []byte) (int, error)  { return c.out.Read(p) }
func (c *conPTY) Write(p []byte) (int, error) { return c.in.Write(p) }

func (c *conPTY) resize(cols, rows uint16) error {
	return windows.ResizePseudoConsole(c.hpc, coord(cols, rows))
}

// signal ends the command: Windows has no process-group signals, and closing the console is its hangup.
func (c *conPTY) signal(sig int) error {
	if sig == 0 {
		c.closeConsole()
		return nil
	}
	select {
	case <-c.exited:
		return nil
	default:
	}
	return windows.TerminateProcess(c.process, 1)
}

func (c *conPTY) closeConsole() {
	c.once.Do(func() { windows.ClosePseudoConsole(c.hpc) })
}

func (c *conPTY) wait() int {
	<-c.exited
	return c.code
}

func (c *conPTY) close() {
	c.closeConsole()
	c.in.Close()
	c.out.Close()
}

func (c *conPTY) pid() int { return c.processID }
