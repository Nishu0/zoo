//go:build !windows

package main

import (
	"errors"
	"os"
	"os/exec"
	"syscall"

	"github.com/creack/pty"
)

type unixConsole struct {
	*os.File
	cmd *exec.Cmd
}

func loginShell() []string {
	for _, shell := range []string{os.Getenv("SHELL"), "/bin/bash", "/bin/sh"} {
		if shell != "" {
			if _, err := os.Stat(shell); err == nil {
				return []string{shell, "-l"}
			}
		}
	}
	return []string{"/bin/sh", "-l"}
}

func startConsole(argv, env []string, cwd string, cols, rows uint16) (console, error) {
	cmd := exec.Command(argv[0], argv[1:]...)
	cmd.Dir = cwd
	cmd.Env = env
	f, err := pty.StartWithSize(cmd, &pty.Winsize{Cols: cols, Rows: rows})
	if err != nil {
		return nil, err
	}
	return &unixConsole{f, cmd}, nil
}

func (u *unixConsole) resize(cols, rows uint16) error {
	return pty.Setsize(u.File, &pty.Winsize{Cols: cols, Rows: rows})
}

func (u *unixConsole) signal(sig int) error {
	if sig <= 0 {
		sig = int(syscall.SIGHUP)
	}
	return syscall.Kill(-u.cmd.Process.Pid, syscall.Signal(sig))
}

func (u *unixConsole) wait() int {
	if err := u.cmd.Wait(); err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			return exitCode(exit)
		}
		return -1
	}
	return 0
}

func (u *unixConsole) close() { u.File.Close() }

func (u *unixConsole) pid() int { return u.cmd.Process.Pid }
