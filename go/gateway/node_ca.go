package main

import (
	"crypto"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"math/big"
	"net"
	"time"
)

type nodeAuthority struct {
	certificate *x509.Certificate
	key         crypto.Signer
}

func newNodeAuthority(certificate *x509.Certificate, key crypto.Signer) (*nodeAuthority, error) {
	if certificate == nil || key == nil || !certificate.IsCA || certificate.KeyUsage&x509.KeyUsageCertSign == 0 {
		return nil, errors.New("a node CA certificate and signing key are required")
	}
	publicKey, ok := key.Public().(*ecdsa.PublicKey)
	if !ok || !publicKey.Equal(certificate.PublicKey) {
		return nil, errors.New("the node CA requires its matching ECDSA key")
	}
	if now := time.Now(); now.Before(certificate.NotBefore) || !now.Before(certificate.NotAfter) {
		return nil, errors.New("the node CA is not currently valid")
	}
	return &nodeAuthority{certificate: certificate, key: key}, nil
}

func (authority *nodeAuthority) issue(publicKey *ecdsa.PublicKey, commonName string, names []string) ([]byte, *x509.Certificate, error) {
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 159))
	if err != nil {
		return nil, nil, err
	}
	serial.Add(serial, big.NewInt(1))
	now := time.Now().UTC()
	expires := now.Add(365 * 24 * time.Hour)
	if expires.After(authority.certificate.NotAfter) {
		expires = authority.certificate.NotAfter
	}
	if !now.Before(expires) {
		return nil, nil, errors.New("the node CA has expired")
	}
	template := &x509.Certificate{
		SerialNumber: serial, Subject: pkix.Name{CommonName: commonName},
		NotBefore: now.Add(-5 * time.Minute), NotAfter: expires,
		BasicConstraintsValid: true, SignatureAlgorithm: x509.ECDSAWithSHA256,
		KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	}
	if names != nil {
		template.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}
		for _, name := range names {
			if address := net.ParseIP(name); address != nil {
				template.IPAddresses = append(template.IPAddresses, address)
			} else {
				template.DNSNames = append(template.DNSNames, name)
			}
		}
	}
	der, err := x509.CreateCertificate(rand.Reader, template, authority.certificate, publicKey, authority.key)
	if err != nil {
		return nil, nil, err
	}
	certificate, err := x509.ParseCertificate(der)
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), certificate, err
}

func (authority *nodeAuthority) renew(request []byte, nodeID string) ([]byte, *x509.Certificate, error) {
	block, trailing := pem.Decode(request)
	if block == nil || block.Type != "CERTIFICATE REQUEST" || len(trailing) != 0 {
		return nil, nil, errors.New("not a PEM certificate request")
	}
	csr, err := x509.ParseCertificateRequest(block.Bytes)
	if err != nil {
		return nil, nil, errors.New("not a PEM certificate request")
	}
	if err := csr.CheckSignature(); err != nil {
		return nil, nil, errors.New("the certificate request's signature is invalid")
	}
	publicKey, ok := csr.PublicKey.(*ecdsa.PublicKey)
	if !ok {
		return nil, nil, errors.New("node keys must be EC keys")
	}
	return authority.issue(publicKey, nodeID, nil)
}

func (authority *nodeAuthority) serverTLSConfig(names []string) (*tls.Config, error) {
	if len(names) == 0 {
		return nil, errors.New("the node listener requires TLS server names")
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, err
	}
	_, certificate, err := authority.issue(&key.PublicKey, "Zoo API", names)
	if err != nil {
		return nil, err
	}
	roots := x509.NewCertPool()
	roots.AddCert(authority.certificate)
	return &tls.Config{
		MinVersion: tls.VersionTLS12, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: roots,
		Certificates: []tls.Certificate{{Certificate: [][]byte{certificate.Raw}, PrivateKey: key, Leaf: certificate}},
	}, nil
}
