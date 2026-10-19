package main

import (
	"errors"
	"os"
	"os/exec"
	"syscall"
	"time"
)

func init() {
	handlers["update"] = updateOp
}

// updateOp swaps in a new build that the host copied next to the running one (<exe>.new, through Hyper-V's
// Copy-VMFile) and restarts on it with the same arguments. Windows lets a running .exe be renamed but not
// overwritten, so the running one moves aside to <exe>.old first. The new process connects and replaces this one
// at the API; this one exits shortly after replying.
func updateOp(c call) (any, []byte, error) {
	exe, err := os.Executable()
	if err != nil {
		return nil, nil, err
	}
	next, old := exe+".new", exe+".old"
	if _, err := os.Stat(next); err != nil {
		return nil, nil, errors.New("no new build at " + next)
	}
	os.Remove(old)
	if err := os.Rename(exe, old); err != nil {
		return nil, nil, err
	}
	if err := os.Rename(next, exe); err != nil {
		os.Rename(old, exe)
		return nil, nil, err
	}
	cmd := exec.Command(exe, os.Args[1:]...)
	// DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: the new guest outlives this one
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x00000008 | 0x00000200}
	if err := cmd.Start(); err != nil {
		os.Rename(exe, next)
		os.Rename(old, exe)
		return nil, nil, err
	}
	go func() {
		time.Sleep(time.Second)
		os.Exit(0)
	}()
	return map[string]any{"pid": cmd.Process.Pid}, nil, nil
}
