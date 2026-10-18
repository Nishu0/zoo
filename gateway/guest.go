package main

import (
	"context"
	"crypto/hmac"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"slices"
	"strconv"
	"sync"
	"time"

	"github.com/coder/websocket"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"zoo/gateway/gatewaypb"
)

const (
	guestVersion            = 1
	guestHeaderLimit        = 64 << 10
	guestStreamPayloadLimit = 1 << 20
	guestQueueFrames        = 32
	guestPendingLimit       = 1024
	guestStreamLimit        = 128
	guestHelloTimeout       = 10 * time.Second
	guestReconnectWait      = 2 * time.Second
	guestWriteTimeout       = 30 * time.Second
	guestCallTimeout        = 600 * time.Second
)

type guestFrame struct {
	header  map[string]json.RawMessage
	payload []byte
}

func jsonValue(value any) json.RawMessage {
	raw, _ := json.Marshal(value)
	return raw
}

func decodeGuestHeader(raw []byte) (map[string]json.RawMessage, error) {
	if len(raw) == 0 || len(raw) > guestHeaderLimit {
		return nil, status.Error(codes.InvalidArgument, "invalid guest header length")
	}
	var header map[string]json.RawMessage
	if err := json.Unmarshal(raw, &header); err != nil || header == nil {
		return nil, status.Error(codes.InvalidArgument, "guest header must be a JSON object")
	}
	for _, name := range []string{"op", "stream"} {
		if raw, exists := header[name]; exists {
			var value string
			if err := json.Unmarshal(raw, &value); err != nil {
				return nil, status.Errorf(codes.InvalidArgument, "guest %s must be a string", name)
			}
		}
	}
	if raw, exists := header["id"]; exists {
		var requestID uint64
		if err := json.Unmarshal(raw, &requestID); err != nil {
			return nil, status.Error(codes.InvalidArgument, "guest request id must be an unsigned integer")
		}
	}
	return header, nil
}

func headerString(header map[string]json.RawMessage, name string) string {
	var value string
	json.Unmarshal(header[name], &value)
	return value
}

func headerID(header map[string]json.RawMessage) uint64 {
	var value uint64
	json.Unmarshal(header["id"], &value)
	return value
}

func unpackGuestFrame(data []byte) (guestFrame, error) {
	if len(data) < 4 || len(data) > maxMessageBytes {
		return guestFrame{}, status.Error(codes.InvalidArgument, "invalid guest frame length")
	}
	length := uint64(binary.BigEndian.Uint32(data))
	if length > uint64(len(data)-4) || length > guestHeaderLimit {
		return guestFrame{}, status.Error(codes.InvalidArgument, "guest header overruns frame or limit")
	}
	header, err := decodeGuestHeader(data[4 : 4+length])
	return guestFrame{header: header, payload: data[4+length:]}, err
}

func packGuestFrame(frame guestFrame) ([]byte, error) {
	raw, err := json.Marshal(frame.header)
	if err != nil {
		return nil, status.Error(codes.InvalidArgument, "invalid guest header")
	}
	if len(raw) > guestHeaderLimit || len(raw)+len(frame.payload)+4 > maxMessageBytes {
		return nil, status.Error(codes.ResourceExhausted, "guest frame exceeds limit")
	}
	data := make([]byte, 4, 4+len(raw)+len(frame.payload))
	binary.BigEndian.PutUint32(data, uint32(len(raw)))
	return append(append(data, raw...), frame.payload...), nil
}

type guestHub struct {
	mu      sync.Mutex
	guests  map[string]*guestConnection
	changed chan struct{}
	closed  bool
}

func newGuestHub() *guestHub {
	return &guestHub{guests: make(map[string]*guestConnection), changed: make(chan struct{})}
}

func (hub *guestHub) signal() {
	close(hub.changed)
	hub.changed = make(chan struct{})
}

func (hub *guestHub) attach(sandboxID string, guest *guestConnection) bool {
	hub.mu.Lock()
	if hub.closed {
		hub.mu.Unlock()
		return false
	}
	previous := hub.guests[sandboxID]
	hub.guests[sandboxID] = guest
	hub.signal()
	hub.mu.Unlock()
	if previous != nil {
		previous.disconnect(status.Error(codes.Unavailable, "guest connection replaced"))
	}
	return true
}

func (hub *guestHub) detach(sandboxID string, guest *guestConnection) {
	hub.mu.Lock()
	defer hub.mu.Unlock()
	if hub.guests[sandboxID] == guest {
		delete(hub.guests, sandboxID)
		hub.signal()
	}
}

func (hub *guestHub) wait(ctx context.Context, sandboxID string) (*guestConnection, error) {
	if sandboxID == "" {
		return nil, status.Error(codes.InvalidArgument, "sandbox_id is required")
	}
	timer := time.NewTimer(guestReconnectWait)
	defer timer.Stop()
	for {
		if err := ctx.Err(); err != nil {
			return nil, status.FromContextError(err).Err()
		}
		hub.mu.Lock()
		guest, changed, closed := hub.guests[sandboxID], hub.changed, hub.closed
		hub.mu.Unlock()
		if closed {
			return nil, status.Error(codes.Unavailable, "gateway is shutting down")
		}
		if guest != nil {
			select {
			case <-guest.done:
			default:
				return guest, nil
			}
		}
		select {
		case <-ctx.Done():
			return nil, status.FromContextError(ctx.Err()).Err()
		case <-timer.C:
			return nil, status.Error(codes.Unavailable, "guest is not connected")
		case <-changed:
		}
	}
}

func (hub *guestHub) close() {
	hub.mu.Lock()
	hub.closed = true
	guests := make([]*guestConnection, 0, len(hub.guests))
	for _, guest := range hub.guests {
		guests = append(guests, guest)
	}
	hub.guests = make(map[string]*guestConnection)
	hub.signal()
	hub.mu.Unlock()
	for _, guest := range guests {
		guest.disconnect(status.Error(codes.Unavailable, "gateway is shutting down"))
	}
}

type guestPending struct {
	reply    chan guestFrame
	stream   *guestStream
	clientID uint64
}

type guestConnection struct {
	conn     *websocket.Conn
	ctx      context.Context
	cancel   context.CancelFunc
	mu       sync.Mutex
	done     chan struct{}
	failure  error
	sequence uint64
	pending  map[uint64]*guestPending
	streams  map[string]*guestStream
	os       string
	services []string
	metrics  json.RawMessage
	seen     time.Time
}

func newGuestConnection(conn *websocket.Conn, hello map[string]json.RawMessage) (*guestConnection, error) {
	var version int
	var services []string
	var operatingSystem string
	if json.Unmarshal(hello["version"], &version) != nil || version != guestVersion || headerString(hello, "op") != "hello" {
		return nil, errors.New("unsupported guest version")
	}
	if raw, exists := hello["services"]; exists && json.Unmarshal(raw, &services) != nil {
		return nil, errors.New("invalid guest services")
	}
	if raw, exists := hello["os"]; exists && json.Unmarshal(raw, &operatingSystem) != nil {
		return nil, errors.New("invalid guest OS")
	}
	slices.Sort(services)
	ctx, cancel := context.WithCancel(context.Background())
	return &guestConnection{
		conn: conn, ctx: ctx, cancel: cancel, done: make(chan struct{}), pending: make(map[uint64]*guestPending),
		streams: make(map[string]*guestStream), os: operatingSystem, services: slices.Compact(services), seen: time.Now(),
	}, nil
}

func (guest *guestConnection) disconnect(err error) {
	guest.mu.Lock()
	if guest.failure != nil {
		guest.mu.Unlock()
		return
	}
	guest.failure = err
	close(guest.done)
	guest.cancel()
	for _, stream := range guest.streams {
		stream.fail(err)
	}
	guest.pending = make(map[uint64]*guestPending)
	guest.streams = make(map[string]*guestStream)
	guest.mu.Unlock()
	guest.conn.CloseNow()
}

func (guest *guestConnection) send(ctx context.Context, frame guestFrame) error {
	data, err := packGuestFrame(frame)
	if err != nil {
		return err
	}
	writeContext, cancel := context.WithTimeout(ctx, guestWriteTimeout)
	defer cancel()
	if err := guest.conn.Write(writeContext, websocket.MessageBinary, data); err != nil {
		guest.disconnect(status.Error(codes.Unavailable, "guest disconnected during send"))
		if ctx.Err() != nil {
			return status.FromContextError(ctx.Err()).Err()
		}
		return status.Error(codes.Unavailable, "guest disconnected during send")
	}
	return nil
}

func (guest *guestConnection) register(pending *guestPending) (uint64, error) {
	guest.mu.Lock()
	defer guest.mu.Unlock()
	if guest.failure != nil {
		return 0, guest.failure
	}
	if len(guest.pending) >= guestPendingLimit {
		return 0, status.Error(codes.ResourceExhausted, "too many guest requests")
	}
	guest.sequence++
	guest.pending[guest.sequence] = pending
	return guest.sequence, nil
}

func (guest *guestConnection) forget(requestID uint64) {
	guest.mu.Lock()
	delete(guest.pending, requestID)
	guest.mu.Unlock()
}

func (guest *guestConnection) read() error {
	for {
		kind, data, err := guest.conn.Read(guest.ctx)
		if err != nil {
			return err
		}
		if kind != websocket.MessageBinary {
			return errors.New("guest frames must be binary")
		}
		frame, err := unpackGuestFrame(data)
		if err != nil {
			return err
		}
		guest.mu.Lock()
		guest.seen = time.Now()
		operation := headerString(frame.header, "op")
		if operation == "metrics" {
			guest.metrics = append(json.RawMessage(nil), frame.header["data"]...)
			guest.mu.Unlock()
			continue
		}
		if operation == "pty_data" || operation == "pty_exit" || operation == "tunnel_data" || operation == "tunnel_close" {
			wireID := headerString(frame.header, "stream")
			stream := guest.streams[wireID]
			guest.mu.Unlock()
			if stream != nil {
				frame.header["stream"] = jsonValue(stream.clientStream)
				stream.offer(frame)
			}
			continue
		}
		requestID := headerID(frame.header)
		pending := guest.pending[requestID]
		delete(guest.pending, requestID)
		guest.mu.Unlock()
		if pending == nil {
			continue
		}
		if pending.stream != nil {
			frame.header["id"] = jsonValue(pending.clientID)
			var result map[string]json.RawMessage
			if json.Unmarshal(frame.header["result"], &result) == nil && result != nil {
				if _, exists := result["stream"]; exists {
					result["stream"] = jsonValue(pending.stream.clientStream)
					frame.header["result"] = jsonValue(result)
				}
			}
			pending.stream.offer(frame)
		} else {
			pending.reply <- frame
		}
	}
}

func (service *gatewayServer) serveGuest(writer http.ResponseWriter, request *http.Request) {
	sandboxID := request.Header.Get("X-Zoo-Sandbox")
	auth := request.Header.Values("Authorization")
	if sandboxID == "" || len(auth) != 1 || !hmac.Equal([]byte(auth[0]), []byte("Bearer "+service.settings.token("zoo-guest:"+sandboxID))) {
		http.Error(writer, "invalid guest token", http.StatusUnauthorized)
		return
	}
	if service.routes.routeGuest(writer, request, sandboxID) {
		return
	}
	conn, err := websocket.Accept(writer, request, nil)
	if err != nil {
		return
	}
	defer conn.CloseNow()
	conn.SetReadLimit(maxMessageBytes)
	helloContext, cancel := context.WithTimeout(request.Context(), guestHelloTimeout)
	kind, data, err := conn.Read(helloContext)
	cancel()
	if err != nil || kind != websocket.MessageBinary {
		conn.Close(websocket.StatusProtocolError, "expected binary hello")
		return
	}
	frame, err := unpackGuestFrame(data)
	if err != nil || len(frame.payload) != 0 {
		conn.Close(websocket.StatusProtocolError, "invalid hello frame")
		return
	}
	guest, err := newGuestConnection(conn, frame.header)
	if err != nil {
		conn.Close(websocket.StatusPolicyViolation, "unsupported guest hello")
		return
	}
	if !service.guests.attach(sandboxID, guest) {
		guest.disconnect(status.Error(codes.Unavailable, "gateway is shutting down"))
		return
	}
	defer service.guests.detach(sandboxID, guest)
	defer guest.disconnect(status.Error(codes.Unavailable, "guest disconnected"))
	guest.read()
}

func notification(operation string) bool {
	return operation == "pty_input" || operation == "pty_resize" || operation == "pty_kill" || operation == "tunnel_write" || operation == "tunnel_close"
}

func (service *gatewayServer) GuestCall(ctx context.Context, request *gatewaypb.GuestCallRequest) (*gatewaypb.GuestCallReply, error) {
	if err := service.routes.requireOwner(ctx, request.SandboxId); err != nil {
		return nil, err
	}
	if request.Op == "" {
		return nil, status.Error(codes.InvalidArgument, "op is required")
	}
	header, err := decodeGuestHeader(request.HeaderJson)
	if err != nil {
		return nil, err
	}
	header["op"] = jsonValue(request.Op)
	delete(header, "id")
	frame := guestFrame{header: header, payload: request.Payload}
	if _, err := packGuestFrame(frame); err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(ctx, guestCallTimeout)
	defer cancel()
	guest, err := service.guests.wait(ctx, request.SandboxId)
	if err != nil {
		return nil, err
	}
	if notification(request.Op) {
		if err := guest.send(ctx, frame); err != nil {
			return nil, err
		}
		return &gatewaypb.GuestCallReply{}, nil
	}
	pending := &guestPending{reply: make(chan guestFrame, 1)}
	requestID, err := guest.register(pending)
	if err != nil {
		return nil, err
	}
	defer guest.forget(requestID)
	header["id"] = jsonValue(requestID)
	if err := guest.send(ctx, frame); err != nil {
		return nil, err
	}
	select {
	case response := <-pending.reply:
		raw, _ := json.Marshal(response.header)
		return &gatewaypb.GuestCallReply{HeaderJson: raw, Payload: response.payload}, nil
	case <-ctx.Done():
		return nil, status.FromContextError(ctx.Err()).Err()
	case <-guest.done:
		return nil, status.Error(codes.Unavailable, "guest disconnected during request")
	}
}

func (service *gatewayServer) GuestStatus(ctx context.Context, request *gatewaypb.GuestStatusRequest) (*gatewaypb.GuestStatusReply, error) {
	if err := service.routes.requireOwner(ctx, request.SandboxId); err != nil {
		return nil, err
	}
	if request.SandboxId == "" {
		return nil, status.Error(codes.InvalidArgument, "sandbox_id is required")
	}
	service.guests.mu.Lock()
	guest := service.guests.guests[request.SandboxId]
	service.guests.mu.Unlock()
	if guest == nil {
		return &gatewaypb.GuestStatusReply{}, nil
	}
	guest.mu.Lock()
	defer guest.mu.Unlock()
	if guest.failure != nil {
		return &gatewaypb.GuestStatusReply{}, nil
	}
	quiet := time.Since(guest.seen).Seconds()
	return &gatewaypb.GuestStatusReply{
		Connected: true, Version: guestVersion, Os: guest.os, Services: slices.Clone(guest.services),
		MetricsJson: slices.Clone(guest.metrics), QuietSeconds: quiet,
		Heartbeat: !slices.Contains(guest.services, "metrics") || quiet < 45,
	}, nil
}

type guestStream struct {
	mu           sync.Mutex
	frames       []guestFrame
	queuedBytes  int
	available    chan struct{}
	done         chan struct{}
	failure      error
	clientStream string
	wireStream   string
	operation    string
}

const guestStreamBufferBytes = guestQueueFrames * (64 << 10)

func newGuestStream(clientStream, operation string) *guestStream {
	return &guestStream{available: make(chan struct{}, 1), done: make(chan struct{}), clientStream: clientStream, operation: operation}
}

func (stream *guestStream) next() (guestFrame, bool) {
	stream.mu.Lock()
	defer stream.mu.Unlock()
	if len(stream.frames) == 0 {
		return guestFrame{}, false
	}
	frame := stream.frames[0]
	stream.frames[0] = guestFrame{}
	stream.frames = stream.frames[1:]
	stream.queuedBytes -= len(frame.payload)
	if len(stream.frames) > 0 {
		select {
		case stream.available <- struct{}{}:
		default:
		}
	}
	return frame, true
}

func (stream *guestStream) fail(err error) {
	stream.mu.Lock()
	defer stream.mu.Unlock()
	if stream.failure == nil {
		stream.failure = err
		close(stream.done)
	}
}

func (stream *guestStream) offer(frame guestFrame) {
	stream.mu.Lock()
	defer stream.mu.Unlock()
	if stream.failure != nil {
		return
	}
	if len(frame.payload) > guestStreamPayloadLimit {
		stream.failure = status.Error(codes.ResourceExhausted, "guest stream payload exceeds limit")
		close(stream.done)
		return
	}
	if stream.queuedBytes+len(frame.payload) > guestStreamBufferBytes {
		stream.failure = status.Error(codes.ResourceExhausted, "guest stream consumer is too slow")
		close(stream.done)
		return
	}
	// TCP can fragment a single VNC redraw into many small tunnel_data messages.
	// Combine adjacent data while holding the queue lock, before ownership passes
	// to the sender. Control messages retain their order and frame boundaries.
	merged := false
	if last := len(stream.frames) - 1; last >= 0 && headerString(frame.header, "op") == "tunnel_data" && headerString(stream.frames[last].header, "op") == "tunnel_data" && len(stream.frames[last].payload)+len(frame.payload) <= guestStreamPayloadLimit {
		stream.frames[last].payload = append(stream.frames[last].payload, frame.payload...)
		merged = true
	}
	if !merged {
		if len(stream.frames) >= guestQueueFrames {
			stream.failure = status.Error(codes.ResourceExhausted, "guest stream consumer is too slow")
			close(stream.done)
			return
		}
		stream.frames = append(stream.frames, frame)
	}
	stream.queuedBytes += len(frame.payload)
	select {
	case stream.available <- struct{}{}:
	default:
	}
}

func streamArgs(header map[string]json.RawMessage) (map[string]json.RawMessage, error) {
	var args map[string]json.RawMessage
	if json.Unmarshal(header["args"], &args) != nil || args == nil || headerString(args, "stream") == "" {
		return nil, status.Error(codes.InvalidArgument, "stream args must name a stream")
	}
	return args, nil
}

func (service *gatewayServer) GuestStream(rpc grpc.BidiStreamingServer[gatewaypb.Frame, gatewaypb.Frame]) error {
	first, err := rpc.Recv()
	if err != nil {
		return err
	}
	if err := service.routes.requireOwner(rpc.Context(), first.SandboxId); err != nil {
		return err
	}
	header, err := decodeGuestHeader(first.HeaderJson)
	if err != nil {
		return err
	}
	operation := headerString(header, "op")
	clientID := headerID(header)
	if (operation != "pty_open" && operation != "tunnel_open") || clientID == 0 || len(first.Payload) > guestStreamPayloadLimit {
		return status.Error(codes.InvalidArgument, "first stream frame must request pty_open or tunnel_open")
	}
	args, err := streamArgs(header)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithCancel(rpc.Context())
	defer cancel()
	guest, err := service.guests.wait(ctx, first.SandboxId)
	if err != nil {
		return err
	}
	stream := newGuestStream(headerString(args, "stream"), operation)
	pending := &guestPending{stream: stream, clientID: clientID}
	requestID, err := guest.register(pending)
	if err != nil {
		return err
	}
	defer guest.forget(requestID)
	stream.wireStream = "gateway-" + strconv.FormatUint(requestID, 10)
	guest.mu.Lock()
	if guest.failure != nil || len(guest.streams) >= guestStreamLimit {
		guest.mu.Unlock()
		return status.Error(codes.Unavailable, "guest stream is unavailable")
	}
	guest.streams[stream.wireStream] = stream
	guest.mu.Unlock()
	defer func() {
		guest.mu.Lock()
		delete(guest.streams, stream.wireStream)
		guest.mu.Unlock()
		stream.fail(status.Error(codes.Canceled, "stream closed"))
		closeOperation := "tunnel_close"
		if operation == "pty_open" {
			closeOperation = "pty_kill"
		}
		cleanup, finish := context.WithTimeout(context.Background(), time.Second)
		defer finish()
		guest.send(cleanup, guestFrame{header: map[string]json.RawMessage{"op": jsonValue(closeOperation), "args": jsonValue(map[string]string{"stream": stream.wireStream})}})
	}()
	args["stream"] = jsonValue(stream.wireStream)
	header["args"], header["id"] = jsonValue(args), jsonValue(requestID)
	if err := guest.send(ctx, guestFrame{header: header, payload: first.Payload}); err != nil {
		return err
	}
	acknowledged := make(chan struct{}, 1)
	sent := make(chan error, 1)
	go func() { sent <- stream.send(ctx, rpc, first.SandboxId, acknowledged) }()
	type incomingFrame struct {
		frame *gatewaypb.Frame
		err   error
	}
	incoming := make(chan incomingFrame, 1)
	go func() {
		for {
			frame, err := rpc.Recv()
			select {
			case incoming <- incomingFrame{frame, err}:
			case <-ctx.Done():
				return
			}
			if err != nil {
				return
			}
		}
	}()
	opening := time.NewTimer(guestWriteTimeout)
	defer opening.Stop()
	deadline := opening.C
	for {
		select {
		case <-ctx.Done():
			return status.FromContextError(ctx.Err()).Err()
		case <-guest.done:
			return status.Error(codes.Unavailable, "guest disconnected during stream")
		case <-stream.done:
			stream.mu.Lock()
			err := stream.failure
			stream.mu.Unlock()
			return err
		case err := <-sent:
			return err
		case <-acknowledged:
			opening.Stop()
			deadline = nil
		case <-deadline:
			return status.Error(codes.DeadlineExceeded, "guest stream open timed out")
		case next := <-incoming:
			if errors.Is(next.err, io.EOF) {
				incoming = nil
				continue
			}
			if next.err != nil {
				return next.err
			}
			if next.frame.SandboxId != "" && next.frame.SandboxId != first.SandboxId {
				return status.Error(codes.InvalidArgument, "cannot change stream sandbox")
			}
			header, err := decodeGuestHeader(next.frame.HeaderJson)
			if err != nil {
				return err
			}
			args, err := streamArgs(header)
			if err != nil {
				return err
			}
			operation := headerString(header, "op")
			pty := stream.operation == "pty_open" && (operation == "pty_input" || operation == "pty_resize" || operation == "pty_kill")
			tunnel := stream.operation == "tunnel_open" && (operation == "tunnel_write" || operation == "tunnel_close")
			if headerID(header) != 0 || headerString(args, "stream") != stream.clientStream || (!pty && !tunnel) || len(next.frame.Payload) > guestStreamPayloadLimit {
				return status.Error(codes.InvalidArgument, "invalid stream notification")
			}
			args["stream"] = jsonValue(stream.wireStream)
			header["args"] = jsonValue(args)
			if err := guest.send(ctx, guestFrame{header: header, payload: next.frame.Payload}); err != nil {
				return err
			}
		}
	}
}

func (stream *guestStream) send(ctx context.Context, rpc grpc.BidiStreamingServer[gatewaypb.Frame, gatewaypb.Frame], sandboxID string, acknowledged chan<- struct{}) error {
	opened, ended := false, false
	for {
		select {
		case <-ctx.Done():
			return status.FromContextError(ctx.Err()).Err()
		case <-stream.done:
			stream.mu.Lock()
			err := stream.failure
			stream.mu.Unlock()
			return err
		case <-stream.available:
			frame, exists := stream.next()
			if !exists {
				continue
			}
			raw, _ := json.Marshal(frame.header)
			if err := rpc.Send(&gatewaypb.Frame{SandboxId: sandboxID, HeaderJson: raw, Payload: frame.payload}); err != nil {
				return err
			}
			if headerID(frame.header) != 0 {
				opened = true
				acknowledged <- struct{}{}
				var ok bool
				if json.Unmarshal(frame.header["ok"], &ok) != nil || !ok {
					return nil
				}
			}
			operation := headerString(frame.header, "op")
			ended = ended || operation == "pty_exit" || operation == "tunnel_close"
			if opened && ended {
				return nil
			}
		}
	}
}
