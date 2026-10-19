package main

import (
	"encoding/json"
	"errors"
	"io"
	"os"
	"sync"
)

// A console is a running command on a pseudo-terminal: a Unix pty, or a ConPTY on Windows.
type console interface {
	io.ReadWriter
	resize(cols, rows uint16) error
	// signal sends sig to the command's process group; 0 hangs it up, as closing a terminal window does
	signal(sig int) error
	// wait returns the exit code once output has ended
	wait() int
	close()
	pid() int
}

type terminal struct {
	con   console
	input chan []byte
	done  chan struct{}
}

var (
	terminals   = map[string]*terminal{}
	terminalsMu sync.Mutex
)

type ptyArgs struct {
	Stream string            `json:"stream"`
	Argv   []string          `json:"argv"`
	Env    map[string]string `json:"env"`
	Cwd    string            `json:"cwd"`
	Cols   uint16            `json:"cols"`
	Rows   uint16            `json:"rows"`
	Signal int               `json:"signal"`
}

// ptyOpen starts a command on a new pseudo-terminal. The API names the stream, so output that arrives before the
// reply already has somewhere to go. Output streams as pty_data frames and ends with one pty_exit frame.
func ptyOpen(c call) (any, []byte, error) {
	a := ptyArgs{Cols: 80, Rows: 24}
	if err := json.Unmarshal(c.args, &a); err != nil {
		return nil, nil, err
	}
	if a.Stream == "" {
		return nil, nil, errors.New("stream is required")
	}
	if len(a.Argv) == 0 {
		a.Argv = loginShell()
	}
	env := append(append(os.Environ(), secretEnv()...), "TERM=xterm-256color")
	for k, v := range a.Env {
		env = append(env, k+"="+v)
	}
	con, err := startConsole(a.Argv, env, a.Cwd, a.Cols, a.Rows)
	if err != nil {
		return nil, nil, err
	}
	t := &terminal{con: con, input: make(chan []byte, 256), done: make(chan struct{})}
	terminalsMu.Lock()
	if _, taken := terminals[a.Stream]; taken {
		terminalsMu.Unlock()
		con.signal(9)
		con.close()
		con.wait()
		return nil, nil, errors.New("stream already open")
	}
	terminals[a.Stream] = t
	terminalsMu.Unlock()

	go func() {
		for {
			select {
			case data := <-t.input:
				if _, err := con.Write(data); err != nil {
					return
				}
			case <-t.done:
				return
			}
		}
	}()
	go func() {
		buf := make([]byte, 32<<10)
		for {
			n, err := con.Read(buf)
			if n > 0 {
				c.send(map[string]any{"op": "pty_data", "stream": a.Stream}, append([]byte(nil), buf[:n]...))
			}
			if err != nil {
				break
			}
		}
		code := con.wait()
		terminalsMu.Lock()
		delete(terminals, a.Stream)
		terminalsMu.Unlock()
		close(t.done)
		con.close()
		c.send(map[string]any{"op": "pty_exit", "stream": a.Stream, "exit_code": code}, nil)
	}()
	return map[string]any{"stream": a.Stream, "pid": con.pid()}, nil, nil
}

func openTerminal(raw json.RawMessage) (*terminal, ptyArgs, error) {
	var a ptyArgs
	if err := json.Unmarshal(raw, &a); err != nil {
		return nil, a, err
	}
	terminalsMu.Lock()
	defer terminalsMu.Unlock()
	t, ok := terminals[a.Stream]
	if !ok {
		return nil, a, errors.New("no such terminal " + a.Stream)
	}
	return t, a, nil
}

func ptyInput(c call) (any, []byte, error) {
	t, _, err := openTerminal(c.args)
	if err != nil {
		return nil, nil, err
	}
	select {
	case t.input <- c.payload:
	case <-t.done:
	}
	return nil, nil, nil
}

func ptyResize(c call) (any, []byte, error) {
	t, a, err := openTerminal(c.args)
	if err != nil {
		return nil, nil, err
	}
	return nil, nil, t.con.resize(a.Cols, a.Rows)
}

// ptyKill signals the terminal's process group (a hangup unless told otherwise), as closing a terminal window does.
func ptyKill(c call) (any, []byte, error) {
	t, a, err := openTerminal(c.args)
	if err != nil {
		return nil, nil, err
	}
	return nil, nil, t.con.signal(a.Signal)
}

// closeTerminals hangs up every terminal when the API connection drops; nobody can reach them any more.
func closeTerminals() {
	terminalsMu.Lock()
	defer terminalsMu.Unlock()
	for _, t := range terminals {
		t.con.signal(0)
	}
}
