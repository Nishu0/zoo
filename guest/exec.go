package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"syscall"
	"time"
)

type execArgs struct {
	Argv []string          `json:"argv"`
	Env  map[string]string `json:"env"`
	Cwd  string            `json:"cwd"`
	// merge interleaves stderr into stdout, as docker exec does without demux
	Merge bool `json:"merge"`
}

// execOp runs a command to completion with the request's payload as its stdin. The reply's payload holds stdout
// then stderr; result.stdout_len splits them.
func execOp(c call) (any, []byte, error) {
	var a execArgs
	if err := json.Unmarshal(c.args, &a); err != nil {
		return nil, nil, err
	}
	if len(a.Argv) == 0 {
		return nil, nil, errors.New("argv is empty")
	}
	cmd := exec.Command(a.Argv[0], a.Argv[1:]...)
	cmd.Dir = a.Cwd
	hideWindow(cmd)
	cmd.Env = append(os.Environ(), secretEnv()...)
	for k, v := range a.Env {
		cmd.Env = append(cmd.Env, k+"="+v)
	}
	var stdout, stderr bytes.Buffer
	if len(c.payload) > 0 {
		cmd.Stdin = bytes.NewReader(c.payload)
	}
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if a.Merge {
		cmd.Stderr = &stdout
	}
	// a background child that keeps the output pipes open must not hold the reply forever
	cmd.WaitDelay = time.Second
	code := 0
	if err := cmd.Run(); err != nil {
		var exit *exec.ExitError
		switch {
		case errors.As(err, &exit):
			code = exitCode(exit)
		case errors.Is(err, exec.ErrWaitDelay):
		default:
			// like docker exec: a command that cannot start exits 127 with the reason on its output
			code = 127
			cmd.Stderr.(*bytes.Buffer).WriteString(err.Error() + "\n")
		}
	}
	n := stdout.Len()
	return map[string]any{"exit_code": code, "stdout_len": n}, append(stdout.Bytes(), stderr.Bytes()...), nil
}

func exitCode(exit *exec.ExitError) int {
	if ws, ok := exit.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
		return 128 + int(ws.Signal())
	}
	return exit.ExitCode()
}
