package main

import (
	"context"
	"crypto/tls"
	"errors"
	"io"
	"log/slog"
	"math/rand/v2"
	"net"
	"os"
	"runtime"
	"slices"
	"sync"
	"time"

	"github.com/chann44/zoo/node/nodepb"
	"google.golang.org/grpc"
	"google.golang.org/grpc/backoff"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/keepalive"
)

var errUpdated = errors.New("updated")

const (
	minBackoff   = 250 * time.Millisecond
	maxBackoff   = time.Second
	renewBefore  = 30 * 24 * time.Hour
	tunnelBuffer = 64
	readChunk    = 64 * 1024
)

// Node holds one stream to each API endpoint. Every endpoint is a separate API process with its own view of the
// node, so each stream reports and serves tunnels on its own.
type Node struct {
	cfg     Config
	home    string
	log     *slog.Logger
	drivers []Driver
	targets map[string]target

	mu      sync.Mutex
	running map[string]bool // endpoints with a loop
	updated chan struct{}
	once    sync.Once
	// renewing is held while a certificate renewal is in flight, so only one stream asks
	renewing sync.Mutex
}

func newNode(cfg Config, home string, logger *slog.Logger) *Node {
	return &Node{
		cfg:     cfg,
		home:    home,
		log:     logger,
		drivers: hostDrivers(),
		targets: hostTargets(),
		running: map[string]bool{},
		updated: make(chan struct{}),
	}
}

func (n *Node) run(ctx context.Context) error {
	removeStaleUpdates()
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	var wg sync.WaitGroup
	n.mu.Lock()
	for _, endpoint := range n.cfg.Endpoints {
		n.startLoop(ctx, &wg, endpoint)
	}
	n.mu.Unlock()
	select {
	case <-ctx.Done():
		wg.Wait()
		return nil
	case <-n.updated:
		cancel()
		wg.Wait()
		return errUpdated
	}
}

// startLoop needs n.mu held.
func (n *Node) startLoop(ctx context.Context, wg *sync.WaitGroup, endpoint string) {
	if n.running[endpoint] {
		return
	}
	n.running[endpoint] = true
	wg.Add(1)
	go func() {
		defer wg.Done()
		n.loop(ctx, wg, endpoint)
	}()
}

// loop keeps a stream to one endpoint, reconnecting with backoff, until ctx ends or the endpoint is dropped.
func (n *Node) loop(ctx context.Context, wg *sync.WaitGroup, endpoint string) {
	backoff := minBackoff
	for ctx.Err() == nil {
		n.mu.Lock()
		wanted := slices.Contains(n.cfg.Endpoints, endpoint)
		if !wanted {
			delete(n.running, endpoint)
		}
		n.mu.Unlock()
		if !wanted {
			return
		}
		started := time.Now()
		err := n.connect(ctx, wg, endpoint)
		if ctx.Err() != nil {
			return
		}
		if time.Since(started) > time.Minute {
			backoff = minBackoff
		}
		n.log.Warn("lost the API", "endpoint", endpoint, "error", err, "retry_in", backoff)
		jitter := time.Duration(rand.Int64N(int64(backoff / 2)))
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff + jitter):
		}
		backoff = min(backoff*2, maxBackoff)
	}
}

func (n *Node) dial(endpoint string) (*grpc.ClientConn, error) {
	pair, pool, err := loadCredentials(n.home)
	if err != nil {
		return nil, err
	}
	host, _, err := net.SplitHostPort(endpoint)
	if err != nil {
		return nil, err
	}
	creds := credentials.NewTLS(&tls.Config{
		Certificates: []tls.Certificate{pair},
		RootCAs:      pool,
		ServerName:   host,
		MinVersion:   tls.VersionTLS13,
	})
	return grpc.NewClient(endpoint,
		grpc.WithTransportCredentials(creds),
		grpc.WithConnectParams(grpc.ConnectParams{
			Backoff: backoff.Config{
				BaseDelay:  100 * time.Millisecond,
				Multiplier: 1.5,
				Jitter:     0.2,
				MaxDelay:   time.Second,
			},
			MinConnectTimeout: time.Second,
		}),
		grpc.WithKeepaliveParams(keepalive.ClientParameters{Time: 30 * time.Second, Timeout: 20 * time.Second}),
	)
}

// session is one Connect stream.
type session struct {
	ctx     context.Context
	node    *Node
	out     chan *nodepb.NodeMessage
	mu      sync.Mutex
	tunnels map[uint64]*tunnel
	update  *pendingUpdate
	renewal *renewal
}

func (n *Node) connect(ctx context.Context, wg *sync.WaitGroup, endpoint string) error {
	conn, err := n.dial(endpoint)
	if err != nil {
		return err
	}
	defer conn.Close()
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	stream, err := nodepb.NewNodeClient(conn).Connect(ctx)
	if err != nil {
		return err
	}
	s := &session{ctx: ctx, node: n, out: make(chan *nodepb.NodeMessage, 256), tunnels: map[uint64]*tunnel{}}
	defer s.closeTunnels()
	sendErr := make(chan error, 1)
	go func() {
		for {
			select {
			case <-ctx.Done():
				sendErr <- ctx.Err()
				return
			case msg := <-s.out:
				if err := stream.Send(msg); err != nil {
					sendErr <- err
					cancel()
					return
				}
			}
		}
	}()
	s.send(&nodepb.NodeMessage{Body: &nodepb.NodeMessage_Hello{Hello: n.hello(ctx)}})
	recvErr := make(chan error, 1)
	go func() {
		for {
			msg, err := stream.Recv()
			if err != nil {
				recvErr <- err
				cancel()
				return
			}
			s.handle(ctx, wg, msg)
		}
	}()
	select {
	case err := <-recvErr:
		if errors.Is(err, io.EOF) {
			return errors.New("the API closed the stream")
		}
		return err
	case err := <-sendErr:
		return err
	}
}

func (s *session) send(msg *nodepb.NodeMessage) {
	select {
	case s.out <- msg:
	case <-s.ctx.Done():
	}
}

func (n *Node) hello(ctx context.Context) *nodepb.Hello {
	hostname, _ := os.Hostname()
	hello := &nodepb.Hello{Version: version, Os: runtime.GOOS, Arch: runtime.GOARCH, Hostname: hostname}
	for _, d := range n.drivers {
		ok, detail := d.Probe(ctx)
		hello.Drivers = append(hello.Drivers, &nodepb.Driver{Name: d.Name(), Available: ok, Detail: detail})
	}
	for name := range n.targets {
		hello.Targets = append(hello.Targets, name)
	}
	slices.Sort(hello.Targets)
	return hello
}

func (s *session) handle(ctx context.Context, wg *sync.WaitGroup, msg *nodepb.ApiMessage) {
	n := s.node
	switch body := msg.Body.(type) {
	case *nodepb.ApiMessage_Welcome:
		n.welcome(ctx, wg, body.Welcome)
		go s.report(ctx, time.Duration(max(body.Welcome.StatusSeconds, 5))*time.Second)
		if expiresWithin(n.home, renewBefore) {
			s.renew()
		}
	case *nodepb.ApiMessage_Open:
		s.open(ctx, body.Open)
	case *nodepb.ApiMessage_Data:
		s.mu.Lock()
		t := s.tunnels[body.Data.Id]
		s.mu.Unlock()
		if t != nil {
			select {
			case t.in <- body.Data.Data:
			case <-t.done:
			case <-ctx.Done():
			}
		}
	case *nodepb.ApiMessage_Close:
		s.closeTunnel(body.Close.Id)
	case *nodepb.ApiMessage_Update:
		s.receiveUpdate(body.Update)
	case *nodepb.ApiMessage_Renewal:
		s.renewed(body.Renewal)
	}
}

// welcome picks up the API's current endpoints: new ones get a stream, dropped ones end theirs.
func (n *Node) welcome(ctx context.Context, wg *sync.WaitGroup, w *nodepb.Welcome) {
	if len(w.Endpoints) == 0 {
		return
	}
	n.mu.Lock()
	defer n.mu.Unlock()
	if slices.Equal(n.cfg.Endpoints, w.Endpoints) {
		return
	}
	n.cfg.Endpoints = slices.Clone(w.Endpoints)
	if err := saveConfig(n.home, n.cfg); err != nil {
		n.log.Error("saving the new endpoints failed", "error", err)
	}
	for _, endpoint := range n.cfg.Endpoints {
		n.startLoop(ctx, wg, endpoint)
	}
}

func (s *session) report(ctx context.Context, every time.Duration) {
	ticker := time.NewTicker(every)
	defer ticker.Stop()
	for {
		status := s.node.status(ctx)
		select {
		case s.out <- &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Status{Status: status}}:
		case <-ctx.Done():
			return
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (n *Node) status(ctx context.Context) *nodepb.Status {
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	stats := hostStats()
	status := &nodepb.Status{
		Cpus:            uint32(runtime.NumCPU()),
		MemoryTotal:     stats.memoryTotal,
		MemoryAvailable: stats.memoryAvailable,
		DiskTotal:       stats.diskTotal,
		DiskFree:        stats.diskFree,
		Load:            stats.load,
	}
	seen := map[string]bool{}
	for _, d := range n.drivers {
		ok, detail := d.Probe(ctx)
		if !ok {
			continue
		}
		ids, err := d.Sandboxes(ctx)
		if err != nil {
			status.Checks = append(status.Checks, &nodepb.Check{Name: d.Name(), Ok: false, Detail: err.Error()})
			continue
		}
		status.Checks = append(status.Checks, &nodepb.Check{Name: d.Name(), Ok: true, Detail: detail})
		for _, id := range ids {
			if !seen[id] {
				seen[id] = true
				status.Sandboxes = append(status.Sandboxes, id)
			}
		}
	}
	for name, t := range n.targets {
		err := t.check(ctx)
		check := &nodepb.Check{Name: name, Ok: err == nil}
		if err != nil {
			check.Detail = err.Error()
		}
		status.Checks = append(status.Checks, check)
	}
	status.Checks = append(status.Checks, diskCheck(stats))
	slices.Sort(status.Sandboxes)
	slices.SortFunc(status.Checks, func(a, b *nodepb.Check) int {
		if a.Name < b.Name {
			return -1
		}
		if a.Name > b.Name {
			return 1
		}
		return 0
	})
	return status
}

// minimum free disk before the node reports itself unhealthy
const minDiskFree = 10 << 30

func diskCheck(stats stats) *nodepb.Check {
	if stats.diskTotal == 0 {
		return &nodepb.Check{Name: "disk", Ok: false, Detail: "couldn't read free disk space"}
	}
	if stats.diskFree < minDiskFree {
		return &nodepb.Check{Name: "disk", Ok: false, Detail: "less than 10 GB free"}
	}
	return &nodepb.Check{Name: "disk", Ok: true}
}

// Tunnels

type tunnel struct {
	in   chan []byte
	done chan struct{}
	once sync.Once
	conn net.Conn
	mu   sync.Mutex
}

func (t *tunnel) stop() {
	t.once.Do(func() {
		close(t.done)
		t.mu.Lock()
		if t.conn != nil {
			t.conn.Close()
		}
		t.mu.Unlock()
	})
}

func (s *session) open(ctx context.Context, open *nodepb.TunnelOpen) {
	tg, ok := s.node.targets[open.Target]
	t := &tunnel{in: make(chan []byte, tunnelBuffer), done: make(chan struct{})}
	if !ok {
		s.send(closeMessage(open.Id, "no such target here: "+open.Target))
		return
	}
	s.mu.Lock()
	s.tunnels[open.Id] = t
	s.mu.Unlock()
	go func() {
		conn, err := tg.dial(ctx)
		if err != nil {
			s.finish(open.Id, err.Error())
			return
		}
		t.mu.Lock()
		t.conn = conn
		t.mu.Unlock()
		select {
		case <-t.done:
			conn.Close()
			return
		default:
		}
		go s.forward(open.Id, t, conn)
		for {
			select {
			case data := <-t.in:
				if _, err := conn.Write(data); err != nil {
					s.finish(open.Id, err.Error())
					return
				}
			case <-t.done:
				return
			}
		}
	}()
}

// forward sends what the target writes to the API.
func (s *session) forward(id uint64, t *tunnel, conn net.Conn) {
	buf := make([]byte, readChunk)
	for {
		count, err := conn.Read(buf)
		if count > 0 {
			data := make([]byte, count)
			copy(data, buf[:count])
			select {
			case s.out <- &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Data{Data: &nodepb.TunnelData{Id: id, Data: data}}}:
			case <-t.done:
				return
			}
		}
		if err != nil {
			reason := ""
			if !errors.Is(err, io.EOF) && !errors.Is(err, net.ErrClosed) {
				reason = err.Error()
			}
			s.finish(id, reason)
			return
		}
	}
}

// finish ends a tunnel from this side and tells the API.
func (s *session) finish(id uint64, reason string) {
	if s.closeTunnel(id) {
		select {
		case s.out <- closeMessage(id, reason):
		case <-s.ctx.Done():
		}
	}
}

// closeTunnel ends a tunnel; false if it was already gone.
func (s *session) closeTunnel(id uint64) bool {
	s.mu.Lock()
	t := s.tunnels[id]
	delete(s.tunnels, id)
	s.mu.Unlock()
	if t == nil {
		return false
	}
	t.stop()
	return true
}

func (s *session) closeTunnels() {
	s.mu.Lock()
	tunnels := s.tunnels
	s.tunnels = map[uint64]*tunnel{}
	s.mu.Unlock()
	for _, t := range tunnels {
		t.stop()
	}
}

func closeMessage(id uint64, reason string) *nodepb.NodeMessage {
	return &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Close{Close: &nodepb.TunnelClose{Id: id, Error: reason}}}
}
