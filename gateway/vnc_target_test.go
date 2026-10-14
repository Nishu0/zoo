package main

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"golang.org/x/crypto/ssh"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type vncTargetTestDatabase struct {
	test       *testing.T
	ctx        context.Context
	operations []storeTestOperation
}

func (database *vncTargetTestDatabase) QueryRow(ctx context.Context, query string, arguments ...any) pgx.Row {
	database.test.Helper()
	if ctx != database.ctx || len(database.operations) == 0 {
		database.test.Fatal("unexpected screen database query or context")
	}
	operation := database.operations[0]
	database.operations = database.operations[1:]
	if query != operation.sql || !reflect.DeepEqual(arguments, operation.args) {
		database.test.Fatalf("screen SQL or parameters changed: %s %v", query, arguments)
	}
	return operation.row
}

func TestVNCTargetPlacementLookupAndLifecycle(test *testing.T) {
	for _, scenario := range []struct {
		name, state, kind, runtime string
		failure                    error
		code                       codes.Code
	}{
		{"linux", "running", "desktop", "container-id", nil, codes.OK},
		{"stopped", "stopped", "desktop", "container-id", nil, codes.FailedPrecondition},
		{"no-runtime", "running", "desktop", "", nil, codes.FailedPrecondition},
		{"code", "running", "code", "container-id", nil, codes.Unimplemented},
		{"absent", "", "", "", pgx.ErrNoRows, codes.NotFound},
		{"database", "", "", "", errors.New("database credentials must not leak"), codes.Unavailable},
	} {
		test.Run(scenario.name, func(test *testing.T) {
			ctx := test.Context()
			var expiry *time.Time
			database := &vncTargetTestDatabase{test: test, ctx: ctx, operations: []storeTestOperation{{sql: lookupVNCTargetSQL, args: []any{"sandbox"},
				row: storeTestRow{err: scenario.failure, values: []any{"sandbox", "workspace", scenario.state, scenario.kind, scenario.runtime, "{}", "", "", "", "", expiry}}}}}
			resolver := &vncTargetResolver{database: database}
			record, err := resolver.lookup(ctx, "sandbox")
			if status.Code(err) != scenario.code || err != nil && strings.Contains(err.Error(), "credentials must not leak") {
				test.Fatalf("lookup returned %v, %v", record, err)
			}
			if len(database.operations) != 0 {
				test.Fatal("lookup did not read native placement")
			}
		})
	}
}

func TestVNCTargetFixedVMAddressing(test *testing.T) {
	record := vncTargetRecord{Kind: "macos", ServerID: "host", RuntimeID: "macos:host:zoo-sandbox"}
	if name, err := vncRuntimeName(record); err != nil || name != "zoo-sandbox" {
		test.Fatalf("valid runtime rejected: %s, %v", name, err)
	}
	for _, value := range []string{"macos:other:zoo-sandbox", "windows:host:zoo-sandbox", "macos:host:../../file", "macos:host:vm;id", "macos:host:vm\nwhoami", "macos:host:vm:extra", "macos:host:-option", "macos:host:"} {
		record.RuntimeID = value
		if _, err := vncRuntimeName(record); status.Code(err) != codes.FailedPrecondition {
			test.Fatalf("unsafe runtime accepted: %s", value)
		}
	}
	for _, value := range []string{"vnc://:secret@127.0.0.1:59123", "vnc://:secret@localhost:59123", "vnc://:secret@[::1]:59123"} {
		address, password, err := vncMacAddress(value)
		if err != nil || password != "secret" || !strings.HasSuffix(address, ":59123") {
			test.Fatalf("Mac VNC target rejected: %s %s %v", address, password, err)
		}
	}
	for _, value := range []string{"vnc://192.0.2.2:5900", "vnc://host.example:5900", "https://127.0.0.1:5900", "vnc://127.0.0.1", "vnc://127.0.0.1:65536", "vnc://127.0.0.1:5900/path", "vnc://127.0.0.1:5900?host=other"} {
		if _, _, err := vncMacAddress(value); err == nil {
			test.Fatalf("unsafe Mac VNC target accepted: %s", value)
		}
	}
	if address, err := vncWindowsAddress(" 192.168.137.2\n"); err != nil || address != "192.168.137.2:5900" {
		test.Fatalf("guest IP was not resolved on fixed VNC port: %s %v", address, err)
	}
	for _, value := range []string{"127.0.0.1", "0.0.0.0", "224.0.0.1", "vm.example", "192.168.1.2:5901", "192.168.1.2\n192.168.1.3", "fe80::1%en0"} {
		if _, err := vncWindowsAddress(value); err == nil {
			test.Fatalf("unsafe guest IP accepted: %s", value)
		}
	}
	command, input := vncWindowsIPCommand("zoo-sandbox")
	if !strings.HasPrefix(command, "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ") || !strings.Contains(string(input[8:]), "'.zoovm\\zoovm.ps1') 'ip' 'zoo-sandbox'") {
		test.Fatal("Windows addressing did not use the fixed zoovm helper over stdin")
	}
	if password := vncWindowsPassword("The quick brown fox jumps over the lazy dog", "key"); password != "666ih5bq" {
		test.Fatalf("Windows password differs from existing Python HMAC/base32 format: %s", password)
	}
}

func TestVNCTargetEncryptedCredentialsScopeAndNoJWTFallback(test *testing.T) {
	ctx := test.Context()
	material := "screen-secrets-material"
	key := sha256.Sum256([]byte("screen data key"))
	encoded := []byte(base64.URLEncoding.EncodeToString(key[:]))
	ciphertext, err := encryptFernet([]byte("password"), encoded)
	if err != nil {
		test.Fatal(err)
	}
	wrapping := sha256.Sum256([]byte(material))
	wrapped, err := encryptFernet(encoded, []byte(base64.URLEncoding.EncodeToString(wrapping[:])))
	if err != nil {
		test.Fatal(err)
	}
	for _, secret := range []string{material, "wrong-secrets-key"} {
		database := &vncTargetTestDatabase{test: test, ctx: ctx, operations: []storeTestOperation{{sql: "SELECT wrapped FROM secret_keys WHERE id = $1 AND scope = ANY($2::text[])",
			args: []any{"key-id", []string{"system"}}, row: storeTestRow{values: []any{"local:" + wrapped}}}}}
		resolver := &vncTargetResolver{database: database, service: newGatewayServer(config{secretsKey: secret, jwtSecret: material})}
		plaintext, err := resolver.decrypt(ctx, "zk1:key-id:"+ciphertext, []string{"system"})
		if secret == material {
			if err != nil || string(plaintext) != "password" {
				test.Fatalf("encrypted credential could not be read: %v", err)
			}
		} else if status.Code(err) != codes.Unavailable {
			test.Fatalf("SSH credential used JWT as a silent key fallback: %v", err)
		}
	}
	resolver := &vncTargetResolver{}
	if _, err := resolver.decrypt(ctx, "plaintext-password", []string{"system"}); status.Code(err) != codes.FailedPrecondition {
		test.Fatal("plaintext or legacy SSH credential accepted")
	}
}

func TestVNCNodeSSHRequiresDBIdentityAndPinnedHostKey(test *testing.T) {
	ctx := test.Context()
	expires := time.Now().Add(time.Hour)
	record := vncTargetRecord{ServerID: "host", NodeURL: "node://admin@host", Serial: "current", ExpiresAt: &expires, HostKey: "not a host key"}
	resolver := &vncTargetResolver{}
	if _, err := resolver.nodeSSH(ctx, record); status.Code(err) != codes.FailedPrecondition {
		test.Fatalf("unpinned host reached the SSH transport: %v", err)
	}
	public, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		test.Fatal(err)
	}
	key, err := ssh.NewPublicKey(public)
	if err != nil {
		test.Fatal(err)
	}
	record.HostKey = strings.TrimSpace(string(ssh.MarshalAuthorizedKey(key)))
	resolver.database = &vncTargetTestDatabase{test: test, ctx: ctx, operations: []storeTestOperation{{sql: "SELECT key_ciphertext FROM ssh_identity WHERE id = 1", row: storeTestRow{err: pgx.ErrNoRows}}}}
	if _, err := resolver.nodeSSH(ctx, record); status.Code(err) != codes.Unavailable {
		test.Fatalf("missing DB identity did not fail before node dial: %v", err)
	}
	for _, endpoint := range []string{"ssh://admin@host", "node://admin@other", "node://admin@host:22", "node://host", "node://admin@host/path"} {
		record.NodeURL = endpoint
		if _, err := resolver.nodeSSH(ctx, record); status.Code(err) != codes.FailedPrecondition {
			test.Fatalf("unregistered SSH endpoint accepted: %s, %v", endpoint, err)
		}
	}
}
