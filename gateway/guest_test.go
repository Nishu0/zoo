package main

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/coder/websocket"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"zoo/gateway/gatewaypb"
)

type guestFixture struct {
	service *gatewayServer
	client  gatewaypb.GatewayClient
	url     string
	ctx     context.Context
	done    chan struct{}
}

func newGuestFixture(test *testing.T) *guestFixture {
	test.Helper()
	settings := config{secretsKey: "guest-test-key", peerHTTPPort: 8002}
	done := make(chan struct{})
	service := newGatewayServer(settings)
	peers, _ := testPeerSet(test, "127.0.0.1")
	service.routes = newReplicaRouter(peers, nodeTestCA(test), settings)
	mux := http.NewServeMux()
	mux.HandleFunc("GET /guest/connect", service.serveGuest)
	httpServer := httptest.NewServer(mux)
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		test.Fatal(err)
	}
	rpcServer := grpc.NewServer(grpc.UnaryInterceptor(settings.unaryAuth), grpc.StreamInterceptor(settings.streamAuth), grpc.MaxRecvMsgSize(maxMessageBytes), grpc.MaxSendMsgSize(maxMessageBytes))
	gatewaypb.RegisterGatewayServer(rpcServer, service)
	go rpcServer.Serve(listener)
	client, err := grpc.NewClient(listener.Addr().String(), grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithDefaultCallOptions(grpc.MaxCallRecvMsgSize(maxMessageBytes), grpc.MaxCallSendMsgSize(maxMessageBytes)))
	if err != nil {
		test.Fatal(err)
	}
	test.Cleanup(func() {
		close(done)
		service.guests.close()
		service.routes.close()
		client.Close()
		rpcServer.Stop()
		httpServer.Close()
	})
	return &guestFixture{service: service, client: gatewaypb.NewGatewayClient(client), url: "ws" + strings.TrimPrefix(httpServer.URL, "http") + "/guest/connect", ctx: metadata.AppendToOutgoingContext(context.Background(), "authorization", "Bearer "+settings.token("zoo-gateway")), done: done}
}

type fakeRequest struct {
	ID      uint64         `json:"id"`
	Op      string         `json:"op"`
	Args    map[string]any `json:"args"`
	Payload []byte         `json:"-"`
}

type fakeGuest struct {
	conn     *websocket.Conn
	requests chan fakeRequest
	closed   chan struct{}
}

func guestAuthorization(sandboxID string) string {
	digest := hmac.New(sha256.New, []byte("guest-test-key"))
	digest.Write([]byte("zoo-guest:" + sandboxID))
	return "Bearer " + hex.EncodeToString(digest.Sum(nil))
}

func fakeFrame(header any, payload []byte) []byte {
	raw, _ := json.Marshal(header)
	data := make([]byte, 4, len(raw)+len(payload)+4)
	binary.BigEndian.PutUint32(data, uint32(len(raw)))
	return append(append(data, raw...), payload...)
}

func dialGuest(test *testing.T, fixture *guestFixture, sandboxID string) *fakeGuest {
	test.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	conn, _, err := websocket.Dial(ctx, fixture.url, &websocket.DialOptions{HTTPHeader: http.Header{"Authorization": {guestAuthorization(sandboxID)}, "X-Zoo-Sandbox": {sandboxID}}})
	if err != nil {
		test.Fatal(err)
	}
	test.Cleanup(func() { conn.CloseNow() })
	conn.SetReadLimit(maxMessageBytes)
	if err := conn.Write(ctx, websocket.MessageBinary, fakeFrame(map[string]any{"op": "hello", "version": 1, "os": "linux", "services": []string{"tunnel", "metrics", "exec", "pty"}}, nil)); err != nil {
		test.Fatal(err)
	}
	if _, err := fixture.service.guests.wait(ctx, sandboxID); err != nil {
		test.Fatal(err)
	}
	guest := &fakeGuest{conn: conn, requests: make(chan fakeRequest, 64), closed: make(chan struct{})}
	go func() {
		defer close(guest.closed)
		for {
			kind, data, err := conn.Read(context.Background())
			if err != nil {
				return
			}
			if kind != websocket.MessageBinary || len(data) < 4 {
				test.Error("invalid outbound framing")
				return
			}
			length := int(binary.BigEndian.Uint32(data))
			if length > len(data)-4 {
				test.Error("outbound header overruns message")
				return
			}
			var request fakeRequest
			if err := json.Unmarshal(data[4:4+length], &request); err != nil {
				test.Error(err)
				return
			}
			request.Payload = bytes.Clone(data[4+length:])
			select {
			case guest.requests <- request:
			case <-fixture.done:
				return
			}
		}
	}()
	return guest
}

func nextRequest(test *testing.T, guest *fakeGuest) fakeRequest {
	test.Helper()
	select {
	case request := <-guest.requests:
		return request
	case <-time.After(3 * time.Second):
		test.Fatal("fake guest received no request")
	}
	return fakeRequest{}
}

func (guest *fakeGuest) send(test *testing.T, header any, payload []byte) {
	test.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := guest.conn.Write(ctx, websocket.MessageBinary, fakeFrame(header, payload)); err != nil {
		test.Fatal(err)
	}
}

func (guest *fakeGuest) reply(test *testing.T, request fakeRequest, result any, payload []byte) {
	guest.send(test, map[string]any{"id": request.ID, "ok": true, "result": result}, payload)
}

type callResult struct {
	reply *gatewaypb.GuestCallReply
	err   error
}

func callGuest(fixture *guestFixture, ctx context.Context, sandboxID, operation string, args any, payload []byte) <-chan callResult {
	result := make(chan callResult, 1)
	go func() {
		header, _ := json.Marshal(map[string]any{"id": 1, "args": args})
		reply, err := fixture.client.GuestCall(ctx, &gatewaypb.GuestCallRequest{SandboxId: sandboxID, Op: operation, HeaderJson: header, Payload: payload})
		result <- callResult{reply, err}
	}()
	return result
}

func awaitCall(test *testing.T, result <-chan callResult) callResult {
	test.Helper()
	select {
	case reply := <-result:
		return reply
	case <-time.After(4 * time.Second):
		test.Fatal("guest RPC did not finish")
	}
	return callResult{}
}

func frameHeader(test *testing.T, raw []byte) map[string]any {
	test.Helper()
	var header map[string]any
	if err := json.Unmarshal(raw, &header); err != nil {
		test.Fatal(err)
	}
	return header
}

func TestGuestCallRoundTripAndStatus(test *testing.T) {
	fixture := newGuestFixture(test)
	guest := dialGuest(test, fixture, "sandbox")
	result := callGuest(fixture, fixture.ctx, "sandbox", "exec", map[string]any{"argv": []string{"echo", "hello"}}, []byte{0, 255, 1})
	request := nextRequest(test, guest)
	if request.ID == 0 || request.Op != "exec" || !bytes.Equal(request.Payload, []byte{0, 255, 1}) || request.Args["argv"].([]any)[1] != "hello" {
		test.Fatalf("wrong wire request: %+v", request)
	}
	guest.send(test, map[string]any{"op": "metrics", "data": map[string]any{"memory_usage": 1024}}, nil)
	guest.reply(test, request, map[string]any{"exit_code": 0, "stdout_len": 3}, []byte{'o', 'k', '\n', 255})
	reply := awaitCall(test, result)
	if reply.err != nil {
		test.Fatal(reply.err)
	}
	header := frameHeader(test, reply.reply.HeaderJson)
	if header["ok"] != true || header["result"].(map[string]any)["exit_code"] != float64(0) || !bytes.Equal(reply.reply.Payload, []byte{'o', 'k', '\n', 255}) {
		test.Fatalf("wrong reply: %v %v", header, reply.reply.Payload)
	}
	state, err := fixture.client.GuestStatus(fixture.ctx, &gatewaypb.GuestStatusRequest{SandboxId: "sandbox"})
	if err != nil || !state.Connected || !state.Heartbeat || state.Version != 1 || state.Os != "linux" || strings.Join(state.Services, ",") != "exec,metrics,pty,tunnel" || string(state.MetricsJson) != `{"memory_usage":1024}` {
		test.Fatalf("wrong status: %v %v", state, err)
	}
	fixture.service.guests.mu.Lock()
	connected := fixture.service.guests.guests["sandbox"]
	fixture.service.guests.mu.Unlock()
	connected.mu.Lock()
	connected.seen = time.Now().Add(-46 * time.Second)
	connected.mu.Unlock()
	state, err = fixture.client.GuestStatus(fixture.ctx, &gatewaypb.GuestStatusRequest{SandboxId: "sandbox"})
	if err != nil || state.Heartbeat || state.QuietSeconds < 45 {
		test.Fatalf("stale heartbeat: %v %v", state, err)
	}
	guest.conn.CloseNow()
	select {
	case <-connected.done:
	case <-time.After(time.Second):
		test.Fatal("disconnect not drained")
	}
	state, err = fixture.client.GuestStatus(fixture.ctx, &gatewaypb.GuestStatusRequest{SandboxId: "sandbox"})
	if err != nil || state.Connected {
		test.Fatalf("disconnected status: %v %v", state, err)
	}
}

func TestGuestCallIDsErrorsAndCancellation(test *testing.T) {
	fixture := newGuestFixture(test)
	guest := dialGuest(test, fixture, "sandbox")
	first := callGuest(fixture, fixture.ctx, "sandbox", "read", map[string]any{"path": "one"}, nil)
	second := callGuest(fixture, fixture.ctx, "sandbox", "read", map[string]any{"path": "two"}, nil)
	requests := []fakeRequest{nextRequest(test, guest), nextRequest(test, guest)}
	if requests[0].ID == requests[1].ID {
		test.Fatal("colliding request IDs")
	}
	for index := len(requests) - 1; index >= 0; index-- {
		guest.reply(test, requests[index], map[string]any{}, []byte(requests[index].Args["path"].(string)))
	}
	for path, result := range map[string]<-chan callResult{"one": first, "two": second} {
		reply := awaitCall(test, result)
		if reply.err != nil || string(reply.reply.Payload) != path {
			test.Fatalf("reply crossed requests: %v %v", reply.reply, reply.err)
		}
	}
	failed := callGuest(fixture, fixture.ctx, "sandbox", "exec", map[string]any{}, nil)
	request := nextRequest(test, guest)
	guest.send(test, map[string]any{"id": request.ID, "ok": false, "error": "command failed"}, nil)
	reply := awaitCall(test, failed)
	if reply.err != nil || frameHeader(test, reply.reply.HeaderJson)["error"] != "command failed" {
		test.Fatalf("guest error lost: %v %v", reply.reply, reply.err)
	}
	ctx, cancel := context.WithTimeout(fixture.ctx, 60*time.Millisecond)
	defer cancel()
	blocked := callGuest(fixture, ctx, "sandbox", "write", map[string]any{"path": "held"}, []byte("once"))
	nextRequest(test, guest)
	if reply := awaitCall(test, blocked); status.Code(reply.err) != codes.DeadlineExceeded {
		test.Fatalf("deadline: %v", reply.err)
	}
	barrier := callGuest(fixture, fixture.ctx, "sandbox", "read", map[string]any{}, nil)
	request = nextRequest(test, guest)
	if request.Op != "read" {
		test.Fatalf("canceled write retried: %+v", request)
	}
	guest.reply(test, request, map[string]any{}, nil)
	if reply := awaitCall(test, barrier); reply.err != nil {
		test.Fatal(reply.err)
	}
	fixture.service.guests.mu.Lock()
	connected := fixture.service.guests.guests["sandbox"]
	fixture.service.guests.mu.Unlock()
	connected.mu.Lock()
	remaining := len(connected.pending)
	connected.mu.Unlock()
	if remaining != 0 {
		test.Fatalf("pending requests leaked: %d", remaining)
	}
}

func TestGuestDisconnectAndReconnectDoNotRetry(test *testing.T) {
	fixture := newGuestFixture(test)
	guest := dialGuest(test, fixture, "sandbox")
	blocked := callGuest(fixture, fixture.ctx, "sandbox", "exec", map[string]any{}, nil)
	nextRequest(test, guest)
	guest.conn.CloseNow()
	if reply := awaitCall(test, blocked); status.Code(reply.err) != codes.Unavailable {
		test.Fatalf("disconnect: %v", reply.err)
	}
	queued := callGuest(fixture, fixture.ctx, "sandbox", "write", map[string]any{}, []byte("new"))
	guest = dialGuest(test, fixture, "sandbox")
	request := nextRequest(test, guest)
	if request.Op != "write" || string(request.Payload) != "new" {
		test.Fatalf("replayed old call: %+v", request)
	}
	guest.reply(test, request, map[string]any{}, nil)
	if reply := awaitCall(test, queued); reply.err != nil {
		test.Fatal(reply.err)
	}
}

func TestGuestReconnectWaitIsBounded(test *testing.T) {
	fixture := newGuestFixture(test)
	started := time.Now()
	reply := awaitCall(test, callGuest(fixture, fixture.ctx, "missing", "exec", map[string]any{}, nil))
	if status.Code(reply.err) != codes.Unavailable || time.Since(started) < 1500*time.Millisecond || time.Since(started) > 3500*time.Millisecond {
		test.Fatalf("reconnect wait: %v after %s", reply.err, time.Since(started))
	}
	ctx, cancel := context.WithTimeout(fixture.ctx, 30*time.Millisecond)
	defer cancel()
	reply = awaitCall(test, callGuest(fixture, ctx, "missing", "exec", map[string]any{}, nil))
	if status.Code(reply.err) != codes.DeadlineExceeded {
		test.Fatalf("waiting cancellation: %v", reply.err)
	}
}

func TestGuestAuthentication(test *testing.T) {
	fixture := newGuestFixture(test)
	for _, token := range []string{"wrong", guestAuthorization("other-sandbox")} {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		conn, response, err := websocket.Dial(ctx, fixture.url, &websocket.DialOptions{HTTPHeader: http.Header{"Authorization": {token}, "X-Zoo-Sandbox": {"sandbox"}}})
		cancel()
		if conn != nil {
			conn.CloseNow()
		}
		if err == nil || response == nil || response.StatusCode != http.StatusUnauthorized {
			test.Fatalf("guest auth accepted: %v %v", response, err)
		}
	}
	_, err := fixture.client.GuestStatus(context.Background(), &gatewaypb.GuestStatusRequest{SandboxId: "sandbox"})
	if status.Code(err) != codes.Unauthenticated {
		test.Fatalf("unary auth: %v", err)
	}
	stream, err := fixture.client.GuestStream(context.Background())
	if err != nil {
		test.Fatal(err)
	}
	stream.Send(&gatewaypb.Frame{SandboxId: "sandbox", HeaderJson: []byte(`{"id":1,"op":"pty_open","args":{"stream":"terminal"}}`)})
	_, err = stream.Recv()
	if status.Code(err) != codes.Unauthenticated {
		test.Fatalf("stream auth: %v", err)
	}
}

func TestGuestRejectsMalformedHelloAndFrames(test *testing.T) {
	fixture := newGuestFixture(test)
	oversized := make([]byte, 4)
	binary.BigEndian.PutUint32(oversized, guestHeaderLimit+1)
	oversized = append(oversized, bytes.Repeat([]byte{' '}, guestHeaderLimit+1)...)
	for name, data := range map[string][]byte{
		"short": {0, 1, 2}, "overrun": {0, 0, 0, 20, '{'}, "invalid_json": {0, 0, 0, 1, '{'}, "header_limit": oversized,
		"not_object": {0, 0, 0, 2, '[', ']'}, "wrong_version": fakeFrame(map[string]any{"op": "hello", "version": 2}, nil),
	} {
		test.Run(name, func(test *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			conn, _, err := websocket.Dial(ctx, fixture.url, &websocket.DialOptions{HTTPHeader: http.Header{"Authorization": {guestAuthorization(name)}, "X-Zoo-Sandbox": {name}}})
			if err != nil {
				test.Fatal(err)
			}
			defer conn.CloseNow()
			conn.Write(ctx, websocket.MessageBinary, data)
			if _, _, err := conn.Read(ctx); err == nil {
				test.Fatal("bad hello accepted")
			}
		})
	}
	guest := dialGuest(test, fixture, "sandbox")
	guest.conn.Write(context.Background(), websocket.MessageText, []byte("not binary"))
	select {
	case <-guest.closed:
	case <-time.After(time.Second):
		test.Fatal("bad live frame accepted")
	}
	_, err := fixture.client.GuestCall(fixture.ctx, &gatewaypb.GuestCallRequest{SandboxId: "sandbox", Op: "exec", HeaderJson: bytes.Repeat([]byte{' '}, guestHeaderLimit+1)})
	if status.Code(err) != codes.InvalidArgument {
		test.Fatalf("header limit: %v", err)
	}
}

func openRPCStream(test *testing.T, fixture *guestFixture, ctx context.Context, operation string) gatewaypb.Gateway_GuestStreamClient {
	test.Helper()
	stream, err := fixture.client.GuestStream(ctx)
	if err != nil {
		test.Fatal(err)
	}
	header, _ := json.Marshal(map[string]any{"id": 1, "op": operation, "args": map[string]any{"stream": "shared", "port": 5900, "cols": 80, "rows": 24}})
	if err := stream.Send(&gatewaypb.Frame{SandboxId: "sandbox", HeaderJson: header}); err != nil {
		test.Fatal(err)
	}
	return stream
}

func receiveRPCFrame(test *testing.T, stream gatewaypb.Gateway_GuestStreamClient) *gatewaypb.Frame {
	test.Helper()
	frame, err := stream.Recv()
	if err != nil {
		test.Fatal(err)
	}
	return frame
}

func TestGuestStreamsRegisterBeforeOpenAndScopeIDs(test *testing.T) {
	fixture := newGuestFixture(test)
	guest := dialGuest(test, fixture, "sandbox")
	ctx, cancel := context.WithTimeout(fixture.ctx, 5*time.Second)
	defer cancel()
	terminal := openRPCStream(test, fixture, ctx, "pty_open")
	tunnel := openRPCStream(test, fixture, ctx, "tunnel_open")
	requests := map[string]fakeRequest{}
	for range 2 {
		request := nextRequest(test, guest)
		requests[request.Op] = request
	}
	if requests["pty_open"].ID == requests["tunnel_open"].ID || requests["pty_open"].Args["stream"] == requests["tunnel_open"].Args["stream"] {
		test.Fatal("stream clients share wire IDs")
	}
	for operation, stream := range map[string]gatewaypb.Gateway_GuestStreamClient{"pty_open": terminal, "tunnel_open": tunnel} {
		request := requests[operation]
		wireStream := request.Args["stream"].(string)
		dataOp, closeOp, inputOp := "pty_data", "pty_exit", "pty_input"
		if operation == "tunnel_open" {
			dataOp, closeOp, inputOp = "tunnel_data", "tunnel_close", "tunnel_write"
		}
		guest.send(test, map[string]any{"op": dataOp, "stream": wireStream}, []byte(operation))
		first := receiveRPCFrame(test, stream)
		if frameHeader(test, first.HeaderJson)["stream"] != "shared" || string(first.Payload) != operation {
			test.Fatalf("early data crossed stream: %v", first)
		}
		guest.reply(test, request, map[string]any{"stream": wireStream}, nil)
		reply := frameHeader(test, receiveRPCFrame(test, stream).HeaderJson)
		if reply["id"] != float64(1) || reply["result"].(map[string]any)["stream"] != "shared" {
			test.Fatalf("wire IDs leaked: %v", reply)
		}
		notification, _ := json.Marshal(map[string]any{"op": inputOp, "args": map[string]any{"stream": "shared"}})
		stream.Send(&gatewaypb.Frame{HeaderJson: notification, Payload: []byte("input")})
		input := nextRequest(test, guest)
		if input.ID != 0 || input.Op != inputOp || input.Args["stream"] != wireStream || string(input.Payload) != "input" {
			test.Fatalf("wrong notification: %+v", input)
		}
		guest.send(test, map[string]any{"op": closeOp, "stream": wireStream, "exit_code": 0}, nil)
		receiveRPCFrame(test, stream)
		if _, err := stream.Recv(); err != io.EOF {
			test.Fatalf("stream did not end: %v", err)
		}
		cleanup := nextRequest(test, guest)
		if cleanup.Args["stream"] != wireStream {
			test.Fatalf("wrong cleanup: %+v", cleanup)
		}
	}
}

func TestGuestStreamExitBeforeOpenReplyAndHalfClose(test *testing.T) {
	fixture := newGuestFixture(test)
	guest := dialGuest(test, fixture, "sandbox")
	ctx, cancel := context.WithTimeout(fixture.ctx, 3*time.Second)
	defer cancel()
	stream := openRPCStream(test, fixture, ctx, "pty_open")
	stream.CloseSend()
	request := nextRequest(test, guest)
	guest.send(test, map[string]any{"op": "pty_exit", "stream": request.Args["stream"], "exit_code": 0}, nil)
	if frameHeader(test, receiveRPCFrame(test, stream).HeaderJson)["op"] != "pty_exit" {
		test.Fatal("missing early exit")
	}
	guest.reply(test, request, map[string]any{"stream": request.Args["stream"]}, nil)
	if frameHeader(test, receiveRPCFrame(test, stream).HeaderJson)["ok"] != true {
		test.Fatal("opening reply lost")
	}
	if _, err := stream.Recv(); err != io.EOF {
		test.Fatalf("half-closed stream: %v", err)
	}
}

func TestGuestStreamCancellationAndDisconnectDrain(test *testing.T) {
	fixture := newGuestFixture(test)
	guest := dialGuest(test, fixture, "sandbox")
	ctx, cancel := context.WithCancel(fixture.ctx)
	stream := openRPCStream(test, fixture, ctx, "tunnel_open")
	request := nextRequest(test, guest)
	guest.reply(test, request, map[string]any{}, nil)
	receiveRPCFrame(test, stream)
	cancel()
	cleanup := nextRequest(test, guest)
	if cleanup.Op != "tunnel_close" || cleanup.ID != 0 || cleanup.Args["stream"] != request.Args["stream"] {
		test.Fatalf("cancellation leaked tunnel: %+v", cleanup)
	}
	ctx, cancel = context.WithTimeout(fixture.ctx, 3*time.Second)
	defer cancel()
	stream = openRPCStream(test, fixture, ctx, "pty_open")
	nextRequest(test, guest)
	fixture.service.guests.mu.Lock()
	connected := fixture.service.guests.guests["sandbox"]
	fixture.service.guests.mu.Unlock()
	blocked := callGuest(fixture, fixture.ctx, "sandbox", "exec", map[string]any{}, nil)
	nextRequest(test, guest)
	guest.conn.CloseNow()
	if _, err := stream.Recv(); status.Code(err) != codes.Unavailable {
		test.Fatalf("stream disconnect: %v", err)
	}
	if reply := awaitCall(test, blocked); status.Code(reply.err) != codes.Unavailable {
		test.Fatalf("call disconnect: %v", reply.err)
	}
	connected.mu.Lock()
	remaining := len(connected.pending) + len(connected.streams)
	connected.mu.Unlock()
	if remaining != 0 {
		test.Fatalf("disconnect leaked %d registrations", remaining)
	}
}

func TestGuestStreamAcceptsFragmentedTunnelBurst(test *testing.T) {
	stream := newGuestStream("vnc", "tunnel_open")
	stream.offer(guestFrame{header: map[string]json.RawMessage{"id": jsonValue(1), "ok": jsonValue(true)}})
	var expected []byte
	for index := range guestQueueFrames * 4 {
		payload := bytes.Repeat([]byte{byte(index)}, 1024)
		expected = append(expected, payload...)
		stream.offer(guestFrame{header: map[string]json.RawMessage{"op": jsonValue("tunnel_data")}, payload: payload})
	}
	stream.offer(guestFrame{header: map[string]json.RawMessage{"op": jsonValue("tunnel_close")}})
	if stream.failure != nil {
		test.Fatalf("small VNC redraw burst disconnected the stream: %v", stream.failure)
	}
	first, _ := stream.next()
	data, _ := stream.next()
	last, _ := stream.next()
	if headerID(first.header) != 1 || !bytes.Equal(data.payload, expected) || headerString(last.header, "op") != "tunnel_close" || stream.queuedBytes != 0 {
		test.Fatal("coalescing changed tunnel bytes, control order, or queue accounting")
	}
}

func TestGuestStreamTunnelBufferRemainsBounded(test *testing.T) {
	stream := newGuestStream("vnc", "tunnel_open")
	for range 3 {
		stream.offer(guestFrame{header: map[string]json.RawMessage{"op": jsonValue("tunnel_data")}, payload: make([]byte, guestStreamPayloadLimit)})
	}
	if status.Code(stream.failure) != codes.ResourceExhausted || stream.queuedBytes > guestStreamBufferBytes {
		test.Fatal("slow tunnel consumer exceeded the byte budget")
	}
}

func TestGuestStreamBackpressureDoesNotBlockCalls(test *testing.T) {
	fixture := newGuestFixture(test)
	guest := dialGuest(test, fixture, "sandbox")
	ctx, cancel := context.WithTimeout(fixture.ctx, 5*time.Second)
	defer cancel()
	stream := openRPCStream(test, fixture, ctx, "tunnel_open")
	request := nextRequest(test, guest)
	guest.reply(test, request, map[string]any{}, nil)
	payload := bytes.Repeat([]byte{'x'}, 64<<10)
	for range guestQueueFrames + 64 {
		guest.send(test, map[string]any{"op": "tunnel_data", "stream": request.Args["stream"]}, payload)
	}
	var streamErr error
	for streamErr == nil {
		_, streamErr = stream.Recv()
	}
	if status.Code(streamErr) != codes.ResourceExhausted {
		test.Fatalf("backpressure: %v", streamErr)
	}
	cleanup := nextRequest(test, guest)
	if cleanup.Op != "tunnel_close" {
		test.Fatalf("backpressure cleanup: %+v", cleanup)
	}
	result := callGuest(fixture, fixture.ctx, "sandbox", "read", map[string]any{}, nil)
	request = nextRequest(test, guest)
	guest.reply(test, request, map[string]any{}, []byte("still connected"))
	if reply := awaitCall(test, result); reply.err != nil || string(reply.reply.Payload) != "still connected" {
		test.Fatalf("slow consumer blocked other calls: %v", reply.err)
	}
}
