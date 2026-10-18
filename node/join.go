package main

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/user"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

type joinRequest struct {
	Token      string `json:"token"`
	CSR        string `json:"csr"`
	Hostname   string `json:"hostname"`
	OS         string `json:"os"`
	Arch       string `json:"arch"`
	SSHUser    string `json:"ssh_user,omitempty"`
	SSHHostKey string `json:"ssh_host_key,omitempty"`
}

type joinResponse struct {
	NodeID      string   `json:"node_id"`
	ServerID    string   `json:"server_id"`
	Certificate string   `json:"certificate"`
	CA          string   `json:"ca"`
	Endpoints   []string `json:"endpoints"`
}

// newCSR makes a key and a certificate request for it. The API names the certificate itself, so the subject here
// is only a placeholder.
func newCSR() (*ecdsa.PrivateKey, []byte, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, nil, err
	}
	der, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{
		Subject: pkix.Name{CommonName: "zoo-node"},
	}, key)
	if err != nil {
		return nil, nil, err
	}
	return key, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: der}), nil
}

var hostKeyFile = func() string {
	if runtime.GOOS == "windows" {
		return filepath.Join(os.Getenv("ProgramData"), "ssh", "ssh_host_ed25519_key.pub")
	}
	return "/etc/ssh/ssh_host_ed25519_key.pub"
}

// sshHostKey is the host's ed25519 SSH key, for enrolled hosts, which the API reaches over SSH through
// the node and checks like any SSH host.
func sshHostKey() string {
	data, err := os.ReadFile(hostKeyFile())
	if err != nil {
		return ""
	}
	fields := strings.Fields(string(data))
	if len(fields) < 2 {
		return ""
	}
	return fields[0] + " " + fields[1]
}

func join(ctx context.Context, raw, home, sshUser string) (Config, error) {
	if runtime.GOOS != "linux" && runtime.GOOS != "darwin" && runtime.GOOS != "windows" {
		return Config{}, errors.New("zoo-node supports Linux, macOS and Windows hosts")
	}
	token, err := decodeToken(raw)
	if err != nil {
		return Config{}, err
	}
	key, csr, err := newCSR()
	if err != nil {
		return Config{}, err
	}
	hostname, _ := os.Hostname()
	req := joinRequest{Token: raw, CSR: string(csr), Hostname: hostname, OS: runtime.GOOS, Arch: runtime.GOARCH}
	if sshUser == "" && runtime.GOOS != "windows" {
		if u, err := user.Current(); err == nil {
			sshUser = u.Username
		}
	}
	if sshUser == "" {
		return Config{}, errors.New("give --ssh-user: the account the control plane signs in to this host as")
	}
	req.SSHUser = sshUser
	req.SSHHostKey = sshHostKey()
	if req.SSHHostKey == "" {
		return Config{}, errors.New("this host has no SSH host key; turn on its SSH server (the node installer does)")
	}
	resp, err := postJoin(ctx, token.API, req)
	if err != nil {
		return Config{}, err
	}
	if err := checkJoin(resp, token.Fingerprint, &key.PublicKey); err != nil {
		return Config{}, err
	}
	keyPEM, err := encodeKey(key)
	if err != nil {
		return Config{}, err
	}
	_, keyFile, certFile, caFile := paths(home)
	for file, data := range map[string][]byte{keyFile: keyPEM, certFile: []byte(resp.Certificate), caFile: []byte(resp.CA)} {
		if err := writeFile(file, data); err != nil {
			return Config{}, err
		}
	}
	cfg := Config{NodeID: resp.NodeID, ServerID: resp.ServerID, API: token.API, Endpoints: resp.Endpoints}
	return cfg, saveConfig(home, cfg)
}

func postJoin(ctx context.Context, api string, req joinRequest) (joinResponse, error) {
	var out joinResponse
	body, err := json.Marshal(req)
	if err != nil {
		return out, err
	}
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(api, "/")+"/v1/nodes/join", bytes.NewReader(body))
	if err != nil {
		return out, err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(httpReq)
	if err != nil {
		return out, fmt.Errorf("can't reach the control plane at %s: %w", api, err)
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		var detail struct {
			Detail any `json:"detail"`
		}
		if json.Unmarshal(data, &detail) == nil && detail.Detail != nil {
			return out, fmt.Errorf("join refused: %v", detail.Detail)
		}
		return out, fmt.Errorf("join refused: %s", resp.Status)
	}
	if err := json.Unmarshal(data, &out); err != nil {
		return out, fmt.Errorf("unexpected join response: %w", err)
	}
	return out, nil
}

// checkJoin makes sure the CA is the one the token names (so a man in the middle of the join request can't slip in
// its own) and that the certificate is for our key and signed by that CA.
func checkJoin(resp joinResponse, fingerprint string, key *ecdsa.PublicKey) error {
	caBlock, _ := pem.Decode([]byte(resp.CA))
	if caBlock == nil {
		return errors.New("the control plane sent no CA certificate")
	}
	sum := sha256.Sum256(caBlock.Bytes)
	if hex.EncodeToString(sum[:]) != strings.ToLower(fingerprint) {
		return errors.New("the control plane's CA doesn't match the join token; is something intercepting the connection?")
	}
	ca, err := x509.ParseCertificate(caBlock.Bytes)
	if err != nil {
		return err
	}
	certBlock, _ := pem.Decode([]byte(resp.Certificate))
	if certBlock == nil {
		return errors.New("the control plane sent no certificate")
	}
	cert, err := x509.ParseCertificate(certBlock.Bytes)
	if err != nil {
		return err
	}
	if err := cert.CheckSignatureFrom(ca); err != nil {
		return fmt.Errorf("the certificate isn't signed by the node CA: %w", err)
	}
	if pub, ok := cert.PublicKey.(*ecdsa.PublicKey); !ok || !pub.Equal(key) {
		return errors.New("the certificate isn't for this node's key")
	}
	if resp.NodeID == "" || cert.Subject.CommonName != resp.NodeID || len(resp.Endpoints) == 0 {
		return errors.New("incomplete join response")
	}
	return nil
}
