package main

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"math/big"
	"net/url"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestFrontendReloadsNodeCertificateAndRejectsGenerationChange(test *testing.T) {
	directory := test.TempDir()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		test.Fatal(err)
	}
	encodedKey, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		test.Fatal(err)
	}
	keyPath := filepath.Join(directory, "node-key.pem")
	certificatePath := filepath.Join(directory, "node.pem")
	caPath := filepath.Join(directory, "controller-ca.pem")
	if err := os.WriteFile(keyPath, pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: encodedKey}), 0600); err != nil {
		test.Fatal(err)
	}
	expected := "spiffe://zoo.test/vm-control/node/node-a/" + testNodeUID
	writeLeaf := func(identity string, serial int64, expired bool) {
		uri, err := url.Parse(identity)
		if err != nil {
			test.Fatal(err)
		}
		validUntil := time.Now().Add(time.Hour)
		if expired {
			validUntil = time.Now().Add(-time.Minute)
		}
		leaf := &x509.Certificate{
			SerialNumber: big.NewInt(serial), Subject: pkix.Name{CommonName: "node-a"},
			NotBefore: time.Now().Add(-time.Hour), NotAfter: validUntil, URIs: []*url.URL{uri},
			ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		}
		encoded, err := x509.CreateCertificate(rand.Reader, leaf, leaf, key.Public(), key)
		if err != nil {
			test.Fatal(err)
		}
		data := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: encoded})
		if err := os.WriteFile(certificatePath, data, 0600); err != nil {
			test.Fatal(err)
		}
		if err := os.WriteFile(caPath, data, 0600); err != nil {
			test.Fatal(err)
		}
	}
	writeLeaf(expected, 1, false)
	configuration, err := serverTLS(certificatePath, keyPath, caPath, expected, map[string]bool{testController: true})
	if err != nil {
		test.Fatal(err)
	}
	first, err := configuration.GetCertificate(&tls.ClientHelloInfo{})
	if err != nil {
		test.Fatal(err)
	}
	writeLeaf(expected, 2, false)
	renewed, err := configuration.GetCertificate(&tls.ClientHelloInfo{})
	if err != nil || bytes.Equal(first.Certificate[0], renewed.Certificate[0]) {
		test.Fatal("rotated certificate was not reloaded")
	}
	writeLeaf("spiffe://zoo.test/vm-control/node/node-a/"+testPodUID, 3, false)
	if _, err := configuration.GetCertificate(&tls.ClientHelloInfo{}); err == nil {
		test.Fatal("different node generation accepted")
	}
	writeLeaf(expected, 4, true)
	if _, err := configuration.GetCertificate(&tls.ClientHelloInfo{}); err == nil {
		test.Fatal("expired node certificate accepted")
	}
}

func TestLocalCredentialRecordRequiresExactNodeGeneration(test *testing.T) {
	directory := test.TempDir()
	for _, identity := range []nodeCredential{
		{NodeName: "node-b", NodeUID: testNodeUID},
		{NodeName: "node-a", NodeUID: "arbitrary-path"},
	} {
		data, err := json.Marshal(identity)
		if err != nil {
			test.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(directory, "identity.json"), data, 0600); err != nil {
			test.Fatal(err)
		}
		if _, err := readNodeCredential(directory, "node-a"); err == nil {
			test.Fatal("unbound node credentials accepted")
		}
	}
}
