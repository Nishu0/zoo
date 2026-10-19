// zoo-guest runs inside a sandbox and serves the API's tool calls over one websocket it dials itself, so tool
// calls skip docker exec and screenshots skip a process fork.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"log"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"runtime"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/coder/websocket"
)

const version = 1

// A call is one request: its args, its payload, and the session's send and context for ops that stream.
type call struct {
	args    json.RawMessage
	payload []byte
	send    func(any, []byte) error
	ctx     context.Context
}

type handler func(c call) (any, []byte, error)

var handlers = map[string]handler{
	"exec":        execOp,
	"read":        readOp,
	"write":       writeOp,
	"pty_open":    ptyOpen,
	"tunnel_open": tunnelOpen,
}

// notifications carry no id and get no reply. They run in arrival order on the read loop, so a terminal's input
// is never reordered; each one only hands work to a terminal's own goroutine.
var notifications = map[string]handler{
	"pty_input":    ptyInput,
	"pty_resize":   ptyResize,
	"pty_kill":     ptyKill,
	"tunnel_write": tunnelWrite,
	"tunnel_close": tunnelClose,
}

func main() {
	as := flag.String("user", "", "when started as root, run as this user")
	envFile := flag.String("env", "", "read ZOO_GUEST_* from this file, waiting for the API to write it")
	logFile := flag.String("log", "", "append the log to this file instead of stderr")
	appsFile := flag.String("apps", "", "as root, enforce the app policy in this file (apps_linux.go)")
	flag.Parse()
	if *logFile != "" {
		if f, err := os.OpenFile(*logFile, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600); err == nil {
			log.SetOutput(f)
		}
	}
	if *appsFile != "" {
		runApps(*appsFile)
		return
	}
	env := map[string]string{}
	// commands the guest runs inherit its environment, which must not carry the token
	for _, name := range []string{"ZOO_GUEST_URL", "ZOO_GUEST_TOKEN", "ZOO_SANDBOX_ID"} {
		env[name] = os.Getenv(name)
		os.Unsetenv(name)
	}
	if *envFile == "" && !configured(env) {
		log.Fatal("ZOO_GUEST_URL, ZOO_GUEST_TOKEN and ZOO_SANDBOX_ID are required")
		return
	}
	if *as != "" && os.Getuid() == 0 {
		if err := dropTo(*as); err != nil {
			log.Fatalf("switching to %s: %v", *as, err)
		}
	}
	if home, err := os.UserHomeDir(); err == nil {
		os.Chdir(home)
	}
	backoff := 100 * time.Millisecond
	for {
		if *envFile != "" {
			// read again on every attempt: the API rewrites the file at each boot of a VM
			env = readEnv(*envFile)
		}
		began := time.Now()
		err := errors.New(*envFile + " is not written yet")
		if configured(env) {
			err = session(context.Background(), env["ZOO_GUEST_URL"], env["ZOO_GUEST_TOKEN"], env["ZOO_SANDBOX_ID"])
			if ownerURL, valid := guestOwnerURL(env["ZOO_GUEST_URL"], err); valid {
				err = session(context.Background(), ownerURL, env["ZOO_GUEST_TOKEN"], env["ZOO_SANDBOX_ID"])
			}
		}
		if time.Since(began) > 30*time.Second {
			backoff = 100 * time.Millisecond
		}
		log.Printf("disconnected: %v; retrying in %s", err, backoff)
		time.Sleep(backoff)
		backoff = min(backoff*2, time.Second)
	}
}

func configured(env map[string]string) bool {
	return env["ZOO_GUEST_URL"] != "" && env["ZOO_GUEST_TOKEN"] != "" && env["ZOO_SANDBOX_ID"] != ""
}

func guestOwnerURL(address string, failure error) (string, bool) {
	var closed websocket.CloseError
	if !errors.As(failure, &closed) || closed.Code != websocket.StatusCode(4001) || !strings.HasPrefix(closed.Reason, "owner=") {
		return "", false
	}
	owner, err := netip.ParseAddr(strings.TrimPrefix(closed.Reason, "owner="))
	if err != nil || owner.Zone() != "" {
		return "", false
	}
	endpoint, err := url.Parse(address)
	if err != nil || endpoint.Hostname() == "" || endpoint.User != nil || endpoint.Scheme != "ws" && endpoint.Scheme != "wss" {
		return "", false
	}
	query := endpoint.Query()
	query.Set("owner", owner.Unmap().String())
	endpoint.RawQuery = query.Encode()
	return endpoint.String(), true
}

// readEnv parses KEY=VALUE lines; a missing file reads as empty.
func readEnv(path string) map[string]string {
	env := map[string]string{}
	f, err := os.Open(path)
	if err != nil {
		return env
	}
	defer f.Close()
	lines := bufio.NewScanner(f)
	for lines.Scan() {
		if k, v, ok := strings.Cut(strings.TrimSpace(lines.Text()), "="); ok {
			env[k] = v
		}
	}
	return env
}

// session serves one connection until it drops.
func session(ctx context.Context, url, token, sandbox string) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	conn, _, err := websocket.Dial(ctx, url, &websocket.DialOptions{
		HTTPClient: &http.Client{Timeout: 3 * time.Second},
		HTTPHeader: http.Header{"Authorization": {"Bearer " + token}, "X-Zoo-Sandbox": {sandbox}},
	})
	if err != nil {
		return err
	}
	defer conn.CloseNow()
	conn.SetReadLimit(256 << 20)
	send := func(header any, payload []byte) error {
		frame, err := pack(header, payload)
		if err != nil {
			return err
		}
		wctx, done := context.WithTimeout(ctx, 30*time.Second)
		defer done()
		return conn.Write(wctx, websocket.MessageBinary, frame)
	}
	hello := map[string]any{"op": "hello", "version": version, "os": runtime.GOOS, "services": services}
	if err := send(hello, nil); err != nil {
		return err
	}
	if slices.Contains(services, "metrics") {
		go reportMetrics(ctx, send)
	}
	defer closeTerminals()
	defer closeTunnels()
	for {
		_, data, err := conn.Read(ctx)
		if err != nil {
			return err
		}
		raw, payload, err := unpack(data)
		var req request
		if err == nil {
			err = json.Unmarshal(raw, &req)
		}
		if err != nil {
			log.Printf("bad frame: %v", err)
			continue
		}
		c := call{args: req.Args, payload: payload, send: send, ctx: ctx}
		if req.ID == 0 {
			if _, _, err := dispatch(notifications, req.Op, c); err != nil {
				log.Printf("%s: %v", req.Op, err)
			}
			continue
		}
		go serve(req, c)
	}
}

type request struct {
	ID   uint64          `json:"id"`
	Op   string          `json:"op"`
	Args json.RawMessage `json:"args"`
}

func serve(req request, c call) {
	result, out, err := dispatch(handlers, req.Op, c)
	if err != nil {
		c.send(map[string]any{"id": req.ID, "ok": false, "error": err.Error()}, nil)
		return
	}
	if err := c.send(map[string]any{"id": req.ID, "ok": true, "result": result}, out); err != nil {
		log.Printf("reply %d: %v", req.ID, err)
	}
}

func dispatch(ops map[string]handler, op string, c call) (result any, out []byte, err error) {
	defer func() {
		if r := recover(); r != nil {
			err = errors.New("guest panic: " + strconv.Quote(toString(r)))
		}
	}()
	h, ok := ops[op]
	if !ok {
		return nil, nil, errors.New("unknown op " + op)
	}
	if len(c.args) == 0 {
		c.args = json.RawMessage("{}")
	}
	return h(c)
}

func toString(v any) string {
	if err, ok := v.(error); ok {
		return err.Error()
	}
	if s, ok := v.(string); ok {
		return s
	}
	return "unknown"
}
