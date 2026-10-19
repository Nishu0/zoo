package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/chann44/zoo/node/nodepb"
	"github.com/coder/websocket"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"zoo/gateway/gatewaypb"
)

type replicaFixture struct {
	front         *gatewayServer
	owner         *gatewayServer
	frontResolver *fakePeerResolver
	ownerResolver *fakePeerResolver
	frontClient   gatewaypb.GatewayClient
	ownerClient   gatewaypb.GatewayClient
	node          *nodeFixture
	publicURL     string
	private       *httptest.Server
	ctx           context.Context
	done          chan struct{}
}

func replicaResource(test *testing.T, peers *peerSet, owner, prefix string) string {
	test.Helper()
	for index := range 1000 {
		resource := fmt.Sprintf("%s%d", prefix, index)
		actual, err := peers.Owner(resource)
		if err != nil {
			test.Fatal(err)
		}
		if actual == owner {
			return resource
		}
	}
	test.Fatal("could not find a resource for the replica")
	return ""
}

func newReplicaFixture(test *testing.T) *replicaFixture {
	test.Helper()
	authority := nodeTestCA(test)
	secure, err := authority.serverTLSConfig([]string{"127.0.0.1", "::1"})
	if err != nil {
		test.Fatal(err)
	}
	internalTLS := secure.Clone()
	internalTLS.ClientAuth = tls.NoClientCert
	ownerListener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		test.Fatal(err)
	}
	settings := config{secretsKey: "guest-test-key", grpcPort: ownerListener.Addr().(*net.TCPAddr).Port}
	front, owner := newGatewayServer(settings), newGatewayServer(settings)
	private := httptest.NewUnstartedServer(http.HandlerFunc(owner.serveRelayedGuest))
	private.TLS = internalTLS.Clone()
	private.Config.ErrorLog = log.New(io.Discard, "", 0)
	private.StartTLS()
	settings.peerHTTPPort = private.Listener.Addr().(*net.TCPAddr).Port
	front.settings, owner.settings = settings, settings
	frontPeers, frontResolver := testPeerSet(test, "::1", "127.0.0.1")
	ownerPeers, ownerResolver := testPeerSet(test, "127.0.0.1", "::1")
	front.routes = newReplicaRouter(frontPeers, authority, settings)
	owner.routes = newReplicaRouter(ownerPeers, authority, settings)
	frontStore := &fakeNodeStore{records: make(map[string]nodeRecord)}
	ownerStore := &fakeNodeStore{records: make(map[string]nodeRecord)}
	for _, replica := range []struct {
		service *gatewayServer
		store   *fakeNodeStore
	}{{front, frontStore}, {owner, ownerStore}} {
		replica.service.nodes, err = newNodeHub(replica.store, authority, nodeHubOptions{Version: "1.2.3", Endpoints: []string{"zoo.example:7443"}})
		if err != nil {
			test.Fatal(err)
		}
		replica.service.nodeLinks = &nodeLinkRouter{hub: replica.service.nodes, local: replica.service.routes.peers.LocalIP(),
			owner: replica.service.routes.peers.Owner, client: replica.service.routes.client, key: settings.token("zoo-gateway")}
	}
	frontListener, err := net.Listen("tcp6", net.JoinHostPort("::1", strconv.Itoa(settings.grpcPort)))
	if err != nil {
		test.Fatal(err)
	}
	servers := make([]*grpc.Server, 0, 2)
	clients := make([]gatewaypb.GatewayClient, 0, 2)
	connections := make([]*grpc.ClientConn, 0, 2)
	for _, replica := range []struct {
		service  *gatewayServer
		listener net.Listener
	}{{front, frontListener}, {owner, ownerListener}} {
		server := grpc.NewServer(grpc.Creds(credentials.NewTLS(internalTLS.Clone())),
			grpc.UnaryInterceptor(settings.unaryAuth), grpc.StreamInterceptor(settings.streamAuth))
		gatewaypb.RegisterGatewayServer(server, replica.service)
		go server.Serve(replica.listener)
		connection, err := grpc.NewClient(replica.listener.Addr().String(), grpc.WithTransportCredentials(credentials.NewTLS(&tls.Config{MinVersion: tls.VersionTLS12, RootCAs: front.routes.roots})))
		if err != nil {
			test.Fatal(err)
		}
		servers = append(servers, server)
		connections = append(connections, connection)
		clients = append(clients, gatewaypb.NewGatewayClient(connection))
	}
	nativeListener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		test.Fatal(err)
	}
	nativeServer := grpc.NewServer(grpc.Creds(credentials.NewTLS(secure)))
	nodepb.RegisterNodeServer(nativeServer, front.nodeLinks)
	go nativeServer.Serve(nativeListener)
	public := httptest.NewServer(http.HandlerFunc(front.serveGuest))
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	ctx = metadata.AppendToOutgoingContext(ctx, "authorization", "Bearer "+settings.token("zoo-gateway"))
	done := make(chan struct{})
	test.Cleanup(func() {
		cancel()
		close(done)
		front.guests.close()
		owner.guests.close()
		front.nodes.close()
		owner.nodes.close()
		front.routes.close()
		owner.routes.close()
		for _, connection := range connections {
			connection.Close()
		}
		for _, server := range servers {
			server.Stop()
		}
		nativeServer.Stop()
		public.Close()
		private.Close()
	})
	return &replicaFixture{front: front, owner: owner, frontResolver: frontResolver, ownerResolver: ownerResolver,
		frontClient: clients[0], ownerClient: clients[1], publicURL: "ws" + strings.TrimPrefix(public.URL, "http"), private: private, ctx: ctx, done: done,
		node: &nodeFixture{hub: owner.nodes, store: frontStore, authority: authority, settings: settings, client: clients[1], address: nativeListener.Addr().String(), ctx: ctx}}
}

func (fixture *replicaFixture) guest(test *testing.T, sandboxID string) *fakeGuest {
	test.Helper()
	return dialGuest(test, &guestFixture{service: fixture.owner, client: fixture.ownerClient, ctx: fixture.ctx, done: fixture.done,
		url: fixture.publicURL + "/guest/connect?owner=" + url.QueryEscape(fixture.owner.routes.peers.LocalIP())}, sandboxID)
}

func TestReplicaGuestReconnectAndTLSProxy(test *testing.T) {
	fixture := newReplicaFixture(test)
	sandboxID := replicaResource(test, fixture.front.routes.peers, "127.0.0.1", "sandbox-")
	var dials atomic.Int64
	dial := fixture.front.routes.transport.DialContext
	fixture.front.routes.transport.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
		dials.Add(1)
		if address != net.JoinHostPort("127.0.0.1", strconv.Itoa(fixture.front.settings.peerHTTPPort)) {
			return nil, fmt.Errorf("unexpected proxy destination %s", address)
		}
		return dial(ctx, network, address)
	}
	for _, pin := range []string{"", "http://169.254.169.254/latest/meta-data", "203.0.113.8", "::1", "127.0.0.1:8002"} {
		test.Run("pin="+pin, func(test *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			connection, response, err := websocket.Dial(ctx, fixture.publicURL+"/guest/connect?owner="+url.QueryEscape(pin), &websocket.DialOptions{
				HTTPHeader: http.Header{"Authorization": {guestAuthorization(sandboxID)}, "X-Zoo-Sandbox": {sandboxID}}})
			if err != nil {
				test.Fatal(err)
			}
			defer connection.CloseNow()
			_, _, err = connection.Read(ctx)
			var closed websocket.CloseError
			if !errors.As(err, &closed) || closed.Code != 4001 || closed.Reason != "owner=127.0.0.1" {
				test.Fatalf("expected IP-only owner reconnect, got %v", err)
			}
			if response.Header.Get("Location") != "" || dials.Load() != 0 {
				test.Fatal("invalid owner pin caused a redirect or outbound connection")
			}
		})
	}
	guest := fixture.guest(test, sandboxID)
	guestFixture := &guestFixture{service: fixture.owner, client: fixture.ownerClient, ctx: fixture.ctx, done: fixture.done}
	result := callGuest(guestFixture, fixture.ctx, sandboxID, "exec", map[string]any{"argv": []string{"echo", "relay"}}, []byte{0, 255, 42})
	request := nextRequest(test, guest)
	if request.Op != "exec" || !bytes.Equal(request.Payload, []byte{0, 255, 42}) {
		test.Fatalf("private proxy changed request bytes: %+v", request)
	}
	guest.reply(test, request, map[string]any{"exit_code": 0}, []byte{255, 0, 13, 10})
	reply := awaitCall(test, result)
	if reply.err != nil || !bytes.Equal(reply.reply.GetPayload(), []byte{255, 0, 13, 10}) || dials.Load() != 1 {
		test.Fatalf("same-origin reconnect failed: %v, %v, dials=%d", reply.reply, reply.err, dials.Load())
	}
	fixture.front.guests.mu.Lock()
	attached := len(fixture.front.guests.guests)
	fixture.front.guests.mu.Unlock()
	if attached != 0 {
		test.Fatal("nonowner attached the guest locally")
	}
}

func TestReplicaPrivateGuestAuthenticationAndOwnership(test *testing.T) {
	fixture := newReplicaFixture(test)
	owned := replicaResource(test, fixture.front.routes.peers, "127.0.0.1", "sandbox-")
	nonowned := replicaResource(test, fixture.front.routes.peers, "::1", "sandbox-")
	client := fixture.private.Client()
	for _, scenario := range []struct {
		name    string
		keys    []string
		sandbox string
		code    int
	}{
		{"missing", nil, owned, http.StatusUnauthorized},
		{"wrong", []string{"wrong"}, owned, http.StatusUnauthorized},
		{"bearer-not-raw", []string{"Bearer " + fixture.owner.settings.token("zoo-gateway")}, owned, http.StatusUnauthorized},
		{"duplicate", []string{fixture.owner.settings.token("zoo-gateway"), fixture.owner.settings.token("zoo-gateway")}, owned, http.StatusUnauthorized},
		{"nonowner", []string{fixture.owner.settings.token("zoo-gateway")}, nonowned, http.StatusConflict},
	} {
		test.Run(scenario.name, func(test *testing.T) {
			request, err := http.NewRequestWithContext(fixture.ctx, http.MethodGet, fixture.private.URL+"/guest/connect", nil)
			if err != nil {
				test.Fatal(err)
			}
			request.Header["X-Zoo-Gateway"] = scenario.keys
			request.Header.Set("X-Zoo-Sandbox", scenario.sandbox)
			request.Header.Set("Authorization", guestAuthorization(scenario.sandbox))
			response, err := client.Do(request)
			if err != nil {
				test.Fatal(err)
			}
			defer response.Body.Close()
			if response.StatusCode != scenario.code || response.Header.Get("Location") != "" {
				test.Fatalf("unexpected private response: %s", response.Status)
			}
		})
	}
}

func TestReplicaProxyRejectsUntrustedTLS(test *testing.T) {
	fixture := newReplicaFixture(test)
	fixture.front.routes.transport.TLSClientConfig.RootCAs = x509.NewCertPool()
	sandboxID := replicaResource(test, fixture.front.routes.peers, "127.0.0.1", "sandbox-")
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	connection, response, err := websocket.Dial(ctx, fixture.publicURL+"/guest/connect?owner=127.0.0.1", &websocket.DialOptions{
		HTTPHeader: http.Header{"Authorization": {guestAuthorization(sandboxID)}, "X-Zoo-Sandbox": {sandboxID}}})
	if connection != nil {
		connection.CloseNow()
	}
	if err == nil || response == nil || response.StatusCode != http.StatusServiceUnavailable || response.Header.Get("Location") != "" {
		test.Fatalf("untrusted private TLS was accepted: %v, %v", response, err)
	}
}

func TestReplicaProxyRequiresGatewayAuthentication(test *testing.T) {
	fixture := newReplicaFixture(test)
	fixture.front.routes.settings.secretsKey = "wrong-peer-key"
	sandboxID := replicaResource(test, fixture.front.routes.peers, "127.0.0.1", "sandbox-")
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	connection, response, err := websocket.Dial(ctx, fixture.publicURL+"/guest/connect?owner=127.0.0.1", &websocket.DialOptions{
		HTTPHeader: http.Header{"Authorization": {guestAuthorization(sandboxID)}, "X-Zoo-Sandbox": {sandboxID}}})
	if connection != nil {
		connection.CloseNow()
	}
	if err == nil || response == nil || response.StatusCode != http.StatusUnauthorized || response.Header.Get("Location") != "" {
		test.Fatalf("TLS proxy bypassed gateway authentication: %v, %v", response, err)
	}
}

func TestReplicaRPCOwnershipAndMembership(test *testing.T) {
	fixture := newReplicaFixture(test)
	owned := replicaResource(test, fixture.front.routes.peers, "127.0.0.1", "resource-")
	for _, operation := range []string{"guest", "node", "call", "guest-stream", "node-tunnel"} {
		var trailers metadata.MD
		var err error
		switch operation {
		case "guest":
			_, err = fixture.frontClient.GuestStatus(fixture.ctx, &gatewaypb.GuestStatusRequest{SandboxId: owned}, grpc.Trailer(&trailers))
		case "node":
			_, err = fixture.frontClient.NodeStatus(fixture.ctx, &gatewaypb.NodeStatusRequest{ServerId: owned}, grpc.Trailer(&trailers))
		case "call":
			_, err = fixture.frontClient.GuestCall(fixture.ctx, &gatewaypb.GuestCallRequest{SandboxId: owned, Op: "exec"}, grpc.Trailer(&trailers))
		case "guest-stream":
			stream, opening := fixture.frontClient.GuestStream(fixture.ctx)
			if opening != nil {
				test.Fatal(opening)
			}
			if err := stream.Send(&gatewaypb.Frame{SandboxId: owned}); err != nil {
				test.Fatal(err)
			}
			_, err = stream.Recv()
			trailers = stream.Trailer()
		case "node-tunnel":
			stream, opening := fixture.frontClient.NodeTunnel(metadata.AppendToOutgoingContext(fixture.ctx, "server_id", owned, "target", "ssh"))
			if opening != nil {
				test.Fatal(opening)
			}
			_, err = stream.Recv()
			trailers = stream.Trailer()
		}
		if status.Code(err) != codes.FailedPrecondition || strings.Join(trailers.Get("zoo-owner"), ",") != "127.0.0.1" || strings.Contains(err.Error(), "https://") {
			test.Fatalf("%s returned unsafe ownership hint: %v, %v", operation, err, trailers)
		}
	}
	local := replicaResource(test, fixture.front.routes.peers, "::1", "sandbox-local-")
	localClient, err := fixture.front.routes.client("::1")
	if err != nil {
		test.Fatal(err)
	}
	if _, err := localClient.GuestStatus(fixture.ctx, &gatewaypb.GuestStatusRequest{SandboxId: local}); err != nil {
		test.Fatalf("local authenticated TLS endpoint was not reachable: %v", err)
	}
	if err := fixture.front.routes.requireOwner(fixture.ctx, ""); status.Code(err) != codes.InvalidArgument {
		test.Fatalf("empty resource was accepted: %v", err)
	}
	for _, invalid := range []string{"203.0.113.5", "https://127.0.0.1", "127.0.0.1:7444", ""} {
		if _, err := fixture.front.routes.client(invalid); err == nil {
			test.Fatalf("accepted a nonmember owner %q", invalid)
		}
	}
	first, err := fixture.front.routes.client("127.0.0.1")
	if err != nil {
		test.Fatal(err)
	}
	second, err := fixture.front.routes.client("127.0.0.1")
	if err != nil || first == nil || second == nil || len(fixture.front.routes.channels) != 2 {
		test.Fatal("peer connection was not cached")
	}
	fixture.frontResolver.set("gateway.test", peerAddresses("::1"), nil)
	if err := fixture.front.routes.peers.Refresh(fixture.ctx); err != nil {
		test.Fatal(err)
	}
	fixture.front.routes.closeMoved(fixture.front)
	if _, err := fixture.front.routes.client("127.0.0.1"); err == nil || len(fixture.front.routes.channels) != 1 {
		test.Fatal("removed peer remained dialable or cached")
	}
	fixture.frontResolver.set("gateway.test", nil, errors.New("DNS unavailable"))
	if err := fixture.front.routes.peers.Refresh(fixture.ctx); err == nil {
		test.Fatal("expected failed membership refresh")
	}
	if err := fixture.front.routes.requireOwner(fixture.ctx, owned); status.Code(err) != codes.Unavailable {
		test.Fatalf("used stale ownership after DNS failure: %v", err)
	}
	fixture.front.routes.close()
	fixture.frontResolver.set("gateway.test", peerAddresses("::1"), nil)
	if err := fixture.front.routes.peers.Refresh(fixture.ctx); err != nil {
		test.Fatal(err)
	}
	if _, err := fixture.front.routes.client("::1"); err == nil {
		test.Fatal("closed router created a connection")
	}
}

func TestReplicaRPCRejectsUntrustedTLS(test *testing.T) {
	fixture := newReplicaFixture(test)
	fixture.front.routes.roots = x509.NewCertPool()
	client, err := fixture.front.routes.client("127.0.0.1")
	if err != nil {
		test.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(fixture.ctx, 3*time.Second)
	defer cancel()
	owned := replicaResource(test, fixture.front.routes.peers, "127.0.0.1", "sandbox-")
	if _, err := client.GuestStatus(ctx, &gatewaypb.GuestStatusRequest{SandboxId: owned}); status.Code(err) != codes.Unavailable {
		test.Fatalf("untrusted private RPC TLS was accepted: %v", err)
	}
}

func TestReplicaGuestOwnershipChangeDisconnects(test *testing.T) {
	fixture := newReplicaFixture(test)
	sandboxID := replicaResource(test, fixture.front.routes.peers, "127.0.0.1", "sandbox-")
	guest := fixture.guest(test, sandboxID)
	fixture.ownerResolver.set("gateway.test", peerAddresses("::1"), nil)
	if err := fixture.owner.routes.peers.Refresh(fixture.ctx); err != nil {
		test.Fatal(err)
	}
	fixture.owner.routes.closeMoved(fixture.owner)
	select {
	case <-guest.closed:
	case <-time.After(3 * time.Second):
		test.Fatal("guest remained connected after owner changed")
	}
}

func TestReplicaGuestMembershipFailureIsUnavailable(test *testing.T) {
	fixture := newReplicaFixture(test)
	fixture.frontResolver.set("gateway.test", nil, errors.New("DNS unavailable"))
	if err := fixture.front.routes.peers.Refresh(fixture.ctx); err == nil {
		test.Fatal("expected failed membership refresh")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	connection, response, err := websocket.Dial(ctx, fixture.publicURL+"/guest/connect?owner=127.0.0.1", &websocket.DialOptions{
		HTTPHeader: http.Header{"Authorization": {guestAuthorization("sandbox")}, "X-Zoo-Sandbox": {"sandbox"}}})
	if connection != nil {
		connection.CloseNow()
	}
	if err == nil || response == nil || response.StatusCode != http.StatusServiceUnavailable {
		test.Fatalf("guest was routed using stale membership: %v, %v", response, err)
	}
}
