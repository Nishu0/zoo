package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/coder/websocket"
)

type reply struct {
	ID     uint64         `json:"id"`
	OK     bool           `json:"ok"`
	Error  string         `json:"error"`
	Result map[string]any `json:"result"`
	Op     string         `json:"op"`
	Stream string         `json:"stream"`
	Exit   *int           `json:"exit_code"`
}

// api stands in for the API's /guest/connect: it checks the handshake and hands the test the connection.
func api(t *testing.T) (*websocket.Conn, context.Context) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	t.Cleanup(cancel)
	conns := make(chan *websocket.Conn, 1)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer tok" || r.Header.Get("X-Zoo-Sandbox") != "sb" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		c.SetReadLimit(1 << 20)
		conns <- c
		<-ctx.Done()
	}))
	t.Cleanup(srv.Close)
	go session(ctx, "ws"+strings.TrimPrefix(srv.URL, "http"), "tok", "sb")
	c := <-conns
	hello := read(t, ctx, c)
	if hello.Op != "hello" {
		t.Fatalf("expected hello, got %+v", hello)
	}
	return c, ctx
}

func read(t *testing.T, ctx context.Context, c *websocket.Conn) reply {
	t.Helper()
	r, _ := readFrame(t, ctx, c)
	return r
}

func readFrame(t *testing.T, ctx context.Context, c *websocket.Conn) (reply, []byte) {
	t.Helper()
	_, data, err := c.Read(ctx)
	if err != nil {
		t.Fatal(err)
	}
	raw, payload, err := unpack(data)
	if err != nil {
		t.Fatal(err)
	}
	var r reply
	if err := json.Unmarshal(raw, &r); err != nil {
		t.Fatal(err)
	}
	return r, payload
}

func ask(t *testing.T, ctx context.Context, c *websocket.Conn, id uint64, op string, args any, payload []byte) (reply, []byte) {
	t.Helper()
	a, _ := json.Marshal(args)
	frame, _ := pack(map[string]any{"id": id, "op": op, "args": json.RawMessage(a)}, payload)
	if err := c.Write(ctx, websocket.MessageBinary, frame); err != nil {
		t.Fatal(err)
	}
	return readFrame(t, ctx, c)
}

func TestExec(t *testing.T) {
	c, ctx := api(t)
	r, out := ask(t, ctx, c, 1, "exec", map[string]any{
		"argv": []string{"sh", "-c", "echo $GREETING; echo oops >&2; exit 3"},
		"env":  map[string]string{"GREETING": "hi"},
	}, nil)
	n := int(r.Result["stdout_len"].(float64))
	if !r.OK || r.Result["exit_code"].(float64) != 3 || string(out[:n]) != "hi\n" || string(out[n:]) != "oops\n" {
		t.Fatalf("got %+v %q", r, out)
	}
	r, out = ask(t, ctx, c, 2, "exec", map[string]any{"argv": []string{"no-such-binary"}, "merge": true}, nil)
	if r.Result["exit_code"].(float64) != 127 || !strings.Contains(string(out), "not found") {
		t.Fatalf("got %+v %q", r, out)
	}
}

func TestFiles(t *testing.T) {
	c, ctx := api(t)
	path := filepath.Join(t.TempDir(), "note.txt")
	if r, _ := ask(t, ctx, c, 1, "write", map[string]any{"path": path}, []byte("hello")); !r.OK {
		t.Fatalf("write: %+v", r)
	}
	if data, _ := os.ReadFile(path); string(data) != "hello" {
		t.Fatalf("file holds %q", data)
	}
	r, out := ask(t, ctx, c, 2, "read", map[string]any{"path": path}, nil)
	if !r.OK || string(out) != "hello" {
		t.Fatalf("read: %+v %q", r, out)
	}
	if r, _ := ask(t, ctx, c, 3, "read", map[string]any{"path": path + ".missing"}, nil); r.OK || r.Error == "" {
		t.Fatalf("expected an error, got %+v", r)
	}
	if r, _ := ask(t, ctx, c, 4, "nope", nil, nil); r.OK || !strings.Contains(r.Error, "unknown op") {
		t.Fatalf("expected unknown op, got %+v", r)
	}
}

func notify(t *testing.T, ctx context.Context, c *websocket.Conn, op string, args any, payload []byte) {
	t.Helper()
	a, _ := json.Marshal(args)
	frame, _ := pack(map[string]any{"op": op, "args": json.RawMessage(a)}, payload)
	if err := c.Write(ctx, websocket.MessageBinary, frame); err != nil {
		t.Fatal(err)
	}
}

func TestTerminal(t *testing.T) {
	c, ctx := api(t)
	r, _ := ask(t, ctx, c, 1, "pty_open", map[string]any{"stream": "s1", "argv": []string{"sh"}, "cols": 100, "rows": 30}, nil)
	if !r.OK {
		t.Fatalf("pty_open: %+v", r)
	}
	// input frames are applied in order: the size comes from the resize before the command runs
	notify(t, ctx, c, "pty_resize", map[string]any{"stream": "s1", "cols": 120, "rows": 40}, nil)
	for _, part := range []string{"stty size; ", "echo $((6*", "7)); ", "exit 7\n"} {
		notify(t, ctx, c, "pty_input", map[string]any{"stream": "s1"}, []byte(part))
	}
	var out strings.Builder
	for {
		r, payload := readFrame(t, ctx, c)
		if r.Stream != "s1" {
			continue
		}
		if r.Op == "pty_data" {
			out.Write(payload)
			continue
		}
		if r.Op != "pty_exit" || r.Exit == nil || *r.Exit != 7 {
			code := -1
			if r.Exit != nil {
				code = *r.Exit
			}
			t.Fatalf("expected pty_exit with code 7, got %s (exit %d) after output %q", r.Op, code, out.String())
		}
		break
	}
	if !strings.Contains(out.String(), "40 120") || !strings.Contains(out.String(), "\n42") {
		t.Fatalf("terminal output %q", out.String())
	}
	// the stream is gone once it exits
	if r, _ := ask(t, ctx, c, 2, "pty_open", map[string]any{"stream": "s1", "argv": []string{"true"}}, nil); !r.OK {
		t.Fatalf("reopening a finished stream: %+v", r)
	}
}
