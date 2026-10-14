package main

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"io"
	"log/slog"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/chann44/zoo/node/nodepb"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
)

// testCA plays the API's node CA.
type testCA struct {
	cert *x509.Certificate
	key  *ecdsa.PrivateKey
	pem  []byte
}

func newCA(t *testing.T) *testCA {
	t.Helper()
	key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	tmpl := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "Zoo node CA"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(time.Hour * 24 * 3650),
		IsCA:                  true,
		BasicConstraintsValid: true,
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	cert, _ := x509.ParseCertificate(der)
	return &testCA{cert: cert, key: key, pem: pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})}
}

func (ca *testCA) fingerprint() string {
	sum := sha256.Sum256(ca.cert.Raw)
	return hex.EncodeToString(sum[:])
}

func (ca *testCA) issue(t *testing.T, pub *ecdsa.PublicKey, cn string, server bool, days int) []byte {
	t.Helper()
	tmpl := &x509.Certificate{
		SerialNumber: big.NewInt(time.Now().UnixNano()),
		Subject:      pkix.Name{CommonName: cn},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(time.Duration(days) * 24 * time.Hour),
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	}
	if server {
		tmpl.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}
		tmpl.IPAddresses = []net.IP{net.ParseIP("127.0.0.1")}
		tmpl.DNSNames = []string{"localhost"}
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, ca.cert, pub, ca.key)
	if err != nil {
		t.Fatal(err)
	}
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
}

func (ca *testCA) signCSR(t *testing.T, csrPEM []byte, cn string, days int) []byte {
	t.Helper()
	block, _ := pem.Decode(csrPEM)
	csr, err := x509.ParseCertificateRequest(block.Bytes)
	if err != nil {
		t.Fatal(err)
	}
	return ca.issue(t, csr.PublicKey.(*ecdsa.PublicKey), cn, false, days)
}

func token(api, secret, fingerprint string) string {
	body, _ := json.Marshal(Token{API: api, Secret: secret, Fingerprint: fingerprint})
	return tokenPrefix + base64.RawURLEncoding.EncodeToString(body)
}

func TestDecodeToken(t *testing.T) {
	got, err := decodeToken(token("https://zoo.example", "s3cret", "abc"))
	if err != nil || got.API != "https://zoo.example" || got.Secret != "s3cret" || got.Fingerprint != "abc" {
		t.Fatalf("got %+v, %v", got, err)
	}
	// padded, as Python's urlsafe_b64encode would leave it if not stripped
	padded := tokenPrefix + base64.URLEncoding.EncodeToString([]byte(`{"u":"x","s":"y","f":"z"}`))
	if _, err := decodeToken(padded); err != nil {
		t.Fatalf("padded token: %v", err)
	}
	for _, bad := range []string{"", "zn1.", "zn2.e30", "zn1.!!!", tokenPrefix + base64.RawURLEncoding.EncodeToString([]byte(`{"u":"x"}`))} {
		if _, err := decodeToken(bad); err == nil {
			t.Errorf("%q decoded", bad)
		}
	}
}

// fakeJoin serves /v1/nodes/join like the API: it signs the CSR with the CA and names the node.
func fakeJoin(t *testing.T, ca *testCA, endpoints []string, seen *joinRequest) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/nodes/join" {
			http.NotFound(w, r)
			return
		}
		var req joinRequest
		json.NewDecoder(r.Body).Decode(&req)
		if seen != nil {
			*seen = req
		}
		if !strings.Contains(req.Token, tokenPrefix) {
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]string{"detail": "the join token is unknown, used or expired"})
			return
		}
		json.NewEncoder(w).Encode(joinResponse{
			NodeID:      "node-1",
			ServerID:    "server-1",
			Certificate: string(ca.signCSR(t, []byte(req.CSR), "node-1", 365)),
			CA:          string(ca.pem),
			Endpoints:   endpoints,
		})
	}))
}

func TestJoinSavesCredentialsAndPinsTheCA(t *testing.T) {
	ca := newCA(t)
	var seen joinRequest
	api := fakeJoin(t, ca, []string{"127.0.0.1:7443"}, &seen)
	defer api.Close()
	hostKey := filepath.Join(t.TempDir(), "host.pub")
	os.WriteFile(hostKey, []byte("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA host\n"), 0o644)
	hostKeyFile = func() string { return hostKey }
	home := t.TempDir()
	cfg, err := join(context.Background(), token(api.URL, "s", ca.fingerprint()), home, "zoo")
	if err != nil {
		t.Fatal(err)
	}
	if cfg.NodeID != "node-1" || cfg.ServerID != "server-1" || cfg.Endpoints[0] != "127.0.0.1:7443" {
		t.Fatalf("config %+v", cfg)
	}
	if seen.OS != runtime.GOOS || seen.Arch != runtime.GOARCH || !strings.Contains(seen.CSR, "CERTIFICATE REQUEST") {
		t.Fatalf("join request %+v", seen)
	}
	if seen.SSHUser != "zoo" || seen.SSHHostKey != "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA" {
		t.Fatalf("ssh fields %q %q", seen.SSHUser, seen.SSHHostKey)
	}
	if _, _, err := loadCredentials(home); err != nil {
		t.Fatalf("credentials: %v", err)
	}
	loaded, err := loadConfig(home)
	if err != nil || loaded.NodeID != "node-1" {
		t.Fatalf("loaded %+v, %v", loaded, err)
	}
	_, keyFile, _, _ := paths(home)
	if info, _ := os.Stat(keyFile); runtime.GOOS != "windows" && info.Mode().Perm() != 0o600 {
		t.Fatalf("key mode %v", info.Mode())
	}

	// a CA other than the token's is refused, and nothing is written
	other := t.TempDir()
	_, err = join(context.Background(), token(api.URL, "s", strings.Repeat("0", 64)), other, "zoo")
	if err == nil || !strings.Contains(err.Error(), "doesn't match the join token") {
		t.Fatalf("pin: %v", err)
	}
	if _, err := os.Stat(filepath.Join(other, "node.key")); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("wrote a key after a failed join")
	}
}

func TestCheckJoinRefusesACertificateForAnotherKey(t *testing.T) {
	ca := newCA(t)
	ours, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	theirs, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	resp := joinResponse{NodeID: "n", Certificate: string(ca.issue(t, &theirs.PublicKey, "n", false, 1)), CA: string(ca.pem), Endpoints: []string{"x:1"}}
	if err := checkJoin(resp, ca.fingerprint(), &ours.PublicKey); err == nil {
		t.Fatal("accepted a certificate for another key")
	}
	resp.Certificate = string(ca.issue(t, &ours.PublicKey, "someone-else", false, 1))
	if err := checkJoin(resp, ca.fingerprint(), &ours.PublicKey); err == nil {
		t.Fatal("accepted a certificate for another node")
	}
	resp.Certificate = string(ca.issue(t, &ours.PublicKey, "n", false, 1))
	if err := checkJoin(resp, ca.fingerprint(), &ours.PublicKey); err != nil {
		t.Fatal(err)
	}
}

// fakeAPI is the API end of Connect: it records what the node sends and lets the test send it messages.
type fakeAPI struct {
	nodepb.UnimplementedNodeServer
	mu       sync.Mutex
	peers    []string
	hellos   chan *nodepb.Hello
	statuses chan *nodepb.Status
	inbox    chan *nodepb.NodeMessage
	outbox   chan *nodepb.ApiMessage
	welcome  *nodepb.Welcome
}

func (f *fakeAPI) Connect(stream nodepb.Node_ConnectServer) error {
	ctx := stream.Context()
	go func() {
		for {
			select {
			case msg := <-f.outbox:
				if stream.Send(msg) != nil {
					return
				}
			case <-ctx.Done():
				return
			}
		}
	}()
	for {
		msg, err := stream.Recv()
		if err != nil {
			return nil
		}
		switch body := msg.Body.(type) {
		case *nodepb.NodeMessage_Hello:
			f.hellos <- body.Hello
			stream.Send(&nodepb.ApiMessage{Body: &nodepb.ApiMessage_Welcome{Welcome: f.welcome}})
		case *nodepb.NodeMessage_Status:
			select {
			case f.statuses <- body.Status:
			default:
			}
		default:
			f.inbox <- msg
		}
	}
}

// startAPI serves a fake API over mTLS and joins a node to it, returning the node's home.
func startAPI(t *testing.T, ca *testCA, nodeDays int) (*fakeAPI, string, func()) {
	t.Helper()
	serverKey, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	serverCert, _ := tls.X509KeyPair(ca.issue(t, &serverKey.PublicKey, "Zoo API", true, 1), mustKey(t, serverKey))
	pool := x509.NewCertPool()
	pool.AppendCertsFromPEM(ca.pem)
	creds := credentials.NewTLS(&tls.Config{
		Certificates: []tls.Certificate{serverCert},
		ClientCAs:    pool,
		ClientAuth:   tls.RequireAndVerifyClientCert,
	})
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	api := &fakeAPI{
		hellos:   make(chan *nodepb.Hello, 4),
		statuses: make(chan *nodepb.Status, 4),
		inbox:    make(chan *nodepb.NodeMessage, 64),
		outbox:   make(chan *nodepb.ApiMessage, 64),
		welcome:  &nodepb.Welcome{ApiVersion: "1.0.0", StatusSeconds: 5},
	}
	srv := grpc.NewServer(grpc.Creds(creds))
	nodepb.RegisterNodeServer(srv, api)
	go srv.Serve(lis)

	home := t.TempDir()
	nodeKey, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	_, keyFile, certFile, caFile := paths(home)
	os.WriteFile(keyFile, mustKey(t, nodeKey), 0o600)
	os.WriteFile(certFile, ca.issue(t, &nodeKey.PublicKey, "node-1", false, nodeDays), 0o600)
	os.WriteFile(caFile, ca.pem, 0o600)
	saveConfig(home, Config{NodeID: "node-1", ServerID: "server-1", Endpoints: []string{lis.Addr().String()}})
	api.welcome.Endpoints = []string{lis.Addr().String()}
	return api, home, srv.Stop
}

func mustKey(t *testing.T, key *ecdsa.PrivateKey) []byte {
	data, err := encodeKey(key)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

// echoTarget writes tunnel bytes back over a local TCP connection.
func echoTarget(t *testing.T) string {
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { lis.Close() })
	go func() {
		for {
			conn, err := lis.Accept()
			if err != nil {
				return
			}
			go func() {
				defer conn.Close()
				io.Copy(conn, conn)
			}()
		}
	}()
	return lis.Addr().String()
}

func runNode(t *testing.T, home string, targets map[string]target) (*Node, chan error, context.CancelFunc) {
	cfg, err := loadConfig(home)
	if err != nil {
		t.Fatal(err)
	}
	n := newNode(cfg, home, slog.New(slog.NewTextHandler(io.Discard, nil)))
	n.drivers = []Driver{fakeDriver{}}
	n.targets = targets
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- n.run(ctx) }()
	return n, done, cancel
}

type fakeDriver struct{}

func (fakeDriver) Name() string                                { return "zoovm" }
func (fakeDriver) Probe(context.Context) (bool, string)        { return true, "zoovm" }
func (fakeDriver) Sandboxes(context.Context) ([]string, error) { return []string{"sb-2", "sb-1"}, nil }

func wait[T any](t *testing.T, ch chan T) T {
	t.Helper()
	select {
	case v := <-ch:
		return v
	case <-time.After(10 * time.Second):
		t.Fatal("timed out")
	}
	var zero T
	return zero
}

func TestNodeReportsAndTunnels(t *testing.T) {
	ca := newCA(t)
	api, home, stop := startAPI(t, ca, 365)
	defer stop()
	_, done, cancel := runNode(t, home, map[string]target{"ssh": {"tcp", echoTarget(t)}})
	defer cancel()

	hello := wait(t, api.hellos)
	if hello.Version != version || hello.Os != runtime.GOOS || len(hello.Drivers) != 1 || !hello.Drivers[0].Available {
		t.Fatalf("hello %+v", hello)
	}
	if len(hello.Targets) != 1 || hello.Targets[0] != "ssh" {
		t.Fatalf("targets %v", hello.Targets)
	}
	status := wait(t, api.statuses)
	if status.Cpus == 0 || strings.Join(status.Sandboxes, ",") != "sb-1,sb-2" {
		t.Fatalf("status %+v", status)
	}
	names := []string{}
	for _, c := range status.Checks {
		names = append(names, c.Name)
	}
	if strings.Join(names, ",") != "disk,ssh,zoovm" {
		t.Fatalf("checks %v", names)
	}

	// a tunnel to the echo socket: what the API sends comes back
	api.outbox <- &nodepb.ApiMessage{Body: &nodepb.ApiMessage_Open{Open: &nodepb.TunnelOpen{Id: 7, Target: "ssh"}}}
	api.outbox <- &nodepb.ApiMessage{Body: &nodepb.ApiMessage_Data{Data: &nodepb.TunnelData{Id: 7, Data: []byte("SSH-2.0-Zoo")}}}
	var got []byte
	for len(got) < len("SSH-2.0-Zoo") {
		msg := wait(t, api.inbox)
		data := msg.GetData()
		if data == nil || data.Id != 7 {
			t.Fatalf("unexpected %v", msg)
		}
		got = append(got, data.Data...)
	}
	if string(got) != "SSH-2.0-Zoo" {
		t.Fatalf("echo %q", got)
	}
	// the API closes it; then a target the node doesn't have is refused
	api.outbox <- &nodepb.ApiMessage{Body: &nodepb.ApiMessage_Close{Close: &nodepb.TunnelClose{Id: 7}}}
	api.outbox <- &nodepb.ApiMessage{Body: &nodepb.ApiMessage_Open{Open: &nodepb.TunnelOpen{Id: 8, Target: "unknown"}}}
	closed := wait(t, api.inbox).GetClose()
	if closed == nil || closed.Id != 8 || !strings.Contains(closed.Error, "no such target") {
		t.Fatalf("close %v", closed)
	}
	cancel()
	if err := wait(t, done); err != nil {
		t.Fatalf("run: %v", err)
	}
}

func TestNodeAppliesAVerifiedUpdate(t *testing.T) {
	ca := newCA(t)
	api, home, stop := startAPI(t, ca, 365)
	defer stop()
	exe := filepath.Join(t.TempDir(), "zoo-node")
	os.WriteFile(exe, []byte("old build"), 0o755)
	executable = func() (string, error) { return exe, nil }
	defer func() { executable = os.Executable }()
	_, done, cancel := runNode(t, home, map[string]target{})
	defer cancel()
	wait(t, api.hellos)

	build := []byte(strings.Repeat("new build ", 1000))
	sum := sha256.Sum256(build)
	chunk := func(data []byte, digest string, last bool) *nodepb.ApiMessage {
		return &nodepb.ApiMessage{Body: &nodepb.ApiMessage_Update{Update: &nodepb.UpdateChunk{
			Version: "2.0.0", Sha256: digest, Size: uint64(len(build)), Data: data, Last: last}}}
	}
	// a corrupted build is thrown away and the node keeps running
	api.outbox <- chunk(build[:10], strings.Repeat("0", 64), false)
	api.outbox <- chunk(build[10:], strings.Repeat("0", 64), true)
	time.Sleep(300 * time.Millisecond)
	if data, _ := os.ReadFile(exe); string(data) != "old build" {
		t.Fatal("swapped in a build with the wrong checksum")
	}
	select {
	case err := <-done:
		t.Fatalf("stopped after a bad update: %v", err)
	default:
	}
	api.outbox <- chunk(build[:4000], hex.EncodeToString(sum[:]), false)
	api.outbox <- chunk(build[4000:], hex.EncodeToString(sum[:]), true)
	if err := wait(t, done); !errors.Is(err, errUpdated) {
		t.Fatalf("run: %v", err)
	}
	if data, _ := os.ReadFile(exe); string(data) != string(build) {
		t.Fatal("the new build isn't in place")
	}
	if info, _ := os.Stat(exe); runtime.GOOS != "windows" && info.Mode().Perm() != 0o755 {
		t.Fatalf("mode %v", info.Mode())
	}
}

func TestNodeRenewsAnExpiringCertificate(t *testing.T) {
	ca := newCA(t)
	api, home, stop := startAPI(t, ca, 10) // expires within the 30-day window
	defer stop()
	_, _, certFile, _ := paths(home)
	before, _ := os.ReadFile(certFile)
	_, _, cancel := runNode(t, home, map[string]target{})
	defer cancel()
	wait(t, api.hellos)
	renew := wait(t, api.inbox).GetRenew()
	if renew == nil {
		t.Fatal("no renewal request")
	}
	api.outbox <- &nodepb.ApiMessage{Body: &nodepb.ApiMessage_Renewal{Renewal: &nodepb.Renewal{
		Certificate: ca.signCSR(t, renew.Csr, "node-1", 365)}}}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		after, _ := os.ReadFile(certFile)
		if string(after) != string(before) {
			if _, _, err := loadCredentials(home); err != nil {
				t.Fatalf("renewed pair doesn't load: %v", err)
			}
			if expiresWithin(home, renewBefore) {
				t.Fatal("still expiring")
			}
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatal("certificate not renewed")
}

func TestRenewalRefusesACertificateForAnotherNode(t *testing.T) {
	ca := newCA(t)
	_, home, stop := startAPI(t, ca, 10)
	defer stop()
	n := &Node{cfg: Config{NodeID: "node-1"}, home: home}
	key, csr, _ := newCSR()
	err := n.saveRenewal(key, &nodepb.Renewal{Certificate: ca.signCSR(t, csr, "node-2", 365)})
	if err == nil {
		t.Fatal("saved a certificate for another node")
	}
	if err := n.saveRenewal(key, &nodepb.Renewal{Error: "nope"}); err == nil || err.Error() != "nope" {
		t.Fatalf("error renewal: %v", err)
	}
}

func TestLoadCredentialsRecoversAnInterruptedRenewal(t *testing.T) {
	ca := newCA(t)
	_, home, stop := startAPI(t, ca, 10)
	defer stop()
	_, keyFile, certFile, _ := paths(home)
	// the new key is in place but the new certificate is still beside the old one
	key, csr, _ := newCSR()
	os.WriteFile(certFile+".new", ca.signCSR(t, csr, "node-1", 365), 0o600)
	os.WriteFile(keyFile, mustKey(t, key), 0o600)
	if _, _, err := loadCredentials(home); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(certFile + ".new"); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("left node.crt.new behind")
	}
}

func TestSandboxIDsSkipBaseVMs(t *testing.T) {
	got := sandboxIDs([]string{
		"zoo-0b0e4f6a-1c2d-4e5f-8a9b-0c1d2e3f4a5b\r",
		"zoo-macos-base",
		"zoo-windows-base",
		"",
		"other-0b0e4f6a-1c2d-4e5f-8a9b-0c1d2e3f4a5b",
	})
	if strings.Join(got, ",") != "0b0e4f6a-1c2d-4e5f-8a9b-0c1d2e3f4a5b" {
		t.Fatalf("got %v", got)
	}
}

func TestDiskCheck(t *testing.T) {
	if c := diskCheck(stats{diskTotal: 100 << 30, diskFree: 50 << 30}); !c.Ok {
		t.Fatal(c)
	}
	if c := diskCheck(stats{diskTotal: 100 << 30, diskFree: 1 << 30}); c.Ok {
		t.Fatal(c)
	}
	if c := diskCheck(stats{}); c.Ok {
		t.Fatal(c)
	}
}

func TestParseKeepsFlagsAfterArguments(t *testing.T) {
	fs := flagSet("join")
	service := fs.Bool("service", false, "")
	user := fs.String("ssh-user", "", "")
	rest, err := parse(fs, []string{"zn1.abc", "--service", "--ssh-user", "zoo"})
	if err != nil || len(rest) != 1 || rest[0] != "zn1.abc" || !*service || *user != "zoo" {
		t.Fatalf("%v %v %v %v", rest, err, *service, *user)
	}
}
