package main

import (
	"context"
	"crypto/sha256"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/chann44/zoo/node/nodepb"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/peer"
	"google.golang.org/grpc/status"
	"zoo/gateway/gatewaypb"
)

const (
	nodeTunnelChunk = 64 << 10
	nodeUpdateChunk = 1 << 20
	nodeQueueSize   = 32
	nodeTunnelLimit = 128
	nodeHelloWait   = 10 * time.Second
)

var errNodeNotFound = errors.New("node not found")

type nodeRecord struct {
	NodeID, ServerID, Serial, OS, Arch, SSHHostKey string
	ExpiresAt                                      time.Time
}

type nodeDriverData struct {
	Name      string `json:"name"`
	Available bool   `json:"available"`
	Detail    string `json:"detail"`
}

type nodeCheckData struct {
	Name   string `json:"name"`
	OK     bool   `json:"ok"`
	Detail string `json:"detail"`
}

type nodeHelloData struct {
	Version  string           `json:"version"`
	OS       string           `json:"os"`
	Arch     string           `json:"arch"`
	Hostname string           `json:"hostname"`
	Drivers  []nodeDriverData `json:"drivers"`
	Targets  []string         `json:"targets"`
}

type nodeStatusData struct {
	CPUs            uint32          `json:"cpus"`
	MemoryTotal     uint64          `json:"memory_total"`
	MemoryAvailable uint64          `json:"memory_available"`
	DiskTotal       uint64          `json:"disk_total"`
	DiskFree        uint64          `json:"disk_free"`
	Load            float64         `json:"load"`
	Sandboxes       []string        `json:"sandboxes"`
	Checks          []nodeCheckData `json:"checks"`
}

type nodeReport struct {
	NodeID   string `json:"node_id"`
	ServerID string `json:"server_id"`
	nodeHelloData
	nodeStatusData
	SeenAt time.Time `json:"seen_at"`
}

type nodeStore interface {
	Lookup(context.Context, string) (nodeRecord, error)
	WriteHello(context.Context, nodeRecord, nodeHelloData) error
	WriteStatus(context.Context, nodeRecord, nodeStatusData) error
	SetOnline(context.Context, nodeRecord, bool) error
	CompareAndSwapCertificate(context.Context, string, string, string, time.Time) (bool, error)
}

type nodeHubOptions struct {
	Version       string
	Endpoints     []string
	DistDir       string
	StatusSeconds uint32
}

type nodeHub struct {
	gatewaypb.UnimplementedGatewayServer
	store     nodeStore
	authority *nodeAuthority
	options   nodeHubOptions
	lifecycle sync.Mutex
	mu        sync.Mutex
	sessions  map[string]*nodeSession
	reports   map[string]nodeReport
	closed    bool
}

func newNodeHub(store nodeStore, authority *nodeAuthority, options nodeHubOptions) (*nodeHub, error) {
	if store == nil || authority == nil || options.Version == "" {
		return nil, errors.New("the node hub requires a store, node CA and API version")
	}
	if options.StatusSeconds == 0 {
		options.StatusSeconds = 15
	}
	options.Endpoints = slices.Clone(options.Endpoints)
	return &nodeHub{store: store, authority: authority, options: options, sessions: make(map[string]*nodeSession), reports: make(map[string]nodeReport)}, nil
}

type nodeSession struct {
	ctx           context.Context
	cancel        context.CancelFunc
	authorization sync.Mutex
	mu            sync.Mutex
	registration  nodeRecord
	hello         nodeHelloData
	out           chan *nodepb.ApiMessage
	failures      chan error
	tunnels       map[uint64]*nodeByteTunnel
	nextID        uint64
	ready         chan struct{}
}

func (session *nodeSession) record() nodeRecord {
	session.mu.Lock()
	defer session.mu.Unlock()
	return session.registration
}

func (session *nodeSession) send(ctx context.Context, message *nodepb.ApiMessage) error {
	select {
	case <-session.ctx.Done():
		return status.Error(codes.Unavailable, "the server's zoo-node disconnected")
	default:
	}
	select {
	case session.out <- message:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	case <-session.ctx.Done():
		return status.Error(codes.Unavailable, "the server's zoo-node disconnected")
	}
}

func (session *nodeSession) close() {
	session.cancel()
	session.mu.Lock()
	defer session.mu.Unlock()
	for tunnelID, tunnel := range session.tunnels {
		tunnel.finish(status.Error(codes.Unavailable, "the server's zoo-node disconnected"))
		delete(session.tunnels, tunnelID)
	}
}

func (hub *nodeHub) attach(session *nodeSession) error {
	hub.lifecycle.Lock()
	defer hub.lifecycle.Unlock()
	hub.mu.Lock()
	closed := hub.closed
	hub.mu.Unlock()
	if closed || session.ctx.Err() != nil {
		return status.Error(codes.Unavailable, "the node hub is closed")
	}
	record := session.record()
	if err := hub.store.SetOnline(session.ctx, record, true); err != nil {
		return status.Error(codes.Unavailable, "could not persist node connection")
	}
	hub.mu.Lock()
	previous := hub.sessions[record.ServerID]
	hub.sessions[record.ServerID] = session
	hub.reports[record.ServerID] = nodeReport{NodeID: record.NodeID, ServerID: record.ServerID, nodeHelloData: session.hello, SeenAt: time.Now().UTC()}
	hub.mu.Unlock()
	if previous != nil {
		previous.close()
	}
	return nil
}

func (hub *nodeHub) detach(session *nodeSession) {
	session.close()
	hub.lifecycle.Lock()
	defer hub.lifecycle.Unlock()
	record := session.record()
	hub.mu.Lock()
	current := hub.sessions[record.ServerID] == session
	if current {
		delete(hub.sessions, record.ServerID)
	}
	hub.mu.Unlock()
	if current {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := hub.store.SetOnline(ctx, record, false); err != nil {
			slog.Error("node disconnect persistence failed", "node_id", record.NodeID, "error", err)
		}
	}
}

func (hub *nodeHub) close() {
	hub.lifecycle.Lock()
	hub.mu.Lock()
	hub.closed = true
	sessions := make([]*nodeSession, 0, len(hub.sessions))
	for _, session := range hub.sessions {
		sessions = append(sessions, session)
	}
	hub.mu.Unlock()
	hub.lifecycle.Unlock()
	for _, session := range sessions {
		hub.detach(session)
	}
}

func (hub *nodeHub) authenticate(ctx context.Context) (nodeRecord, error) {
	remote, ok := peer.FromContext(ctx)
	if !ok {
		return nodeRecord{}, status.Error(codes.Unauthenticated, "node mTLS certificate required")
	}
	info, ok := remote.AuthInfo.(credentials.TLSInfo)
	if !ok || len(info.State.VerifiedChains) == 0 || len(info.State.PeerCertificates) == 0 {
		return nodeRecord{}, status.Error(codes.Unauthenticated, "node mTLS certificate required")
	}
	certificate := info.State.PeerCertificates[0]
	record, err := hub.store.Lookup(ctx, certificate.Subject.CommonName)
	if errors.Is(err, errNodeNotFound) {
		return nodeRecord{}, status.Error(codes.Unauthenticated, "unknown node certificate")
	}
	if err != nil {
		return nodeRecord{}, status.Error(codes.Unavailable, "node registration could not be read")
	}
	if !validNodeRecord(record, certificate) {
		return nodeRecord{}, status.Error(codes.Unauthenticated, "unknown, expired or replaced node certificate")
	}
	if !supportedNodePlatform(record.OS, record.Arch) {
		return nodeRecord{}, status.Error(codes.Unimplemented, "node hosts support only darwin/arm64 and windows/amd64")
	}
	return record, nil
}

func supportedNodePlatform(osName, arch string) bool {
	return osName == "darwin" && arch == "arm64" || osName == "windows" && arch == "amd64"
}

func validNodeRecord(record nodeRecord, certificate *x509.Certificate) bool {
	return record.NodeID != "" && record.NodeID == certificate.Subject.CommonName && record.ServerID != "" &&
		record.Serial == certificate.SerialNumber.Text(16) && record.ExpiresAt.Equal(certificate.NotAfter) &&
		time.Now().Before(record.ExpiresAt) && record.SSHHostKey != ""
}

func (hub *nodeHub) current(ctx context.Context, session *nodeSession) error {
	session.authorization.Lock()
	defer session.authorization.Unlock()
	return hub.checkCurrent(ctx, session)
}

func (hub *nodeHub) checkCurrent(ctx context.Context, session *nodeSession) error {
	registered := session.record()
	record, err := hub.store.Lookup(ctx, registered.NodeID)
	if err != nil && !errors.Is(err, errNodeNotFound) {
		return status.Error(codes.Unavailable, "node registration could not be read")
	}
	if err != nil || record.NodeID != registered.NodeID || record.ServerID != registered.ServerID ||
		record.Serial != registered.Serial || !record.ExpiresAt.Equal(registered.ExpiresAt) ||
		record.OS != registered.OS || record.Arch != registered.Arch || !time.Now().Before(record.ExpiresAt) {
		return status.Error(codes.Unauthenticated, "node certificate was revoked or replaced")
	}
	return nil
}

type nativeNodeConnect struct {
	nodepb.UnimplementedNodeServer
	hub *nodeHub
}

func (service *nativeNodeConnect) Connect(stream grpc.BidiStreamingServer[nodepb.NodeMessage, nodepb.ApiMessage]) error {
	record, err := service.hub.authenticate(stream.Context())
	if err != nil {
		return err
	}
	ctx, cancel := context.WithCancel(stream.Context())
	session := &nodeSession{ctx: ctx, cancel: cancel, registration: record, out: make(chan *nodepb.ApiMessage, nodeQueueSize), failures: make(chan error, 1), tunnels: make(map[uint64]*nodeByteTunnel), ready: make(chan struct{})}
	defer service.hub.detach(session)
	received := make(chan error, 1)
	go func() { received <- service.hub.receive(session, stream) }()
	sent := make(chan error, 1)
	go func() {
		select {
		case <-session.ready:
		case <-ctx.Done():
			return
		}
		for {
			select {
			case message := <-session.out:
				if err := stream.Send(message); err != nil {
					sent <- err
					return
				}
			case <-ctx.Done():
				return
			}
		}
	}()
	deadline := time.NewTimer(nodeHelloWait)
	defer deadline.Stop()
	verification := time.NewTicker(time.Duration(service.hub.options.StatusSeconds) * time.Second)
	defer verification.Stop()
	ready := session.ready
	for {
		select {
		case err := <-sent:
			return err
		case err := <-session.failures:
			return err
		case err := <-received:
			if errors.Is(err, io.EOF) {
				return nil
			}
			return err
		case <-ready:
			deadline.Stop()
			ready = nil
		case <-deadline.C:
			return status.Error(codes.DeadlineExceeded, "node hello timed out")
		case <-verification.C:
			select {
			case <-session.ready:
				if err := service.hub.current(ctx, session); err != nil {
					return err
				}
			default:
			}
		case <-ctx.Done():
			return status.Error(codes.Unavailable, "node disconnected or replaced")
		}
	}
}

func (hub *nodeHub) receive(session *nodeSession, stream grpc.BidiStreamingServer[nodepb.NodeMessage, nodepb.ApiMessage]) error {
	first, err := stream.Recv()
	if err != nil {
		return err
	}
	hello := first.GetHello()
	record := session.record()
	if hello == nil || hello.Os != record.OS || hello.Arch != record.Arch {
		return status.Error(codes.InvalidArgument, "the first message must be a hello matching the node's registered OS and architecture")
	}
	for _, target := range hello.Targets {
		if target != "ssh" {
			return status.Error(codes.InvalidArgument, "node tunnels support only the ssh target")
		}
	}
	if err := hub.current(session.ctx, session); err != nil {
		return err
	}
	session.hello = nodeHelloData{Version: hello.Version, OS: hello.Os, Arch: hello.Arch, Hostname: hello.Hostname, Targets: slices.Clone(hello.Targets), Drivers: make([]nodeDriverData, 0, len(hello.Drivers))}
	for _, driver := range hello.Drivers {
		if driver == nil {
			return status.Error(codes.InvalidArgument, "invalid node driver")
		}
		session.hello.Drivers = append(session.hello.Drivers, nodeDriverData{driver.Name, driver.Available, driver.Detail})
	}
	if err := hub.store.WriteHello(session.ctx, record, session.hello); err != nil {
		return status.Error(codes.Unavailable, "node hello could not be persisted")
	}
	if err := session.send(session.ctx, &nodepb.ApiMessage{Body: &nodepb.ApiMessage_Welcome{Welcome: &nodepb.Welcome{ApiVersion: hub.options.Version, Endpoints: slices.Clone(hub.options.Endpoints), StatusSeconds: hub.options.StatusSeconds}}}); err != nil {
		return err
	}
	if err := hub.attach(session); err != nil {
		return err
	}
	close(session.ready)
	go func() {
		if err := hub.sendUpdate(session); err != nil && session.ctx.Err() == nil {
			select {
			case session.failures <- err:
			case <-session.ctx.Done():
			}
		}
	}()
	for {
		message, err := stream.Recv()
		if err != nil {
			return err
		}
		if session.ctx.Err() != nil {
			return session.ctx.Err()
		}
		switch body := message.Body.(type) {
		case *nodepb.NodeMessage_Status:
			if err := hub.current(session.ctx, session); err != nil {
				return err
			}
			if body.Status == nil {
				return status.Error(codes.InvalidArgument, "invalid node status")
			}
			report := body.Status
			values := nodeStatusData{CPUs: report.Cpus, MemoryTotal: report.MemoryTotal, MemoryAvailable: report.MemoryAvailable, DiskTotal: report.DiskTotal, DiskFree: report.DiskFree, Load: report.Load, Sandboxes: slices.Clone(report.Sandboxes), Checks: make([]nodeCheckData, 0, len(report.Checks))}
			for _, check := range report.Checks {
				if check == nil {
					return status.Error(codes.InvalidArgument, "invalid node check")
				}
				values.Checks = append(values.Checks, nodeCheckData{check.Name, check.Ok, check.Detail})
			}
			if err := hub.store.WriteStatus(session.ctx, session.record(), values); err != nil {
				return status.Error(codes.Unavailable, "node status could not be persisted")
			}
			hub.mu.Lock()
			if hub.sessions[record.ServerID] == session {
				cached := hub.reports[record.ServerID]
				cached.nodeStatusData, cached.SeenAt = values, time.Now().UTC()
				hub.reports[record.ServerID] = cached
			}
			hub.mu.Unlock()
		case *nodepb.NodeMessage_Data:
			if body.Data == nil || len(body.Data.Data) > nodeTunnelChunk {
				return status.Error(codes.ResourceExhausted, "node tunnel data exceeds limit")
			}
			session.mu.Lock()
			tunnel := session.tunnels[body.Data.Id]
			session.mu.Unlock()
			if tunnel != nil {
				select {
				case tunnel.data <- slices.Clone(body.Data.Data):
				case <-tunnel.done:
				default:
					session.endTunnel(body.Data.Id, status.Error(codes.ResourceExhausted, "node tunnel reader is too slow"), true)
				}
			}
		case *nodepb.NodeMessage_Close:
			if body.Close == nil {
				return status.Error(codes.InvalidArgument, "invalid node tunnel close")
			}
			var failure error
			if body.Close.Error != "" {
				failure = status.Error(codes.Unavailable, body.Close.Error)
			}
			session.endTunnel(body.Close.Id, failure, false)
		case *nodepb.NodeMessage_Renew:
			if body.Renew == nil {
				return status.Error(codes.InvalidArgument, "invalid node renewal")
			}
			renewal := hub.renew(session, body.Renew.Csr)
			if err := session.send(session.ctx, &nodepb.ApiMessage{Body: &nodepb.ApiMessage_Renewal{Renewal: renewal}}); err != nil {
				return err
			}
		default:
			return status.Error(codes.InvalidArgument, "unexpected node message")
		}
	}
}

func (hub *nodeHub) renew(session *nodeSession, request []byte) *nodepb.Renewal {
	session.authorization.Lock()
	defer session.authorization.Unlock()
	if err := hub.checkCurrent(session.ctx, session); err != nil {
		return &nodepb.Renewal{Error: err.Error()}
	}
	registered := session.record()
	encoded, certificate, err := hub.authority.renew(request, registered.NodeID)
	if err != nil {
		return &nodepb.Renewal{Error: err.Error()}
	}
	serial := certificate.SerialNumber.Text(16)
	updated, err := hub.store.CompareAndSwapCertificate(session.ctx, registered.NodeID, registered.Serial, serial, certificate.NotAfter)
	if err != nil {
		return &nodepb.Renewal{Error: "the node certificate could not be persisted"}
	}
	if !updated {
		return &nodepb.Renewal{Error: "the node certificate was replaced during renewal"}
	}
	session.mu.Lock()
	session.registration.Serial, session.registration.ExpiresAt = serial, certificate.NotAfter
	session.mu.Unlock()
	return &nodepb.Renewal{Certificate: encoded}
}

func (hub *nodeHub) sendUpdate(session *nodeSession) error {
	version := session.hello.Version
	if version == "" || version == "dev" || version == hub.options.Version {
		return nil
	}
	if hub.options.DistDir == "" {
		return status.Error(codes.FailedPrecondition, "the node update artifact directory is required")
	}
	built, err := os.ReadFile(filepath.Join(hub.options.DistDir, "VERSION"))
	if err != nil {
		return status.Error(codes.FailedPrecondition, "node/dist/VERSION could not be read")
	}
	if strings.TrimSpace(string(built)) != hub.options.Version {
		return status.Error(codes.FailedPrecondition, "node/dist/VERSION does not match the API version")
	}
	name := fmt.Sprintf("zoo-node-%s-%s", session.hello.OS, session.hello.Arch)
	if session.hello.OS == "windows" {
		name += ".exe"
	}
	file, err := os.Open(filepath.Join(hub.options.DistDir, name))
	if err != nil {
		return status.Errorf(codes.FailedPrecondition, "the matching node update artifact %s could not be read", name)
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return errors.New("node update must be a regular file")
	}
	digest := sha256.New()
	size, err := io.Copy(digest, file)
	if err != nil {
		return err
	}
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		return err
	}
	sha := hex.EncodeToString(digest.Sum(nil))
	for offset := int64(0); ; {
		data := make([]byte, nodeUpdateChunk)
		count, err := file.Read(data)
		if err != nil && !errors.Is(err, io.EOF) {
			return err
		}
		offset += int64(count)
		last := offset >= size
		if count == 0 && !last {
			return errors.New("node update changed while streaming")
		}
		message := &nodepb.ApiMessage{Body: &nodepb.ApiMessage_Update{Update: &nodepb.UpdateChunk{Version: hub.options.Version, Sha256: sha, Size: uint64(size), Data: data[:count], Last: last}}}
		if err := session.send(session.ctx, message); err != nil {
			return err
		}
		if last {
			return nil
		}
	}
}

type nodeByteTunnel struct {
	data chan []byte
	done chan struct{}
	once sync.Once
	err  error
}

func (tunnel *nodeByteTunnel) finish(err error) {
	tunnel.once.Do(func() { tunnel.err = err; close(tunnel.done) })
}

func (session *nodeSession) endTunnel(tunnelID uint64, err error, notify bool) {
	session.mu.Lock()
	tunnel := session.tunnels[tunnelID]
	delete(session.tunnels, tunnelID)
	session.mu.Unlock()
	if tunnel == nil {
		return
	}
	tunnel.finish(err)
	if notify {
		ctx, cancel := context.WithTimeout(session.ctx, 5*time.Second)
		defer cancel()
		reason := ""
		if err != nil {
			reason = err.Error()
		}
		session.send(ctx, &nodepb.ApiMessage{Body: &nodepb.ApiMessage_Close{Close: &nodepb.TunnelClose{Id: tunnelID, Error: reason}}})
	}
}

func (hub *nodeHub) NodeTunnel(stream grpc.BidiStreamingServer[gatewaypb.Bytes, gatewaypb.Bytes]) error {
	headers, _ := metadata.FromIncomingContext(stream.Context())
	serverIDs, targets := headers.Get("server_id"), headers.Get("target")
	if len(serverIDs) != 1 || serverIDs[0] == "" || len(targets) != 1 || targets[0] != "ssh" {
		return status.Error(codes.InvalidArgument, "node tunnels require server_id and the ssh target")
	}
	hub.mu.Lock()
	session := hub.sessions[serverIDs[0]]
	hub.mu.Unlock()
	if session == nil || session.ctx.Err() != nil {
		return status.Error(codes.Unavailable, "the server's zoo-node is not connected")
	}
	if err := hub.current(stream.Context(), session); err != nil {
		return err
	}
	if !slices.Contains(session.hello.Targets, "ssh") {
		return status.Error(codes.Unavailable, "the server's zoo-node cannot reach ssh")
	}
	tunnel := &nodeByteTunnel{data: make(chan []byte, nodeQueueSize), done: make(chan struct{})}
	session.mu.Lock()
	if len(session.tunnels) >= nodeTunnelLimit || session.ctx.Err() != nil {
		session.mu.Unlock()
		return status.Error(codes.ResourceExhausted, "node tunnel capacity exceeded")
	}
	session.nextID++
	tunnelID := session.nextID
	session.tunnels[tunnelID] = tunnel
	session.mu.Unlock()
	defer session.endTunnel(tunnelID, nil, true)
	if err := session.send(stream.Context(), &nodepb.ApiMessage{Body: &nodepb.ApiMessage_Open{Open: &nodepb.TunnelOpen{Id: tunnelID, Target: "ssh"}}}); err != nil {
		return err
	}
	go func() {
		for {
			message, err := stream.Recv()
			if err != nil {
				if errors.Is(err, io.EOF) {
					err = nil
				}
				session.endTunnel(tunnelID, err, true)
				return
			}
			for offset := 0; offset < len(message.Data); offset += nodeTunnelChunk {
				data := message.Data[offset:min(offset+nodeTunnelChunk, len(message.Data))]
				if err := session.send(stream.Context(), &nodepb.ApiMessage{Body: &nodepb.ApiMessage_Data{Data: &nodepb.TunnelData{Id: tunnelID, Data: slices.Clone(data)}}}); err != nil {
					session.endTunnel(tunnelID, err, true)
					return
				}
			}
		}
	}()
	for {
		select {
		case data := <-tunnel.data:
			if err := stream.Send(&gatewaypb.Bytes{Data: data}); err != nil {
				return err
			}
		case <-tunnel.done:
			for {
				select {
				case data := <-tunnel.data:
					if err := stream.Send(&gatewaypb.Bytes{Data: data}); err != nil {
						return err
					}
				default:
					return tunnel.err
				}
			}
		case <-stream.Context().Done():
			return stream.Context().Err()
		}
	}
}

func (hub *nodeHub) NodeStatus(ctx context.Context, request *gatewaypb.NodeStatusRequest) (*gatewaypb.NodeStatusReply, error) {
	if request.ServerId == "" {
		return nil, status.Error(codes.InvalidArgument, "server_id is required")
	}
	hub.mu.Lock()
	session := hub.sessions[request.ServerId]
	report, reported := hub.reports[request.ServerId]
	online := session != nil && session.ctx.Err() == nil
	hub.mu.Unlock()
	var encoded []byte
	if reported {
		var err error
		encoded, err = json.Marshal(report)
		if err != nil {
			return nil, status.Error(codes.Internal, "node report could not be encoded")
		}
	}
	return &gatewaypb.NodeStatusReply{Online: online, ReportJson: encoded}, nil
}
