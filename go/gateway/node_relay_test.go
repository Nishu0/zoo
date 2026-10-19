package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/chann44/zoo/node/nodepb"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
	"zoo/gateway/gatewaypb"
)

func (fixture *replicaFixture) certificate(test *testing.T, osName, arch string) tls.Certificate {
	test.Helper()
	serverID := replicaResource(test, fixture.front.routes.peers, "127.0.0.1", "server-relay-")
	nodeID := strings.TrimPrefix(serverID, "server-")
	certificate := fixture.node.certificate(test, nodeID, osName, arch)
	fixture.node.store.mu.Lock()
	record := fixture.node.store.records[nodeID]
	fixture.node.store.mu.Unlock()
	store := fixture.owner.nodes.store.(*fakeNodeStore)
	store.mu.Lock()
	store.records[nodeID] = record
	store.mu.Unlock()
	return certificate
}

func TestNodeRelayNativeMTLSReportsAndSSHTunnel(test *testing.T) {
	for _, platform := range []struct{ osName, arch string }{{"darwin", "arm64"}, {"windows", "amd64"}} {
		test.Run(platform.osName, func(test *testing.T) {
			fixture := newReplicaFixture(test)
			certificate := fixture.certificate(test, platform.osName, platform.arch)
			serverID := "server-" + certificate.Leaf.Subject.CommonName
			fake := fixture.node.connect(test, certificate, platform.osName, platform.arch, "1.2.3")
			fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Status{Status: &nodepb.Status{Cpus: 8, MemoryTotal: 32 << 30, MemoryAvailable: 20 << 30, Sandboxes: []string{"sandbox-relayed"}}}})
			var report nodeReport
			deadline := time.Now().Add(3 * time.Second)
			for time.Now().Before(deadline) {
				reply := fixture.node.waitOnline(test, serverID, true)
				if err := json.Unmarshal(reply.ReportJson, &report); err != nil {
					test.Fatal(err)
				}
				if report.CPUs == 8 {
					break
				}
				time.Sleep(time.Millisecond)
			}
			if report.CPUs != 8 || report.MemoryAvailable != 20<<30 || report.NodeID != certificate.Leaf.Subject.CommonName || report.OS != platform.osName {
				test.Fatalf("owner lost original node identity or status: %#v", report)
			}
			ownerStore := fixture.owner.nodes.store.(*fakeNodeStore)
			ownerStore.mu.Lock()
			persisted := len(ownerStore.hellos) == 1 && len(ownerStore.statuses) == 1 && len(ownerStore.events) == 1 && ownerStore.events[0] == (nodeOnlineEvent{serverID, true})
			ownerStore.mu.Unlock()
			fixture.node.store.mu.Lock()
			frontWrites := len(fixture.node.store.hellos) + len(fixture.node.store.statuses) + len(fixture.node.store.events)
			fixture.node.store.mu.Unlock()
			if !persisted || frontWrites != 0 {
				test.Fatal("relay attached or wrote node state on the nonowner")
			}
			tunnel := fixture.node.tunnel(test, serverID, "ssh")
			open := fake.next(test).GetOpen()
			if open == nil || open.Target != "ssh" || open.Id == 0 {
				test.Fatalf("invalid SSH tunnel open: %v", open)
			}
			outbound := bytes.Repeat([]byte{0, 255, 13, 10}, nodeTunnelChunk/4+3)
			if err := tunnel.Send(&gatewaypb.Bytes{Data: outbound}); err != nil {
				test.Fatal(err)
			}
			var received []byte
			for len(received) < len(outbound) {
				data := fake.next(test).GetData()
				if data == nil || data.Id != open.Id || len(data.Data) > nodeTunnelChunk {
					test.Fatalf("invalid relayed tunnel chunk: %v", data)
				}
				received = append(received, data.Data...)
			}
			if !bytes.Equal(received, outbound) {
				test.Fatal("node relay changed SSH protocol bytes")
			}
			response := []byte("SSH-2.0-fake\r\n\x00\xff")
			fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Data{Data: &nodepb.TunnelData{Id: open.Id, Data: response}}})
			actual, err := tunnel.Recv()
			if err != nil || !bytes.Equal(actual.GetData(), response) {
				test.Fatalf("relay changed return bytes: %v, %v", actual, err)
			}
			if err := tunnel.Send(&gatewaypb.Bytes{Data: []byte("still open")}); err != nil {
				test.Fatal(err)
			}
			if data := fake.next(test).GetData(); data == nil || string(data.Data) != "still open" {
				test.Fatal("relayed SSH tunnel did not remain open")
			}
			if err := tunnel.CloseSend(); err != nil {
				test.Fatal(err)
			}
			if closed := fake.next(test).GetClose(); closed == nil || closed.Id != open.Id {
				test.Fatalf("tunnel close did not reach the original node: %v", closed)
			}
			if _, err := tunnel.Recv(); !errors.Is(err, io.EOF) {
				test.Fatalf("relayed tunnel did not terminate: %v", err)
			}
			fake.cancel()
			fixture.node.waitOnline(test, serverID, false)
		})
	}
}

func TestNodeRelayOwnerIndependentlyChecksRegistration(test *testing.T) {
	for _, invalid := range []string{"unknown-cn", "node-id", "serial", "expiry", "server-id", "host-key", "server-owner", "hash-owner", "membership"} {
		test.Run(invalid, func(test *testing.T) {
			fixture := newReplicaFixture(test)
			certificate := fixture.certificate(test, "darwin", "arm64")
			nodeID := certificate.Leaf.Subject.CommonName
			store := fixture.owner.nodes.store.(*fakeNodeStore)
			expected := codes.Unauthenticated
			store.mu.Lock()
			record := store.records[nodeID]
			switch invalid {
			case "unknown-cn":
				delete(store.records, nodeID)
			case "node-id":
				record.NodeID = "another-node"
			case "serial":
				record.Serial = "revoked"
			case "expiry":
				record.ExpiresAt = time.Now().Add(-time.Minute)
			case "server-id":
				record.ServerID = ""
			case "host-key":
				record.SSHHostKey = ""
			case "server-owner":
				record.ServerID = replicaResource(test, fixture.owner.routes.peers, "::1", "server-other-")
				expected = codes.FailedPrecondition
			case "hash-owner":
				expected = codes.FailedPrecondition
			case "membership":
				expected = codes.Unavailable
			}
			if invalid != "unknown-cn" {
				store.records[nodeID] = record
			}
			store.mu.Unlock()
			if invalid == "hash-owner" {
				fixture.ownerResolver.set("gateway.test", peerAddresses("::1"), nil)
				if err := fixture.owner.routes.peers.Refresh(fixture.ctx); err != nil {
					test.Fatal(err)
				}
			}
			if invalid == "membership" {
				fixture.ownerResolver.set("gateway.test", nil, errors.New("owner DNS unavailable"))
				if err := fixture.owner.routes.peers.Refresh(fixture.ctx); err == nil {
					test.Fatal("expected failed owner membership refresh")
				}
			}
			fake := fixture.node.dial(test, certificate)
			fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Hello{Hello: &nodepb.Hello{Os: "darwin", Arch: "arm64", Version: "1.2.3", Targets: []string{"ssh"}}}})
			if err := fake.failure(test); status.Code(err) != expected || strings.Contains(err.Error(), "https://") {
				test.Fatalf("owner accepted invalid %s or leaked redirect: %v", invalid, err)
			}
			store.mu.Lock()
			writes := len(store.hellos) + len(store.events)
			store.mu.Unlock()
			if writes != 0 {
				test.Fatal("invalid relay mutated owner state")
			}
		})
	}
}

func TestNodeRelayAuthenticatesPrivateRPCAndOriginalCertificate(test *testing.T) {
	fixture := newReplicaFixture(test)
	certificate := fixture.certificate(test, "darwin", "arm64")
	foreign := &nodeFixture{authority: nodeTestCA(test), store: &fakeNodeStore{records: make(map[string]nodeRecord)}}
	foreignCertificate := foreign.certificate(test, certificate.Leaf.Subject.CommonName, "darwin", "arm64")
	forged := bytes.Clone(certificate.Leaf.Raw)
	forged[len(forged)-1] ^= 1
	hello, err := proto.Marshal(&nodepb.NodeMessage{Body: &nodepb.NodeMessage_Hello{Hello: &nodepb.Hello{Os: "darwin", Arch: "arm64", Version: "1.2.3", Targets: []string{"ssh"}}}})
	if err != nil {
		test.Fatal(err)
	}
	for _, scenario := range []struct {
		name   string
		auth   []string
		frames []*gatewaypb.NodeRelayFrame
		code   codes.Code
	}{
		{"missing-auth", nil, []*gatewaypb.NodeRelayFrame{{CertificateDer: certificate.Leaf.Raw}}, codes.Unauthenticated},
		{"wrong-auth", []string{"Bearer wrong"}, []*gatewaypb.NodeRelayFrame{{CertificateDer: certificate.Leaf.Raw}}, codes.Unauthenticated},
		{"duplicate-auth", []string{"Bearer " + fixture.owner.settings.token("zoo-gateway"), "Bearer " + fixture.owner.settings.token("zoo-gateway")}, []*gatewaypb.NodeRelayFrame{{CertificateDer: certificate.Leaf.Raw}}, codes.Unauthenticated},
		{"foreign-ca", nil, []*gatewaypb.NodeRelayFrame{{CertificateDer: foreignCertificate.Leaf.Raw}}, codes.Unauthenticated},
		{"forged-der", nil, []*gatewaypb.NodeRelayFrame{{CertificateDer: []byte("not a certificate")}}, codes.Unauthenticated},
		{"forged-signature", nil, []*gatewaypb.NodeRelayFrame{{CertificateDer: forged}}, codes.Unauthenticated},
		{"missing-cert", nil, []*gatewaypb.NodeRelayFrame{{Message: hello}}, codes.InvalidArgument},
		{"oversized-cert", nil, []*gatewaypb.NodeRelayFrame{{CertificateDer: make([]byte, (64<<10)+1)}}, codes.InvalidArgument},
		{"mixed-first-frame", nil, []*gatewaypb.NodeRelayFrame{{CertificateDer: certificate.Leaf.Raw, Message: hello}}, codes.InvalidArgument},
		{"certificate-repeated", nil, []*gatewaypb.NodeRelayFrame{{CertificateDer: certificate.Leaf.Raw}, {CertificateDer: certificate.Leaf.Raw}}, codes.InvalidArgument},
		{"malformed-message", nil, []*gatewaypb.NodeRelayFrame{{CertificateDer: certificate.Leaf.Raw}, {Message: []byte{255}}}, codes.InvalidArgument},
	} {
		test.Run(scenario.name, func(test *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			headers := metadata.MD{}
			if strings.Contains(scenario.name, "auth") {
				headers["authorization"] = scenario.auth
			} else {
				headers.Set("authorization", "Bearer "+fixture.owner.settings.token("zoo-gateway"))
			}
			stream, err := fixture.ownerClient.RelayNode(metadata.NewOutgoingContext(ctx, headers))
			if err != nil {
				test.Fatal(err)
			}
			for _, frame := range scenario.frames {
				if err := stream.Send(frame); err != nil && !errors.Is(err, io.EOF) {
					test.Fatal(err)
				}
			}
			stream.CloseSend()
			if _, err := stream.Recv(); status.Code(err) != scenario.code {
				test.Fatalf("invalid relay %s accepted: %v", scenario.name, err)
			}
		})
	}
}

func TestNodeRelayRevocationAndOwnershipChangeDisconnect(test *testing.T) {
	for _, change := range []string{"revoked", "moved"} {
		test.Run(change, func(test *testing.T) {
			fixture := newReplicaFixture(test)
			certificate := fixture.certificate(test, "darwin", "arm64")
			serverID := "server-" + certificate.Leaf.Subject.CommonName
			fake := fixture.node.connect(test, certificate, "darwin", "arm64", "1.2.3")
			tunnel := fixture.node.tunnel(test, serverID, "ssh")
			if fake.next(test).GetOpen() == nil {
				test.Fatal("missing SSH open")
			}
			expected := codes.Unauthenticated
			if change == "revoked" {
				store := fixture.owner.nodes.store.(*fakeNodeStore)
				store.mu.Lock()
				record := store.records[certificate.Leaf.Subject.CommonName]
				record.Serial = "revoked"
				store.records[record.NodeID] = record
				store.mu.Unlock()
				fake.send(test, &nodepb.NodeMessage{Body: &nodepb.NodeMessage_Status{Status: &nodepb.Status{}}})
			} else {
				expected = codes.Unavailable
				fixture.ownerResolver.set("gateway.test", peerAddresses("::1"), nil)
				if err := fixture.owner.routes.peers.Refresh(fixture.ctx); err != nil {
					test.Fatal(err)
				}
				fixture.owner.routes.closeMoved(fixture.owner)
			}
			if err := fake.failure(test); status.Code(err) != expected {
				test.Fatalf("changed node remained relayed: %v", err)
			}
			if _, err := tunnel.Recv(); status.Code(err) != codes.Unavailable {
				test.Fatalf("node change left SSH tunnel open: %v", err)
			}
			if change == "revoked" {
				fixture.node.waitOnline(test, serverID, false)
			}
		})
	}
}

func TestNodeRelayFrontRequiresTrustedClientTLS(test *testing.T) {
	fixture := newReplicaFixture(test)
	foreign := &nodeFixture{authority: nodeTestCA(test), store: &fakeNodeStore{records: make(map[string]nodeRecord)}}
	foreignCertificate := foreign.certificate(test, "foreign-node", "darwin", "arm64")
	for _, certificates := range [][]tls.Certificate{nil, {foreignCertificate}} {
		roots := x509.NewCertPool()
		roots.AddCert(fixture.node.authority.certificate)
		connection, err := grpc.NewClient(fixture.node.address, grpc.WithTransportCredentials(credentials.NewTLS(&tls.Config{
			MinVersion: tls.VersionTLS12, RootCAs: roots, Certificates: certificates,
		})))
		if err != nil {
			test.Fatal(err)
		}
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		stream, err := nodepb.NewNodeClient(connection).Connect(ctx)
		if err == nil {
			_, err = stream.Recv()
		}
		cancel()
		connection.Close()
		if status.Code(err) != codes.Unavailable {
			test.Fatalf("native front accepted missing or foreign client TLS identity: %v", err)
		}
	}
}
