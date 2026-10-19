package main

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

type enrollment struct {
	url, issuerCA, nodeCA, tokenPath, directory, serverName string
	nodeName, podName, podUID, trustDomain                  string
	frontendUID                                             uint
	backend, nodeUID                                        string
}

type nodeCredential struct {
	NodeName    string `json:"node_name"`
	NodeUID     string `json:"node_uid"`
	Certificate string `json:"certificate"`
}

func readNodeCredential(directory, nodeName string) (nodeCredential, error) {
	var identity nodeCredential
	data, err := os.ReadFile(filepath.Join(directory, "identity.json"))
	if err != nil {
		return identity, err
	}
	if len(data) > 8192 {
		return identity, errors.New("credential identity too large")
	}
	if err := json.Unmarshal(data, &identity); err != nil {
		return identity, err
	}
	if identity.NodeName != nodeName || !uuidPattern.MatchString(identity.NodeUID) {
		return identity, errors.New("credential generation identity mismatch")
	}
	return identity, nil
}

func writeCredential(directory, name string, data []byte, uid uint) error {
	file, err := os.CreateTemp(directory, ".credential-")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	defer file.Close()
	if err := file.Chmod(0640); err != nil {
		return err
	}
	if _, err := file.Write(data); err != nil {
		return err
	}
	if err := file.Chown(0, int(uid)); err != nil {
		return err
	}
	if err := file.Sync(); err != nil {
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return os.Rename(file.Name(), filepath.Join(directory, name))
}

func (configuration enrollment) provision(ctx context.Context) (nodeCredential, error) {
	var identity nodeCredential
	endpoint, err := url.Parse(configuration.url)
	if err != nil || endpoint.Scheme != "https" || endpoint.Host == "" || endpoint.User != nil || endpoint.RawQuery != "" || endpoint.Fragment != "" || endpoint.Path != "/v1/vm-control/certificates" {
		return identity, errors.New("explicit HTTPS enrollment issuer route required")
	}
	if !filepath.IsAbs(configuration.directory) || filepath.Clean(configuration.directory) != configuration.directory ||
		configuration.frontendUID == 0 || uint64(configuration.frontendUID) > uint64(^uint32(0)) || configuration.serverName == "" {
		return identity, errors.New("private credential directory, frontend UID and Pod-bound enrollment identity required")
	}
	if configuration.backend != "kube" && configuration.backend != "docker" {
		return identity, errors.New("explicit enrollment runtime required")
	}
	if configuration.backend == "kube" && (!nodePattern.MatchString(configuration.podName) || !uuidPattern.MatchString(configuration.podUID)) {
		return identity, errors.New("Pod-bound enrollment identity required")
	}
	if configuration.backend == "docker" && !uuidPattern.MatchString(configuration.nodeUID) {
		return identity, errors.New("pinned single-host Docker node generation required")
	}
	if err := os.MkdirAll(configuration.directory, 0711); err != nil {
		return identity, err
	}
	info, err := os.Lstat(configuration.directory)
	if err != nil || !info.IsDir() || info.Mode().Perm()&0022 != 0 {
		return identity, errors.New("credential directory must not be writable by frontend")
	}
	owner, valid := info.Sys().(*syscall.Stat_t)
	if !valid || owner.Uid != 0 {
		return identity, errors.New("credential directory must be root-owned")
	}
	keyPath := filepath.Join(configuration.directory, "node-key.pem")
	var private *ecdsa.PrivateKey
	keyData, err := os.ReadFile(keyPath)
	if err == nil {
		block, _ := pem.Decode(keyData)
		if block == nil {
			return identity, errors.New("invalid local node key")
		}
		parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes)
		if err != nil {
			return identity, err
		}
		var valid bool
		private, valid = parsed.(*ecdsa.PrivateKey)
		if !valid || private.Curve != elliptic.P256() {
			return identity, errors.New("P-256 local node key required")
		}
	} else if os.IsNotExist(err) {
		private, err = ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
		if err != nil {
			return identity, err
		}
		encoded, err := x509.MarshalPKCS8PrivateKey(private)
		if err != nil {
			return identity, err
		}
		keyData = pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: encoded})
	} else {
		return identity, err
	}
	csr, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{Subject: pkix.Name{CommonName: configuration.nodeName}}, private)
	if err != nil {
		return identity, err
	}
	token, err := os.ReadFile(configuration.tokenPath)
	if err != nil || len(token) == 0 || len(token) > 16384 {
		return identity, errors.New("bounded projected enrollment token required")
	}
	issuerCA, err := os.ReadFile(configuration.issuerCA)
	if err != nil {
		return identity, err
	}
	issuerRoots := x509.NewCertPool()
	if !issuerRoots.AppendCertsFromPEM(issuerCA) {
		return identity, errors.New("issuer HTTPS authority required")
	}
	client := &http.Client{
		Timeout:       15 * time.Second,
		Transport:     &http.Transport{TLSClientConfig: &tls.Config{RootCAs: issuerRoots, MinVersion: tls.VersionTLS13}, DisableKeepAlives: true},
		CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("enrollment redirect rejected") },
	}
	defer client.CloseIdleConnections()
	values := map[string]string{
		"node_name": configuration.nodeName, "pod_name": configuration.podName, "pod_uid": configuration.podUID,
		"csr": string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: csr})),
	}
	if configuration.backend == "docker" {
		delete(values, "pod_name")
		delete(values, "pod_uid")
		values["node_uid"] = configuration.nodeUID
	}
	body, err := json.Marshal(values)
	if err != nil {
		return identity, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, configuration.url, bytes.NewReader(body))
	if err != nil {
		return identity, err
	}
	request.Header.Set("Authorization", "Bearer "+strings.TrimSpace(string(token)))
	request.Header.Set("Content-Type", "application/json")
	response, err := client.Do(request)
	if err != nil {
		return identity, errors.New("authenticated enrollment issuer unavailable")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return identity, fmt.Errorf("enrollment rejected with status %d", response.StatusCode)
	}
	decoder := json.NewDecoder(io.LimitReader(response.Body, 8193))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&identity); err != nil || decoder.Decode(new(any)) != io.EOF {
		return identity, errors.New("invalid enrollment response")
	}
	if identity.NodeName != configuration.nodeName || !uuidPattern.MatchString(identity.NodeUID) || (configuration.backend == "docker" && identity.NodeUID != configuration.nodeUID) {
		return identity, errors.New("enrollment node generation mismatch")
	}
	if current, err := readNodeCredential(configuration.directory, configuration.nodeName); err == nil && current.NodeUID != identity.NodeUID {
		return identity, errors.New("node generation changed; refuse certificate rebind")
	} else if err != nil && !os.IsNotExist(err) {
		return identity, err
	}
	pair, err := tls.X509KeyPair([]byte(identity.Certificate), keyData)
	if err != nil {
		return identity, err
	}
	leaf, err := x509.ParseCertificate(pair.Certificate[0])
	if err != nil {
		return identity, err
	}
	uri, err := certificateIdentity(leaf)
	expected := "spiffe://" + configuration.trustDomain + "/vm-control/node/" + configuration.nodeName + "/" + identity.NodeUID
	if err != nil || uri != expected {
		return identity, errors.New("issued node identity mismatch")
	}
	nodeCA, err := os.ReadFile(configuration.nodeCA)
	if err != nil {
		return identity, err
	}
	nodeRoots := x509.NewCertPool()
	if !nodeRoots.AppendCertsFromPEM(nodeCA) {
		return identity, errors.New("pinned node authority required")
	}
	if _, err := leaf.Verify(x509.VerifyOptions{Roots: nodeRoots, DNSName: configuration.serverName, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}); err != nil {
		return identity, err
	}
	if leaf.NotAfter.After(time.Now().Add(13*time.Hour)) || time.Until(leaf.NotAfter) < time.Hour {
		return identity, errors.New("short-lived node certificate required")
	}
	if err := writeCredential(configuration.directory, "node-key.pem", keyData, configuration.frontendUID); err != nil {
		return identity, err
	}
	if err := writeCredential(configuration.directory, "node.pem", []byte(identity.Certificate), configuration.frontendUID); err != nil {
		return identity, err
	}
	encoded, err := json.Marshal(identity)
	if err != nil {
		return identity, err
	}
	if err := writeCredential(configuration.directory, "identity.json", encoded, configuration.frontendUID); err != nil {
		return identity, err
	}
	return identity, nil
}

func (configuration enrollment) renew(ctx context.Context) {
	ticker := time.NewTicker(30 * time.Minute)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if _, err := configuration.provision(ctx); err != nil {
				log.Printf("node certificate renewal unavailable node=%s", configuration.nodeName)
			}
		}
	}
}
