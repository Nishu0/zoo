package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"errors"
	"os"
	"path/filepath"
	"syscall"
)

func (configuration enrollment) bootstrap(mode, certificatePath string) error {
	if os.Geteuid() != 0 || !uuidPattern.MatchString(configuration.nodeUID) || configuration.frontendUID == 0 ||
		uint64(configuration.frontendUID) > uint64(^uint32(0)) || !filepath.IsAbs(configuration.directory) ||
		filepath.Clean(configuration.directory) != configuration.directory {
		return errors.New("root bootstrap, immutable node UID, frontend UID/GID and private absolute directory required")
	}
	if err := os.MkdirAll(configuration.directory, 0711); err != nil {
		return err
	}
	info, err := os.Lstat(configuration.directory)
	if err != nil {
		return err
	}
	owner, valid := info.Sys().(*syscall.Stat_t)
	if !valid || owner.Uid != 0 || !info.IsDir() || info.Mode().Perm()&0022 != 0 {
		return errors.New("root-owned private credential directory required")
	}
	keyData, err := os.ReadFile(filepath.Join(configuration.directory, "node-key.pem"))
	var private *ecdsa.PrivateKey
	if mode == "csr" && os.IsNotExist(err) {
		private, err = ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
		if err != nil {
			return err
		}
		encoded, err := x509.MarshalPKCS8PrivateKey(private)
		if err != nil {
			return err
		}
		keyData = pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: encoded})
	} else {
		if err != nil {
			return err
		}
		block, _ := pem.Decode(keyData)
		if block == nil {
			return errors.New("invalid local private key")
		}
		parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes)
		if err != nil {
			return err
		}
		private, valid = parsed.(*ecdsa.PrivateKey)
		if !valid || private.Curve != elliptic.P256() {
			return errors.New("local P-256 key required")
		}
	}
	if mode == "csr" {
		csr, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{Subject: pkix.Name{CommonName: configuration.nodeName}}, private)
		if err != nil {
			return err
		}
		if err := writeCredential(configuration.directory, "node-key.pem", keyData, configuration.frontendUID); err != nil {
			return err
		}
		return writeCredential(configuration.directory, "node.csr", pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: csr}), configuration.frontendUID)
	}
	certificate, err := os.ReadFile(certificatePath)
	if err != nil {
		return err
	}
	if len(certificate) > 8192 {
		return errors.New("bounded node certificate required")
	}
	pair, err := tls.X509KeyPair(certificate, keyData)
	if err != nil {
		return err
	}
	leaf, err := x509.ParseCertificate(pair.Certificate[0])
	if err != nil {
		return err
	}
	uri, err := certificateIdentity(leaf)
	expected := "spiffe://" + configuration.trustDomain + "/vm-control/node/" + configuration.nodeName + "/" + configuration.nodeUID
	if err != nil || uri != expected {
		return errors.New("offline node certificate identity mismatch")
	}
	ca, err := os.ReadFile(configuration.nodeCA)
	if err != nil {
		return err
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(ca) {
		return errors.New("pinned node authority required")
	}
	if configuration.serverName == "" {
		return errors.New("installation server DNS SAN required")
	}
	if _, err := leaf.Verify(x509.VerifyOptions{Roots: roots, DNSName: configuration.serverName, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}); err != nil {
		return err
	}
	if current, err := readNodeCredential(configuration.directory, configuration.nodeName); err == nil && current.NodeUID != configuration.nodeUID {
		return errors.New("refuse offline node generation rebind")
	} else if err != nil && !os.IsNotExist(err) {
		return err
	}
	identity := nodeCredential{NodeName: configuration.nodeName, NodeUID: configuration.nodeUID, Certificate: string(certificate)}
	encoded, err := json.Marshal(identity)
	if err != nil {
		return err
	}
	if err := writeCredential(configuration.directory, "node.pem", certificate, configuration.frontendUID); err != nil {
		return err
	}
	return writeCredential(configuration.directory, "identity.json", encoded, configuration.frontendUID)
}
