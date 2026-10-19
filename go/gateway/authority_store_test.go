package main

import (
	"bytes"
	"testing"
)

func TestAuthorityMaterialRoundTripAndKeyBinding(test *testing.T) {
	certificate, privateKey, err := createAuthorityMaterial()
	if err != nil {
		test.Fatal(err)
	}
	authority, err := parseAuthorityMaterial(certificate, privateKey)
	if err != nil || authority.certificate.Subject.CommonName != "Zoo node CA" {
		test.Fatalf("invalid generated authority: %v", err)
	}
	_, otherKey, err := createAuthorityMaterial()
	if err != nil {
		test.Fatal(err)
	}
	if _, err := parseAuthorityMaterial(certificate, otherKey); err == nil {
		test.Fatal("mismatched CA signing key accepted")
	}
	for _, malformed := range []string{"", certificate + certificate, "not a certificate"} {
		if _, err := parseAuthorityMaterial(malformed, privateKey); err == nil {
			test.Fatal("malformed authority accepted")
		}
	}
	if _, err := parseAuthorityMaterial(certificate, append(privateKey, privateKey...)); err == nil {
		test.Fatal("multiple private keys accepted")
	}
}

func TestFernetEncryptRoundTripUsesUniqueIVs(test *testing.T) {
	for _, size := range []int{0, 1, 15, 16, 17, 1024, 1 << 20} {
		plaintext := bytes.Repeat([]byte{0xA5}, size)
		first, err := encryptFernet(plaintext, []byte(fixtureDataKey))
		if err != nil {
			test.Fatal(err)
		}
		second, err := encryptFernet(plaintext, []byte(fixtureDataKey))
		if err != nil || first == second {
			test.Fatalf("encryption reused an IV at size %d: %v", size, err)
		}
		decoded, err := decryptFernet(first, []byte(fixtureDataKey))
		if err != nil || !bytes.Equal(decoded, plaintext) {
			test.Fatalf("Fernet round trip failed at size %d: %v", size, err)
		}
	}
	if _, err := encryptFernet([]byte("secret"), []byte("invalid")); err == nil {
		test.Fatal("invalid encryption key accepted")
	}
}

func TestGatewayPublicNodeConfiguration(test *testing.T) {
	test.Setenv("DATABASE_URL", "postgresql://zoo:password@localhost:5432/zoo")
	test.Setenv("ZOO_SECRETS_KEY", "test-secret")
	test.Setenv("ZOO_API_URL", "https://api.example.test/api")
	test.Setenv("ZOO_NODE_HOST", "nodes.example.test")
	test.Setenv("ZOO_NODE_DIST", "/app/node/dist")
	test.Setenv("ZOO_SECRETS_KEY_PREVIOUS", "old-one,old-two")
	test.Setenv("ZOO_NODE_PORT", "7443")
	test.Setenv("ZOO_GATEWAY_GRPC_PORT", "7444")
	test.Setenv("ZOO_GATEWAY_HTTP_PORT", "8001")
	settings, err := readConfig()
	if err != nil || settings.nodeHost != "nodes.example.test" || len(settings.previousKeys) != 2 {
		test.Fatalf("explicit node endpoint rejected: %v", err)
	}
	test.Setenv("ZOO_NODE_HOST", "")
	settings, err = readConfig()
	if err != nil || settings.nodeHost != "api.example.test" {
		test.Fatalf("derived public node endpoint rejected: %v", err)
	}
	for _, invalid := range []string{"https://nodes.example.test", "nodes.example.test:7443", "nodes.example.test/path", "user@nodes.example.test"} {
		test.Setenv("ZOO_NODE_HOST", invalid)
		if _, err := readConfig(); err == nil {
			test.Fatalf("invalid node host accepted: %s", invalid)
		}
	}
	test.Setenv("ZOO_NODE_HOST", "::1")
	if _, err := readConfig(); err != nil {
		test.Fatalf("IPv6 node host rejected: %v", err)
	}
}
