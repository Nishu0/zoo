package main

import (
	"crypto/ecdsa"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const tokenPrefix = "zn1."

// Config is what joining leaves behind, next to the key and certificates.
type Config struct {
	NodeID    string   `json:"node_id"`
	ServerID  string   `json:"server_id"`
	API       string   `json:"api"`
	Endpoints []string `json:"endpoints"`
}

// Token is a decoded join token: the API's URL, the one-time secret and the node CA's fingerprint.
type Token struct {
	API         string `json:"u"`
	Secret      string `json:"s"`
	Fingerprint string `json:"f"`
}

func decodeToken(raw string) (Token, error) {
	var t Token
	raw = strings.TrimSpace(raw)
	if !strings.HasPrefix(raw, tokenPrefix) {
		return t, errors.New("not a zoo-node join token")
	}
	body, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(raw[len(tokenPrefix):], "="))
	if err != nil {
		return t, errors.New("not a zoo-node join token")
	}
	if err := json.Unmarshal(body, &t); err != nil || t.API == "" || t.Secret == "" || t.Fingerprint == "" {
		return t, errors.New("not a zoo-node join token")
	}
	return t, nil
}

// configDir is where the node keeps its state: --dir, ZOO_NODE_DIR, or the platform's place for it.
func configDir(flagged string) string {
	if flagged != "" {
		return flagged
	}
	if env := os.Getenv("ZOO_NODE_DIR"); env != "" {
		return env
	}
	if runtime.GOOS == "windows" {
		return filepath.Join(os.Getenv("ProgramData"), "zoo-node")
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".zoo-node")
}

func paths(home string) (config, key, cert, ca string) {
	return filepath.Join(home, "config.json"), filepath.Join(home, "node.key"),
		filepath.Join(home, "node.crt"), filepath.Join(home, "ca.crt")
}

func loadConfig(home string) (Config, error) {
	var cfg Config
	file, _, _, _ := paths(home)
	data, err := os.ReadFile(file)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return cfg, fmt.Errorf("no node config in %s; run `zoo-node join <token>` first", home)
		}
		return cfg, err
	}
	if err := json.Unmarshal(data, &cfg); err != nil {
		return cfg, fmt.Errorf("%s: %w", file, err)
	}
	if cfg.NodeID == "" || len(cfg.Endpoints) == 0 {
		return cfg, fmt.Errorf("%s is incomplete; join again", file)
	}
	return cfg, nil
}

func saveConfig(home string, cfg Config) error {
	file, _, _, _ := paths(home)
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	return writeFile(file, data)
}

// writeFile replaces a file atomically, readable only by its owner.
func writeFile(path string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func encodeKey(key *ecdsa.PrivateKey) ([]byte, error) {
	der, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		return nil, err
	}
	return pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: der}), nil
}

// loadCredentials loads the node's certificate and the CA to check the API against. Read on every dial, so a renewed
// certificate takes effect on the next connection.
func loadCredentials(home string) (tls.Certificate, *x509.CertPool, error) {
	_, key, cert, _ := paths(home)
	pair, err := tls.LoadX509KeyPair(cert, key)
	if err != nil {
		// a renewal stopped between swapping in the key and the certificate
		if renewed, err2 := tls.LoadX509KeyPair(cert+".new", key); err2 == nil {
			pair, err = renewed, os.Rename(cert+".new", cert)
		}
		if err != nil {
			return pair, nil, err
		}
	}
	pool, err := caPool(home)
	return pair, pool, err
}

func caPool(home string) (*x509.CertPool, error) {
	_, _, _, ca := paths(home)
	data, err := os.ReadFile(ca)
	if err != nil {
		return nil, err
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(data) {
		return nil, errors.New("ca.crt has no certificate")
	}
	return pool, nil
}

// expiresWithin says whether the node's certificate runs out within d.
func expiresWithin(home string, d time.Duration) bool {
	_, _, cert, _ := paths(home)
	data, err := os.ReadFile(cert)
	if err != nil {
		return false
	}
	block, _ := pem.Decode(data)
	if block == nil {
		return false
	}
	parsed, err := x509.ParseCertificate(block.Bytes)
	return err == nil && time.Until(parsed.NotAfter) < d
}
