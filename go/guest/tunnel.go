package main

import (
	"encoding/json"
	"errors"
	"net"
	"strconv"
	"sync"
	"time"
)

// A tunnel is a raw byte stream between the API and a port on the sandbox's loopback, such as x11vnc's 5900, so
// the API reaches it without the port being published.
type tunnel struct {
	conn  net.Conn
	input chan []byte
	done  chan struct{}
	once  sync.Once
}

var (
	tunnels   = map[string]*tunnel{}
	tunnelsMu sync.Mutex
)

type tunnelArgs struct {
	Stream string `json:"stream"`
	Port   int    `json:"port"`
}

func (t *tunnel) close() {
	t.once.Do(func() {
		close(t.done)
		t.conn.Close()
	})
}

// tunnelOpen dials the port and streams what it sends as tunnel_data frames, ending with one tunnel_close frame.
func tunnelOpen(c call) (any, []byte, error) {
	var a tunnelArgs
	if err := json.Unmarshal(c.args, &a); err != nil {
		return nil, nil, err
	}
	if a.Stream == "" || a.Port <= 0 || a.Port > 65535 {
		return nil, nil, errors.New("stream and a valid port are required")
	}
	conn, err := net.DialTimeout("tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(a.Port)), 5*time.Second)
	if err != nil {
		return nil, nil, err
	}
	t := &tunnel{conn: conn, input: make(chan []byte, 256), done: make(chan struct{})}
	tunnelsMu.Lock()
	if _, taken := tunnels[a.Stream]; taken {
		tunnelsMu.Unlock()
		conn.Close()
		return nil, nil, errors.New("stream already open")
	}
	tunnels[a.Stream] = t
	tunnelsMu.Unlock()

	go func() {
		for {
			select {
			case data := <-t.input:
				if _, err := conn.Write(data); err != nil {
					t.close()
					return
				}
			case <-t.done:
				return
			}
		}
	}()
	go func() {
		buf := make([]byte, 64<<10)
		for {
			n, err := conn.Read(buf)
			if n > 0 {
				if c.send(map[string]any{"op": "tunnel_data", "stream": a.Stream}, append([]byte(nil), buf[:n]...)) != nil {
					break
				}
			}
			if err != nil {
				break
			}
		}
		t.close()
		tunnelsMu.Lock()
		delete(tunnels, a.Stream)
		tunnelsMu.Unlock()
		c.send(map[string]any{"op": "tunnel_close", "stream": a.Stream}, nil)
	}()
	return map[string]any{"stream": a.Stream}, nil, nil
}

func findTunnel(raw json.RawMessage) (*tunnel, error) {
	var a tunnelArgs
	if err := json.Unmarshal(raw, &a); err != nil {
		return nil, err
	}
	tunnelsMu.Lock()
	defer tunnelsMu.Unlock()
	t, ok := tunnels[a.Stream]
	if !ok {
		return nil, errors.New("no such tunnel " + a.Stream)
	}
	return t, nil
}

func tunnelWrite(c call) (any, []byte, error) {
	t, err := findTunnel(c.args)
	if err != nil {
		return nil, nil, err
	}
	select {
	case t.input <- c.payload:
	case <-t.done:
	}
	return nil, nil, nil
}

func tunnelClose(c call) (any, []byte, error) {
	t, err := findTunnel(c.args)
	if err != nil {
		return nil, nil, err
	}
	t.close()
	return nil, nil, nil
}

// closeTunnels drops every tunnel when the API connection drops; nobody can read them any more.
func closeTunnels() {
	tunnelsMu.Lock()
	defer tunnelsMu.Unlock()
	for _, t := range tunnels {
		t.close()
	}
}
