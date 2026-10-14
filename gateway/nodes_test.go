package main

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"io"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/chann44/zoo/node/nodepb"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"zoo/gateway/gatewaypb"
)

type nodeOnlineEvent struct {
	ServerID string
	Online   bool
}

type fakeNodeStore struct {
	mu           sync.Mutex
	records      map[string]nodeRecord
	hellos       []nodeHelloData
	statuses     []nodeStatusData
	events       []nodeOnlineEvent
	casCount     int
	rejectCAS    bool
	writeError   error
	onlineError  error
	casCommitted chan struct{}
	casRelease   chan struct{}
}

func (store *fakeNodeStore) Lookup(ctx context.Context, nodeID string) (nodeRecord, error) {
	store.mu.Lock()
	defer store.mu.Unlock()
	record, exists := store.records[nodeID]
	if !exists {
		return nodeRecord{}, errNodeNotFound
	}
	return record, nil
}

func (store *fakeNodeStore) WriteHello(ctx context.Context, record nodeRecord, hello nodeHelloData) error {
	store.mu.Lock()
	defer store.mu.Unlock()
	if store.writeError != nil {
		return store.writeError
	}
	store.hellos = append(store.hellos, hello)
	return nil
}

func (store *fakeNodeStore) WriteStatus(ctx context.Context, record nodeRecord, report nodeStatusData) error {
	store.mu.Lock()
	defer store.mu.Unlock()
	if store.writeError != nil {
		return store.writeError
	}
	store.statuses = append(store.statuses, report)
	return nil
}

func (store *fakeNodeStore) SetOnline(ctx context.Context, record nodeRecord, online bool) error {
	store.mu.Lock()
	defer store.mu.Unlock()
	if store.writeError != nil {
		return store.writeError
	}
	if online && store.onlineError != nil {
		return store.onlineError
	}
	store.events = append(store.events, nodeOnlineEvent{record.ServerID, online})
	return nil
}

func (store *fakeNodeStore) CompareAndSwapCertificate(ctx context.Context, nodeID, previous, next string, expiry time.Time) (bool, error) {
	store.mu.Lock()
	defer store.mu.Unlock()
	store.casCount++
	if store.writeError != nil {
		return false, store.writeError
	}
	record, exists := store.records[nodeID]
	if !exists || record.Serial != previous || store.rejectCAS {
		return false, nil
	}
	record.Serial, record.ExpiresAt = next, expiry
	store.records[nodeID] = record
	if store.casCommitted != nil {
		close(store.casCommitted)
		select {
		case <-store.casRelease:
		case <-ctx.Done():
			return false, ctx.Err()
		}
	}
	return true, nil
}

func nodeTestCA(test *testing.T) *nodeAuthority {
	test.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		test.Fatal(err)
	}
	template := &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Zoo node test CA"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(10 * 365 * 24 * time.Hour),
		IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageCRLSign,
	}
	encoded, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		test.Fatal(err)
	}
	certificate, err := x509.ParseCertificate(encoded)
	if err != nil {
		test.Fatal(err)
	}
	authority, err := newNodeAuthority(certificate, key)
	if err != nil {
		test.Fatal(err)
	}
	return authority
}

type nodeFixture struct {
	hub       *nodeHub
	store     *fakeNodeStore
	authority *nodeAuthority
	settings  config
	client    gatewaypb.GatewayClient
	address   string
	ctx       context.Context
}

func newNodeFixture(test *testing.T, options nodeHubOptions) *nodeFixture {
	test.Helper()
	authority := nodeTestCA(test)
	store := &fakeNodeStore{records: make(map[string]nodeRecord)}
	if options.Version == "" {
		options.Version = "1.2.3"
	}
	if len(options.Endpoints) == 0 {
		options.Endpoints = []string{"zoo.example:7443"}
	}
	hub, err := newNodeHub(store, authority, options)
	if err != nil {
		test.Fatal(err)
	}
	secure, err := authority.serverTLSConfig([]string{"127.0.0.1"})
	if err != nil {
		test.Fatal(err)
	}
	nativeListener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		test.Fatal(err)
	}
	nativeServer := grpc.NewServer(grpc.Creds(credentials.NewTLS(secure)))
	nodepb.RegisterNodeServer(nativeServer, &nativeNodeConnect{hub: hub})
	go nativeServer.Serve(nativeListener)
	internalListener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		test.Fatal(err)
	}
	settings := config{secretsKey: "node-test-key"}
	internalServer := grpc.NewServer(grpc.UnaryInterceptor(settings.unaryAuth), grpc.StreamInterceptor(settings.streamAuth))
	gatewaypb.RegisterGatewayServer(internalServer, hub)
	go internalServer.Serve(internalListener)
	connection, err := grpc.NewClient(internalListener.Addr().String(), grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		test.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	ctx = metadata.AppendToOutgoingContext(ctx, "authorization", "Bearer "+settings.token("zoo-gateway"))
	test.Cleanup(func() {
		cancel()
		hub.close()
		connection.Close()
		internalServer.Stop()
		nativeServer.Stop()
		nativeListener.Close()
		internalListener.Close()
	})
	return &nodeFixture{hub: hub, store: store, authority: authority, settings: settings, client: gatewaypb.NewGatewayClient(connection), address: nativeListener.Addr().String(), ctx: ctx}
}

func (fixture *nodeFixture) certificate(test *testing.T, nodeID, osName, arch string) tls.Certificate {
	test.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		test.Fatal(err)
	}
	_, certificate, err := fixture.authority.issue(&key.PublicKey, nodeID, nil)
	if err != nil {
		test.Fatal(err)
	}
	fixture.store.mu.Lock()
	fixture.store.records[nodeID] = nodeRecord{NodeID: nodeID, ServerID: "server-" + nodeID, Serial: certificate.SerialNumber.Text(16), ExpiresAt: certificate.NotAfter, OS: osName, Arch: arch, SSHHostKey: "ssh-ed25519 pinned-host-key"}
	fixture.store.mu.Unlock()
	return tls.Certificate{Certificate: [][]byte{certificate.Raw}, PrivateKey: key, Leaf: certificate}
}

type fakeNode struct {
	stream   grpc.BidiStreamingClient[nodepb.NodeMessage, nodepb.ApiMessage]
	incoming chan *nodepb.ApiMessage
	failures chan error
	cancel   context.CancelFunc
}

func (fixture *nodeFixture) dial(test *testing.T, certificate tls.Certificate) *fakeNode {
	test.Helper()
	roots := x509.NewCertPool()
	roots.AddCert(fixture.authority.certificate)
	secure := &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: roots, Certificates: []tls.Certificate{certificate}}
	connection, err := grpc.NewClient(fixture.address, grpc.WithTransportCredentials(credentials.NewTLS(secure)))
	if err != nil {
		test.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	stream, err := nodepb.NewNodeClient(connection).Connect(ctx)
	if err != nil {
		cancel()
		connection.Close()
		test.Fatal(err)
	}
	fake := &fakeNode{stream: stream, incoming: make(chan *nodepb.ApiMessage, 64), failures: make(chan error, 1), cancel: cancel}
	go func() {
		for {
			message, err := stream.Recv()
			if err != nil {
				fake.failures <- err
				return
			}
			select {
			case fake.incoming <- message:
			case <-ctx.Done():
				return
			}
		}
	}()
	test.Cleanup(func() { cancel(); connection.Close() })
	return fake
}

func (fake *fakeNode) send(test *testing.T, message *nodepb.NodeMessage) {
	test.Helper()
	if err := fake.stream.Send(message); err != nil {
		test.Fatal(err)
	}
}

func (fake *fakeNode) next(test *testing.T) *nodepb.ApiMessage {
	test.Helper()
	select {
	case message := <-fake.incoming:
		return message
	case err := <-fake.failures:
		test.Fatalf("node stream failed: %v", err)
	case <-time.After(3 * time.Second):
		test.Fatal("node received no message")
	}
	return nil
}

func (fake *fakeNode) failure(test *testing.T) error {
	test.Helper()
	select {
	case err := <-fake.failures:
		return err
	case <-time.After(3 * time.Second):
		test.Fatal("node stream remained open")
	}
	return nil
}

func (fixture *nodeFixture) connect(test *testing.T, certificate tls.Certificate, osName, arch, version string) *fakeNode {
	test.Helper()
	fake := fixture.dial(test, certificate)
	driver := "zoovm"
	if osName == "windows" {
		driver = "hyperv"
	}
	fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Hello{Hello: &nodepb.Hello{Version: version, Os: osName, Arch: arch, Hostname: "test-host", Targets: []string{"ssh"}, Drivers: []*nodepb.Driver{{Name: driver, Available: true, Detail: "ready"}}}}})
	welcome := fake.next(test).GetWelcome()
	if welcome == nil || welcome.ApiVersion != fixture.hub.options.Version || welcome.StatusSeconds != fixture.hub.options.StatusSeconds || !slices.Equal(welcome.Endpoints, fixture.hub.options.Endpoints) {
		test.Fatalf("unexpected welcome: %v", welcome)
	}
	fixture.waitOnline(test, "server-"+certificate.Leaf.Subject.CommonName, true)
	return fake
}

func (fixture *nodeFixture) waitOnline(test *testing.T, serverID string, expected bool) *gatewaypb.NodeStatusReply {
	test.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		reply, err := fixture.client.NodeStatus(fixture.ctx, &gatewaypb.NodeStatusRequest{ServerId: serverID})
		if err != nil {
			test.Fatal(err)
		}
		if reply.Online == expected {
			return reply
		}
		time.Sleep(5 * time.Millisecond)
	}
	test.Fatalf("node online did not become %v", expected)
	return nil
}

func (fixture *nodeFixture) tunnel(test *testing.T, serverID, target string) grpc.BidiStreamingClient[gatewaypb.Bytes, gatewaypb.Bytes] {
	test.Helper()
	ctx := metadata.AppendToOutgoingContext(fixture.ctx, "server_id", serverID, "target", target)
	stream, err := fixture.client.NodeTunnel(ctx)
	if err != nil {
		test.Fatal(err)
	}
	return stream
}

func TestNodeConnectReportsAndTunnelFlow(test *testing.T) {
	for _, platform := range []struct{ osName, arch string }{{"darwin", "arm64"}, {"windows", "amd64"}} {
		test.Run(platform.osName, func(test *testing.T) {
			fixture := newNodeFixture(test, nodeHubOptions{})
			certificate := fixture.certificate(test, "node-1", platform.osName, platform.arch)
			fake := fixture.connect(test, certificate, platform.osName, platform.arch, "1.2.3")
			fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Status{Status: &nodepb.Status{Cpus: 8, MemoryTotal: 32 << 30, MemoryAvailable: 20 << 30, DiskTotal: 100 << 30, DiskFree: 90 << 30, Load: 0.25, Sandboxes: []string{"sandbox-1"}, Checks: []*nodepb.Check{{Name: "base", Ok: true, Detail: "ready"}}}}})
			var report nodeReport
			deadline := time.Now().Add(3 * time.Second)
			for report.CPUs != 8 && time.Now().Before(deadline) {
				reply := fixture.waitOnline(test, "server-node-1", true)
				if err := json.Unmarshal(reply.ReportJson, &report); err != nil {
					test.Fatal(err)
				}
				time.Sleep(time.Millisecond)
			}
			if report.CPUs != 8 || report.MemoryAvailable != 20<<30 || report.OS != platform.osName || report.NodeID != "node-1" || !slices.Equal(report.Targets, []string{"ssh"}) || len(report.Checks) != 1 || !report.Checks[0].OK {
				test.Fatalf("incomplete cached report: %#v", report)
			}
			fixture.store.mu.Lock()
			persisted := len(fixture.store.hellos) == 1 && len(fixture.store.statuses) == 1 && fixture.store.statuses[0].CPUs == 8
			fixture.store.mu.Unlock()
			if !persisted {
				test.Fatal("hello and status were not persisted")
			}
			stream := fixture.tunnel(test, "server-node-1", "ssh")
			open := fake.next(test).GetOpen()
			if open == nil || open.Id == 0 || open.Target != "ssh" {
				test.Fatalf("invalid tunnel open: %v", open)
			}
			outbound := bytes.Repeat([]byte{0, 255, 13, 10}, nodeTunnelChunk/4+9)
			if err := stream.Send(&gatewaypb.Bytes{Data: outbound}); err != nil {
				test.Fatal(err)
			}
			var forwarded []byte
			for len(forwarded) < len(outbound) {
				data := fake.next(test).GetData()
				if data == nil || data.Id != open.Id || len(data.Data) > nodeTunnelChunk {
					test.Fatalf("invalid tunnel data: %v", data)
				}
				forwarded = append(forwarded, data.Data...)
			}
			if !bytes.Equal(forwarded, outbound) {
				test.Fatal("gateway changed outbound protocol bytes")
			}
			for _, response := range [][]byte{{0, 255, 42}, []byte("SSH-2.0-fake\r\n")} {
				fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Data{Data: &nodepb.TunnelData{Id: open.Id, Data: response}}})
				actual, err := stream.Recv()
				if err != nil || !bytes.Equal(actual.GetData(), response) {
					test.Fatalf("inbound tunnel bytes changed: %v, %v", actual, err)
				}
			}
			if err := stream.Send(&gatewaypb.Bytes{Data: []byte("still open")}); err != nil {
				test.Fatal(err)
			}
			if data := fake.next(test).GetData(); data == nil || string(data.Data) != "still open" {
				test.Fatal("tunnel did not stay open")
			}
			fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Data{Data: &nodepb.TunnelData{Id: open.Id, Data: []byte("last bytes")}}})
			fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Close{Close: &nodepb.TunnelClose{Id: open.Id}}})
			actual, err := stream.Recv()
			if err != nil || string(actual.GetData()) != "last bytes" {
				test.Fatalf("data queued before close was lost: %v, %v", actual, err)
			}
			if _, err := stream.Recv(); !errors.Is(err, io.EOF) {
				test.Fatalf("remote close did not produce EOF: %v", err)
			}
			second := fixture.tunnel(test, "server-node-1", "ssh")
			nextOpen := fake.next(test).GetOpen()
			if nextOpen == nil || nextOpen.Id == open.Id {
				test.Fatal("tunnel IDs were reused")
			}
			if err := second.CloseSend(); err != nil {
				test.Fatal(err)
			}
			closed := fake.next(test).GetClose()
			if closed == nil || closed.Id != nextOpen.Id {
				test.Fatal("gateway close did not reach the node")
			}
			if _, err := second.Recv(); !errors.Is(err, io.EOF) {
				test.Fatalf("gateway close did not produce EOF: %v", err)
			}
			fake.cancel()
			last := fixture.waitOnline(test, "server-node-1", false)
			if len(last.ReportJson) == 0 {
				test.Fatal("disconnect lost the last report")
			}
		})
	}
}

func TestNodeConnectRejectsInvalidRegistrationAndHello(test *testing.T) {
	for _, invalid := range []string{"unknown", "serial", "expiry", "os", "arch", "target", "first", "duplicate"} {
		test.Run(invalid, func(test *testing.T) {
			fixture := newNodeFixture(test, nodeHubOptions{})
			certificate := fixture.certificate(test, "node-1", "darwin", "arm64")
			fixture.store.mu.Lock()
			record := fixture.store.records["node-1"]
			switch invalid {
			case "unknown":
				delete(fixture.store.records, "node-1")
			case "serial":
				record.Serial = "replaced"
				fixture.store.records["node-1"] = record
			case "expiry":
				record.ExpiresAt = time.Now().Add(-time.Minute)
				fixture.store.records["node-1"] = record
			}
			fixture.store.mu.Unlock()
			fake := fixture.dial(test, certificate)
			hello := &nodepb.Hello{Version: "1.2.3", Os: "darwin", Arch: "arm64", Targets: []string{"ssh"}}
			switch invalid {
			case "os":
				hello.Os = "windows"
			case "arch":
				hello.Arch = "amd64"
			case "target":
				hello.Targets = []string{"docker"}
			}
			message := &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Hello{Hello: hello}}
			if invalid == "first" {
				message = &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Status{Status: &nodepb.Status{}}}
			}
			fake.stream.Send(message)
			if invalid == "duplicate" {
				if fake.next(test).GetWelcome() == nil {
					test.Fatal("missing welcome")
				}
				fake.send(test, message)
			}
			failure := fake.failure(test)
			expected := codes.InvalidArgument
			if invalid == "unknown" || invalid == "serial" || invalid == "expiry" {
				expected = codes.Unauthenticated
			}
			if status.Code(failure) != expected {
				test.Fatalf("unexpected rejection: %v", failure)
			}
			fixture.waitOnline(test, "server-node-1", false)
		})
	}
}

func TestNodeMTLSAndInternalAuthentication(test *testing.T) {
	fixture := newNodeFixture(test, nodeHubOptions{})
	certificate := fixture.certificate(test, "node-1", "darwin", "arm64")
	otherCA := nodeTestCA(test)
	_, foreign, err := otherCA.issue(certificate.PrivateKey.(*ecdsa.PrivateKey).Public().(*ecdsa.PublicKey), "node-1", nil)
	if err != nil {
		test.Fatal(err)
	}
	certificate.Certificate, certificate.Leaf = [][]byte{foreign.Raw}, foreign
	for _, certificates := range [][]tls.Certificate{{certificate}, nil} {
		roots := x509.NewCertPool()
		roots.AddCert(fixture.authority.certificate)
		secure := &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: roots, Certificates: certificates}
		connection, err := grpc.NewClient(fixture.address, grpc.WithTransportCredentials(credentials.NewTLS(secure)))
		if err != nil {
			test.Fatal(err)
		}
		nativeStream, failure := nodepb.NewNodeClient(connection).Connect(fixture.ctx)
		if failure == nil {
			nativeStream.Send(&nodepb.NodeMessage{Body: &nodepb.NodeMessage_Hello{Hello: &nodepb.Hello{Os: "darwin", Arch: "arm64"}}})
			_, failure = nativeStream.Recv()
		}
		connection.Close()
		if failure == nil {
			test.Fatal("a missing or foreign CA certificate was accepted")
		}
	}
	if _, err := fixture.client.NodeStatus(context.Background(), &gatewaypb.NodeStatusRequest{ServerId: "server-node-1"}); status.Code(err) != codes.Unauthenticated {
		test.Fatalf("internal node status accepted missing authentication: %v", err)
	}
	stream := fixture.tunnel(test, "server-node-1", "docker")
	if _, err := stream.Recv(); status.Code(err) != codes.InvalidArgument {
		test.Fatalf("non-SSH tunnel accepted: %v", err)
	}
	stream = fixture.tunnel(test, "server-node-1", "ssh")
	if _, err := stream.Recv(); status.Code(err) != codes.Unavailable {
		test.Fatalf("offline node accepted: %v", err)
	}
	fixture.store.mu.Lock()
	defer fixture.store.mu.Unlock()
	if len(fixture.store.hellos) != 0 || len(fixture.store.events) != 0 {
		test.Fatal("unauthenticated node wrote reports or online events")
	}
}

func TestNodeStreamsMatchingBuildWithSHA256(test *testing.T) {
	for _, platform := range []struct{ osName, arch, suffix string }{{"darwin", "arm64", ""}, {"windows", "amd64", ".exe"}} {
		test.Run(platform.osName, func(test *testing.T) {
			directory := test.TempDir()
			build := bytes.Repeat([]byte{0, 255, 1, 2, 3}, nodeUpdateChunk/5*2+11)
			if err := os.WriteFile(filepath.Join(directory, "VERSION"), []byte("1.2.3\n"), 0600); err != nil {
				test.Fatal(err)
			}
			name := "zoo-node-" + platform.osName + "-" + platform.arch + platform.suffix
			if err := os.WriteFile(filepath.Join(directory, name), build, 0600); err != nil {
				test.Fatal(err)
			}
			fixture := newNodeFixture(test, nodeHubOptions{DistDir: directory})
			certificate := fixture.certificate(test, "node-1", platform.osName, platform.arch)
			fake := fixture.connect(test, certificate, platform.osName, platform.arch, "1.0.0")
			digest := sha256.Sum256(build)
			var received []byte
			for {
				chunk := fake.next(test).GetUpdate()
				if chunk == nil || chunk.Version != "1.2.3" || chunk.Size != uint64(len(build)) || chunk.Sha256 != hex.EncodeToString(digest[:]) || len(chunk.Data) > nodeUpdateChunk {
					test.Fatalf("incorrect update metadata: %v", chunk)
				}
				received = append(received, chunk.Data...)
				if chunk.Last {
					break
				}
			}
			if !bytes.Equal(received, build) {
				test.Fatal("update bytes changed")
			}
		})
	}
}

func TestNodeUpdateArtifactRequirements(test *testing.T) {
	for _, scenario := range []struct{ name, version, built string }{
		{"current", "1.2.3", "1.2.3"}, {"dev", "dev", "1.2.3"}, {"empty", "", "1.2.3"},
		{"wrong-version", "1.0.0", "9.9.9"}, {"missing-version", "1.0.0", ""}, {"missing-build", "1.0.0", "1.2.3"},
	} {
		test.Run(scenario.name, func(test *testing.T) {
			directory := test.TempDir()
			if scenario.built != "" {
				if err := os.WriteFile(filepath.Join(directory, "VERSION"), []byte(scenario.built), 0600); err != nil {
					test.Fatal(err)
				}
			}
			fixture := newNodeFixture(test, nodeHubOptions{DistDir: directory})
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			session := &nodeSession{ctx: ctx, cancel: cancel, hello: nodeHelloData{Version: scenario.version, OS: "darwin", Arch: "arm64"}, out: make(chan *nodepb.ApiMessage, 4)}
			err := fixture.hub.sendUpdate(session)
			if scenario.name == "current" || scenario.name == "dev" || scenario.name == "empty" {
				if err != nil {
					test.Fatal(err)
				}
			} else if status.Code(err) != codes.FailedPrecondition {
				test.Fatalf("unmatched update artifacts did not fail: %v", err)
			}
			if len(session.out) != 0 {
				test.Fatal("an unmatched or development build was streamed")
			}
		})
	}
}

func TestNodeRejectsUnsupportedPlatformBuildPairs(test *testing.T) {
	for _, platform := range []struct{ osName, arch string }{{"darwin", "amd64"}, {"windows", "arm64"}} {
		test.Run(platform.osName, func(test *testing.T) {
			fixture := newNodeFixture(test, nodeHubOptions{})
			certificate := fixture.certificate(test, "node-1", platform.osName, platform.arch)
			fake := fixture.dial(test, certificate)
			fake.stream.Send(&nodepb.NodeMessage{Body: &nodepb.NodeMessage_Hello{Hello: &nodepb.Hello{Version: "1.2.3", Os: platform.osName, Arch: platform.arch, Targets: []string{"ssh"}}}})
			if err := fake.failure(test); status.Code(err) != codes.Unimplemented {
				test.Fatalf("unsupported node platform was accepted: %v", err)
			}
			fixture.waitOnline(test, "server-node-1", false)
		})
	}
}

func TestNodeUpdateMismatchClosesNativeConnection(test *testing.T) {
	directory := test.TempDir()
	if err := os.WriteFile(filepath.Join(directory, "VERSION"), []byte("different-release"), 0600); err != nil {
		test.Fatal(err)
	}
	fixture := newNodeFixture(test, nodeHubOptions{DistDir: directory})
	certificate := fixture.certificate(test, "node-1", "darwin", "arm64")
	fake := fixture.dial(test, certificate)
	fake.stream.Send(&nodepb.NodeMessage{Body: &nodepb.NodeMessage_Hello{Hello: &nodepb.Hello{Version: "1.0.0", Os: "darwin", Arch: "arm64", Targets: []string{"ssh"}}}})
	if err := fake.failure(test); status.Code(err) != codes.FailedPrecondition {
		test.Fatalf("native stream hid mismatched update artifacts: %v", err)
	}
	fixture.waitOnline(test, "server-node-1", false)
}

func nodeTestCSR(test *testing.T, key any) []byte {
	test.Helper()
	encoded, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{Subject: pkix.Name{CommonName: "untrusted-requested-name"}, DNSNames: []string{"untrusted.example"}}, key)
	if err != nil {
		test.Fatal(err)
	}
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: encoded})
}

func TestNodeRenewalValidatesCSRAndUpdatesSerialWithCAS(test *testing.T) {
	fixture := newNodeFixture(test, nodeHubOptions{})
	certificate := fixture.certificate(test, "node-1", "darwin", "arm64")
	fake := fixture.connect(test, certificate, "darwin", "arm64", "1.2.3")
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		test.Fatal(err)
	}
	valid := nodeTestCSR(test, key)
	block, _ := pem.Decode(valid)
	corrupted := slices.Clone(block.Bytes)
	corrupted[len(corrupted)-1] ^= 1
	rsaKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		test.Fatal(err)
	}
	for _, invalid := range [][]byte{[]byte("not a CSR"), pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: corrupted}), nodeTestCSR(test, rsaKey)} {
		fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Renew{Renew: &nodepb.Renew{Csr: invalid}}})
		reply := fake.next(test).GetRenewal()
		if reply == nil || reply.Error == "" || len(reply.Certificate) != 0 {
			test.Fatalf("invalid CSR was accepted: %v", reply)
		}
	}
	fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Renew{Renew: &nodepb.Renew{Csr: valid}}})
	reply := fake.next(test).GetRenewal()
	if reply == nil || reply.Error != "" {
		test.Fatalf("renewal failed: %v", reply)
	}
	block, _ = pem.Decode(reply.Certificate)
	if block == nil {
		test.Fatal("renewal was not a PEM certificate")
	}
	renewed, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		test.Fatal(err)
	}
	if renewed.Subject.CommonName != "node-1" || len(renewed.DNSNames) != 0 || renewed.SignatureAlgorithm != x509.ECDSAWithSHA256 || !key.PublicKey.Equal(renewed.PublicKey) {
		test.Fatalf("renewal trusted the CSR identity or used the wrong key: %#v", renewed)
	}
	roots := x509.NewCertPool()
	roots.AddCert(fixture.authority.certificate)
	if _, err := renewed.Verify(x509.VerifyOptions{Roots: roots, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}}); err != nil {
		test.Fatal(err)
	}
	fixture.store.mu.Lock()
	stored := fixture.store.records["node-1"]
	count := fixture.store.casCount
	fixture.store.mu.Unlock()
	if count != 1 || stored.Serial == certificate.Leaf.SerialNumber.Text(16) || stored.Serial != renewed.SerialNumber.Text(16) || !stored.ExpiresAt.Equal(renewed.NotAfter) {
		test.Fatal("renewal did not atomically replace the stored certificate")
	}
	fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Status{Status: &nodepb.Status{Cpus: 4}}})
	fixture.waitOnline(test, "server-node-1", true)
	old := fixture.dial(test, certificate)
	old.stream.Send(&nodepb.NodeMessage{Body: &nodepb.NodeMessage_Hello{Hello: &nodepb.Hello{Version: "1.2.3", Os: "darwin", Arch: "arm64"}}})
	if err := old.failure(test); status.Code(err) != codes.Unauthenticated {
		test.Fatalf("replaced certificate reconnected: %v", err)
	}
	newPair := tls.Certificate{Certificate: [][]byte{renewed.Raw}, PrivateKey: key, Leaf: renewed}
	fixture.connect(test, newPair, "darwin", "arm64", "1.2.3")
	if err := fake.failure(test); status.Code(err) != codes.Unavailable {
		test.Fatalf("renewed connection did not replace the old stream: %v", err)
	}
}

func TestNodeRenewalCASFailureDoesNotIssueCertificate(test *testing.T) {
	fixture := newNodeFixture(test, nodeHubOptions{})
	certificate := fixture.certificate(test, "node-1", "windows", "amd64")
	fake := fixture.connect(test, certificate, "windows", "amd64", "1.2.3")
	fixture.store.mu.Lock()
	fixture.store.rejectCAS = true
	fixture.store.mu.Unlock()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		test.Fatal(err)
	}
	fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Renew{Renew: &nodepb.Renew{Csr: nodeTestCSR(test, key)}}})
	reply := fake.next(test).GetRenewal()
	if reply == nil || len(reply.Certificate) != 0 || !strings.Contains(reply.Error, "replaced") {
		test.Fatalf("failed CAS leaked a certificate: %v", reply)
	}
	fixture.store.mu.Lock()
	defer fixture.store.mu.Unlock()
	if fixture.store.records["node-1"].Serial != certificate.Leaf.SerialNumber.Text(16) || fixture.store.casCount != 1 {
		test.Fatal("failed CAS changed registration")
	}
}

func TestNodeReplacementRevocationAndTunnelCleanup(test *testing.T) {
	fixture := newNodeFixture(test, nodeHubOptions{})
	certificate := fixture.certificate(test, "node-1", "darwin", "arm64")
	first := fixture.connect(test, certificate, "darwin", "arm64", "1.2.3")
	tunnel := fixture.tunnel(test, "server-node-1", "ssh")
	if first.next(test).GetOpen() == nil {
		test.Fatal("missing tunnel open")
	}
	second := fixture.connect(test, certificate, "darwin", "arm64", "1.2.3")
	if err := first.failure(test); status.Code(err) != codes.Unavailable {
		test.Fatalf("old connection was not closed: %v", err)
	}
	if _, err := tunnel.Recv(); status.Code(err) != codes.Unavailable {
		test.Fatalf("replacement left the old tunnel open: %v", err)
	}
	fixture.waitOnline(test, "server-node-1", true)
	fixture.store.mu.Lock()
	if !slices.Equal(fixture.store.events, []nodeOnlineEvent{{"server-node-1", true}, {"server-node-1", true}}) {
		fixture.store.mu.Unlock()
		test.Fatal("old detach emitted an offline event for the new connection")
	}
	record := fixture.store.records["node-1"]
	record.Serial = "revoked"
	fixture.store.records["node-1"] = record
	fixture.store.mu.Unlock()
	second.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Status{Status: &nodepb.Status{}}})
	if err := second.failure(test); status.Code(err) != codes.Unauthenticated {
		test.Fatalf("revoked stream kept reporting: %v", err)
	}
	fixture.waitOnline(test, "server-node-1", false)
}

func TestNodeSlowTunnelDoesNotBlockOtherTunnels(test *testing.T) {
	fixture := newNodeFixture(test, nodeHubOptions{})
	certificate := fixture.certificate(test, "node-1", "darwin", "arm64")
	fake := fixture.connect(test, certificate, "darwin", "arm64", "1.2.3")
	fixture.tunnel(test, "server-node-1", "ssh")
	open := fake.next(test).GetOpen()
	if open == nil {
		test.Fatal("missing tunnel open")
	}
	for range nodeQueueSize * 5 {
		fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Data{Data: &nodepb.TunnelData{Id: open.Id, Data: bytes.Repeat([]byte{42}, nodeTunnelChunk)}}})
	}
	closed := fake.next(test).GetClose()
	if closed == nil || closed.Id != open.Id || !strings.Contains(closed.Error, "slow") {
		test.Fatalf("slow tunnel did not release node flow: %v", closed)
	}
	fast := fixture.tunnel(test, "server-node-1", "ssh")
	other := fake.next(test).GetOpen()
	if other == nil {
		test.Fatal("healthy tunnel could not open")
	}
	fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Data{Data: &nodepb.TunnelData{Id: other.Id, Data: []byte("healthy")}}})
	response, err := fast.Recv()
	if err != nil || string(response.GetData()) != "healthy" {
		test.Fatalf("slow reader blocked an unrelated tunnel: %v, %v", response, err)
	}
	fixture.hub.close()
	if _, err := fast.Recv(); status.Code(err) != codes.Unavailable {
		test.Fatalf("shutdown left a tunnel open: %v", err)
	}
	fixture.waitOnline(test, "server-node-1", false)
}

func TestNodeStoreFailureDoesNotAcceptAConnection(test *testing.T) {
	for _, operation := range []string{"hello", "online"} {
		test.Run(operation, func(test *testing.T) {
			fixture := newNodeFixture(test, nodeHubOptions{})
			certificate := fixture.certificate(test, "node-1", "darwin", "arm64")
			fixture.store.mu.Lock()
			if operation == "hello" {
				fixture.store.writeError = errors.New("store unavailable")
			} else {
				fixture.store.onlineError = errors.New("event persistence unavailable")
			}
			fixture.store.mu.Unlock()
			fake := fixture.dial(test, certificate)
			fake.stream.Send(&nodepb.NodeMessage{Body: &nodepb.NodeMessage_Hello{Hello: &nodepb.Hello{Version: "1.2.3", Os: "darwin", Arch: "arm64"}}})
			if err := fake.failure(test); status.Code(err) != codes.Unavailable {
				test.Fatalf("store failure did not fail the connection: %v", err)
			}
			select {
			case message := <-fake.incoming:
				test.Fatalf("failed registration sent a welcome: %v", message)
			default:
			}
			fixture.waitOnline(test, "server-node-1", false)
		})
	}
}

func TestNodeRevocationClosesSilentStreamAndTunnel(test *testing.T) {
	fixture := newNodeFixture(test, nodeHubOptions{StatusSeconds: 1})
	certificate := fixture.certificate(test, "node-1", "darwin", "arm64")
	fake := fixture.connect(test, certificate, "darwin", "arm64", "1.2.3")
	tunnel := fixture.tunnel(test, "server-node-1", "ssh")
	if fake.next(test).GetOpen() == nil {
		test.Fatal("missing tunnel open")
	}
	fixture.store.mu.Lock()
	record := fixture.store.records["node-1"]
	record.Serial = "revoked"
	fixture.store.records["node-1"] = record
	fixture.store.mu.Unlock()
	if err := fake.failure(test); status.Code(err) != codes.Unauthenticated {
		test.Fatalf("silent revoked stream remained authorized: %v", err)
	}
	if _, err := tunnel.Recv(); status.Code(err) != codes.Unavailable {
		test.Fatalf("revocation did not close an existing tunnel: %v", err)
	}
	fixture.waitOnline(test, "server-node-1", false)
}

func TestNodeFailedStatusWriteDisconnectsWithoutCaching(test *testing.T) {
	fixture := newNodeFixture(test, nodeHubOptions{})
	certificate := fixture.certificate(test, "node-1", "darwin", "arm64")
	fake := fixture.connect(test, certificate, "darwin", "arm64", "1.2.3")
	fixture.store.mu.Lock()
	fixture.store.writeError = errors.New("status persistence unavailable")
	fixture.store.mu.Unlock()
	fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Status{Status: &nodepb.Status{Cpus: 99}}})
	if err := fake.failure(test); status.Code(err) != codes.Unavailable {
		test.Fatalf("failed status write left node online: %v", err)
	}
	last := fixture.waitOnline(test, "server-node-1", false)
	var report nodeReport
	if err := json.Unmarshal(last.ReportJson, &report); err != nil {
		test.Fatal(err)
	}
	if report.CPUs != 0 {
		test.Fatal("unpersisted status reached the cache")
	}
}

func TestNodeTunnelAuthorizationWaitsForRenewalCommit(test *testing.T) {
	fixture := newNodeFixture(test, nodeHubOptions{})
	certificate := fixture.certificate(test, "node-1", "darwin", "arm64")
	fake := fixture.connect(test, certificate, "darwin", "arm64", "1.2.3")
	committed, release := make(chan struct{}), make(chan struct{})
	fixture.store.mu.Lock()
	fixture.store.casCommitted, fixture.store.casRelease = committed, release
	fixture.store.mu.Unlock()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		test.Fatal(err)
	}
	fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Renew{Renew: &nodepb.Renew{Csr: nodeTestCSR(test, key)}}})
	select {
	case <-committed:
	case <-time.After(3 * time.Second):
		test.Fatal("renewal did not reach the certificate CAS")
	}
	tunnel := fixture.tunnel(test, "server-node-1", "ssh")
	select {
	case message := <-fake.incoming:
		test.Fatalf("renewal returned before its store write completed: %v", message)
	case <-time.After(50 * time.Millisecond):
	}
	close(release)
	var open *nodepb.TunnelOpen
	var renewal *nodepb.Renewal
	for range 2 {
		message := fake.next(test)
		if message.GetOpen() != nil {
			open = message.GetOpen()
		}
		if message.GetRenewal() != nil {
			renewal = message.GetRenewal()
		}
	}
	if open == nil || renewal == nil || renewal.Error != "" || len(renewal.Certificate) == 0 {
		test.Fatalf("concurrent tunnel mistook renewal for revocation: %v, %v", open, renewal)
	}
	fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Data{Data: &nodepb.TunnelData{Id: open.Id, Data: []byte("renewed tunnel")}}})
	response, err := tunnel.Recv()
	if err != nil || string(response.GetData()) != "renewed tunnel" {
		test.Fatalf("tunnel after concurrent renewal failed: %v, %v", response, err)
	}
}
