package main

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base32"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/netip"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf16"

	"github.com/jackc/pgx/v5"
	"golang.org/x/crypto/ssh"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

const lookupVNCTargetSQL = `
SELECT b.id, b.workspace_id, b.status, b.kind, COALESCE(b.runtime_id, ''), b.config,
    COALESCE(b.server_id, ''), COALESCE(s.docker_url, ''), COALESCE(n.ssh_host_key, ''),
    COALESCE(n.serial, ''), n.cert_expires_at
FROM sandboxes b LEFT JOIN servers s ON s.id = b.server_id
    LEFT JOIN nodes n ON n.server_id = s.id
WHERE b.id = $1 AND b.deleted_at IS NULL`

// a Mac or Windows host's base VM, watched by its admins while they set it up: "base:<server id>"
const lookupBaseVNCTargetSQL = `
SELECT s.workspace_id, s.platform, s.docker_url, COALESCE(n.ssh_host_key, ''), COALESCE(n.serial, ''),
    n.cert_expires_at
FROM servers s LEFT JOIN nodes n ON n.server_id = s.id
WHERE s.id = $1`

var vncVMName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`)

type vncTargetDatabase interface {
	QueryRow(context.Context, string, ...any) pgx.Row
}

type vncTargetRecord struct {
	SandboxID   string
	WorkspaceID string
	Status      string
	Kind        string
	RuntimeID   string
	Config      string
	ServerID    string
	NodeURL     string
	HostKey     string
	Serial      string
	ExpiresAt   *time.Time
}

func (record vncTargetRecord) sameTarget(other vncTargetRecord) bool {
	current := other.Kind != "macos" && other.Kind != "windows" || other.ExpiresAt != nil && time.Now().Before(*other.ExpiresAt)
	return current && record.SandboxID == other.SandboxID && record.RuntimeID == other.RuntimeID && record.Kind == other.Kind &&
		record.ServerID == other.ServerID && record.NodeURL == other.NodeURL && record.HostKey == other.HostKey &&
		record.Serial == other.Serial && record.Config == other.Config && other.Status == "running"
}

type vncTargetResolver struct {
	database vncTargetDatabase
	service  *gatewayServer
}

func (resolver *vncTargetResolver) lookup(ctx context.Context, sandboxID string) (vncTargetRecord, error) {
	if serverID, base := strings.CutPrefix(sandboxID, "base:"); base {
		return resolver.lookupBase(ctx, sandboxID, serverID)
	}
	var record vncTargetRecord
	err := resolver.database.QueryRow(ctx, lookupVNCTargetSQL, sandboxID).Scan(&record.SandboxID, &record.WorkspaceID,
		&record.Status, &record.Kind, &record.RuntimeID, &record.Config, &record.ServerID, &record.NodeURL, &record.HostKey,
		&record.Serial, &record.ExpiresAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return record, status.Error(codes.NotFound, "sandbox not found")
	}
	if err != nil {
		return record, status.Error(codes.Unavailable, "sandbox screen placement could not be read")
	}
	if record.Status != "running" || record.RuntimeID == "" {
		return record, status.Error(codes.FailedPrecondition, "the sandbox is not running")
	}
	if record.Kind == "code" {
		return record, status.Error(codes.Unimplemented, "code sandboxes have no screen")
	}
	if len(record.Config) > 1<<20 {
		return record, status.Error(codes.FailedPrecondition, "sandbox screen configuration is invalid")
	}
	return record, nil
}

func (resolver *vncTargetResolver) lookupBase(ctx context.Context, identity, serverID string) (vncTargetRecord, error) {
	record := vncTargetRecord{SandboxID: identity, ServerID: serverID, Status: "running"}
	err := resolver.database.QueryRow(ctx, lookupBaseVNCTargetSQL, serverID).Scan(&record.WorkspaceID, &record.Kind,
		&record.NodeURL, &record.HostKey, &record.Serial, &record.ExpiresAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return record, status.Error(codes.NotFound, "server not found")
	}
	if err != nil {
		return record, status.Error(codes.Unavailable, "server screen placement could not be read")
	}
	base := map[string]string{"macos": resolver.service.settings.macosBase, "windows": resolver.service.settings.windowsBase}[record.Kind]
	if base == "" {
		return record, status.Error(codes.FailedPrecondition, "only macOS and Windows hosts have a base VM")
	}
	record.RuntimeID = record.Kind + ":" + serverID + ":" + base
	return record, nil
}

func (resolver *vncTargetResolver) decrypt(ctx context.Context, ciphertext string, scopes []string) ([]byte, error) {
	keyID, token, err := envelopeParts(ciphertext)
	if err != nil {
		return nil, status.Error(codes.FailedPrecondition, "screen credentials require an encrypted secret envelope")
	}
	var wrapped string
	if err := resolver.database.QueryRow(ctx, "SELECT wrapped FROM secret_keys WHERE id = $1 AND scope = ANY($2::text[])", keyID, scopes).Scan(&wrapped); err != nil {
		return nil, status.Error(codes.Unavailable, "screen credential data key is unavailable")
	}
	settings := resolver.service.settings
	key, err := unwrapDataKey(ctx, wrapped, append([]string{settings.secretsKey}, settings.previousKeys...))
	if err != nil {
		return nil, status.Error(codes.Unavailable, "screen credential data key could not be unwrapped")
	}
	plaintext, err := decryptFernet(token, key)
	if err != nil {
		return nil, status.Error(codes.FailedPrecondition, "screen credentials could not be decrypted")
	}
	return plaintext, nil
}

func (resolver *vncTargetResolver) open(ctx context.Context, record vncTargetRecord) (io.ReadWriteCloser, string, error) {
	if record.Kind != "macos" && record.Kind != "windows" {
		if strings.HasPrefix(record.RuntimeID, "macos:") || strings.HasPrefix(record.RuntimeID, "windows:") {
			return nil, "", status.Error(codes.FailedPrecondition, "sandbox screen placement is inconsistent")
		}
		var configuration struct {
			Password string `json:"vnc_password"`
		}
		if json.Unmarshal([]byte(record.Config), &configuration) != nil || configuration.Password == "" {
			return nil, "", status.Error(codes.FailedPrecondition, "the sandbox has no encrypted VNC password")
		}
		password, err := resolver.decrypt(ctx, configuration.Password, []string{record.WorkspaceID, "system"})
		if err != nil {
			return nil, "", err
		}
		if len(password) > 1024 {
			return nil, "", status.Error(codes.FailedPrecondition, "invalid sandbox VNC password")
		}
		transport, err := dialGuestVNC(ctx, resolver.service, record.SandboxID)
		return transport, string(password), err
	}
	name, err := vncRuntimeName(record)
	if err != nil {
		return nil, "", err
	}
	client, err := resolver.nodeSSH(ctx, record)
	if err != nil {
		return nil, "", err
	}
	var address, password string
	if record.Kind == "macos" {
		output, err := vncSSHOutput(ctx, client, `export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; zoovm vnc `+name, nil)
		if err == nil {
			address, password, err = vncMacAddress(output)
		}
		if err != nil {
			client.Close()
			return nil, "", err
		}
	} else {
		command, stdin := vncWindowsIPCommand(name)
		output, err := vncSSHOutput(ctx, client, command, stdin)
		if err != nil {
			client.Close()
			return nil, "", err
		}
		address, err = vncWindowsAddress(output)
		if err != nil {
			client.Close()
			return nil, "", err
		}
		password = vncWindowsPassword(record.RuntimeID, resolver.service.settings.jwtSecret)
	}
	channel, err := client.DialContext(ctx, "tcp", address)
	if err != nil {
		client.Close()
		return nil, "", status.Error(codes.Unavailable, "the host cannot reach the sandbox VNC service")
	}
	return &vncSSHChannel{Conn: channel, client: client}, password, nil
}

func vncRuntimeName(record vncTargetRecord) (string, error) {
	parts := strings.Split(record.RuntimeID, ":")
	if len(parts) != 3 || parts[0] != record.Kind || parts[1] != record.ServerID || record.ServerID == "" || !vncVMName.MatchString(parts[2]) {
		return "", status.Error(codes.FailedPrecondition, "invalid VM screen placement")
	}
	return parts[2], nil
}

func (resolver *vncTargetResolver) nodeSSH(ctx context.Context, record vncTargetRecord) (*ssh.Client, error) {
	endpoint, err := url.Parse(record.NodeURL)
	if err != nil || endpoint.Scheme != "node" || endpoint.Hostname() != record.ServerID || endpoint.Port() != "" || endpoint.User == nil || endpoint.User.Username() == "" || endpoint.Path != "" || endpoint.RawQuery != "" || endpoint.Fragment != "" {
		return nil, status.Error(codes.FailedPrecondition, "VM screens require a registered node SSH endpoint")
	}
	if record.Serial == "" || record.ExpiresAt == nil || !time.Now().Before(*record.ExpiresAt) || len(record.HostKey) > 64<<10 {
		return nil, status.Error(codes.Unauthenticated, "the VM host node registration is not current")
	}
	hostKey, _, _, rest, err := ssh.ParseAuthorizedKey([]byte(record.HostKey))
	if err != nil || len(rest) != 0 {
		return nil, status.Error(codes.FailedPrecondition, "the VM host has no valid pinned SSH host key")
	}
	var encrypted string
	if err := resolver.database.QueryRow(ctx, "SELECT key_ciphertext FROM ssh_identity WHERE id = 1").Scan(&encrypted); err != nil {
		return nil, status.Error(codes.Unavailable, "the shared host SSH identity is unavailable")
	}
	identity, err := resolver.decrypt(ctx, encrypted, []string{"system"})
	if err != nil {
		return nil, err
	}
	if len(identity) > 64<<10 {
		return nil, status.Error(codes.FailedPrecondition, "invalid shared host SSH identity")
	}
	signer, err := ssh.ParsePrivateKey(identity)
	if err != nil {
		return nil, status.Error(codes.FailedPrecondition, "the shared host SSH identity is invalid")
	}
	transport, err := dialNodeSSHTransport(ctx, resolver.service, record.ServerID)
	if err != nil {
		return nil, err
	}
	handshake, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	stop := context.AfterFunc(handshake, func() { transport.Close() })
	connection, channels, requests, err := ssh.NewClientConn(transport, net.JoinHostPort(record.ServerID, "22"), &ssh.ClientConfig{
		User: endpoint.User.Username(), Auth: []ssh.AuthMethod{ssh.PublicKeys(signer)}, HostKeyCallback: ssh.FixedHostKey(hostKey), Timeout: 15 * time.Second,
	})
	stopped := stop()
	if err != nil || !stopped {
		transport.Close()
		if connection != nil {
			connection.Close()
		}
		return nil, status.Error(codes.Unavailable, "the node SSH identity or pinned host key could not be authenticated")
	}
	return ssh.NewClient(connection, channels, requests), nil
}

type vncSSHChannel struct {
	net.Conn
	client *ssh.Client
	once   sync.Once
}

func (channel *vncSSHChannel) Close() error {
	channel.once.Do(func() { channel.Conn.Close(); channel.client.Close() })
	return nil
}

type vncBoundedOutput struct {
	data bytes.Buffer
	max  int
}

func (output *vncBoundedOutput) Write(data []byte) (int, error) {
	if len(data) > output.max-output.data.Len() {
		return 0, errors.New("screen target command output exceeds its limit")
	}
	return output.data.Write(data)
}

func vncSSHOutput(ctx context.Context, client *ssh.Client, command string, stdin []byte) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	stop := context.AfterFunc(ctx, func() { client.Close() })
	defer stop()
	session, err := client.NewSession()
	if err != nil {
		return "", status.Error(codes.Unavailable, "the node SSH command channel is unavailable")
	}
	defer session.Close()
	output, failure := &vncBoundedOutput{max: 16 << 10}, &vncBoundedOutput{max: 4 << 10}
	session.Stdout, session.Stderr, session.Stdin = output, failure, bytes.NewReader(stdin)
	if err := session.Run(command); err != nil {
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
		return "", status.Error(codes.Unavailable, "the node could not resolve its sandbox VNC target")
	}
	return strings.TrimSpace(output.data.String()), nil
}

func vncMacAddress(value string) (string, string, error) {
	endpoint, err := url.Parse(strings.TrimSpace(value))
	if err != nil || endpoint.Scheme != "vnc" || endpoint.Path != "" && endpoint.Path != "/" || endpoint.RawQuery != "" || endpoint.Fragment != "" {
		return "", "", status.Error(codes.FailedPrecondition, "zoovm returned an invalid VNC endpoint")
	}
	host := endpoint.Hostname()
	address, parseErr := netip.ParseAddr(host)
	if host != "localhost" && (parseErr != nil || !address.IsLoopback()) {
		return "", "", status.Error(codes.FailedPrecondition, "zoovm VNC must listen on host loopback")
	}
	port, err := strconv.Atoi(endpoint.Port())
	if err != nil || port < 1 || port > 65535 {
		return "", "", status.Error(codes.FailedPrecondition, "zoovm returned an invalid VNC port")
	}
	password := ""
	if endpoint.User != nil {
		password, _ = endpoint.User.Password()
	}
	if len(password) > 1024 {
		return "", "", status.Error(codes.FailedPrecondition, "zoovm returned an invalid VNC password")
	}
	if host == "localhost" {
		host = "127.0.0.1"
	}
	return net.JoinHostPort(host, strconv.Itoa(port)), password, nil
}

func vncWindowsAddress(value string) (string, error) {
	address, err := netip.ParseAddr(strings.TrimSpace(value))
	if err != nil || !address.IsGlobalUnicast() && !address.IsLinkLocalUnicast() || address.Zone() != "" || address.IsLoopback() {
		return "", status.Error(codes.FailedPrecondition, "zoovm returned an invalid guest IP")
	}
	return net.JoinHostPort(address.String(), "5900"), nil
}

func vncWindowsPassword(runtimeID, secret string) string {
	digest := hmac.New(sha256.New, []byte(secret))
	digest.Write([]byte(runtimeID))
	return strings.ToLower(base32.StdEncoding.EncodeToString(digest.Sum(nil)))[:8]
}

func vncWindowsIPCommand(name string) (string, []byte) {
	bootstrap := `$i=[Console]::OpenStandardInput();$h=New-Object byte[] 8;$n=0;while($n -lt 8){$r=$i.Read($h,$n,8-$n);if($r -le 0){exit 2};$n+=$r};$l=[int][Text.Encoding]::ASCII.GetString($h);$s=New-Object byte[] $l;$n=0;while($n -lt $l){$r=$i.Read($s,$n,$l-$n);if($r -le 0){exit 2};$n+=$r};$ZooIn=$i;. ([ScriptBlock]::Create([Text.Encoding]::UTF8.GetString($s)))`
	encoded := utf16.Encode([]rune(bootstrap))
	data := make([]byte, len(encoded)*2)
	for index, value := range encoded {
		data[index*2], data[index*2+1] = byte(value), byte(value>>8)
	}
	command := "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand " + base64.StdEncoding.EncodeToString(data)
	script := "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)\n" +
		"try { & (Join-Path $env:USERPROFILE '.zoovm\\zoovm.ps1') 'ip' '" + name + "'; exit $LASTEXITCODE } catch { exit 1 }\n"
	return command, []byte(fmt.Sprintf("%08d%s", len([]byte(script)), script))
}
