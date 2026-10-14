package main

import (
	"bytes"
	"encoding/base64"
	"testing"
)

const fixtureDataKey = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
const fixtureWrappedKey = "local:gAAAAABlU_EAT-XAnJfxCSUB1x15eCu3aXVmJwQUUyHDZ76mfNz6qUuxu5AdmroPh8BLzKBtnLFtR7bxD1GH4nV7zayW6DtAR5kNBlIXb2ZMyC67v1-3xZYpyECTFK-G1y51HwxqhGDP"
const fixtureEncryptedValue = "gAAAAABlU_EAdmowEm_IgW08TYFz-BUSBia4nO-h7WKYXWNeKlICkTvjOBZu6ILmNKDe3fYw611SUOQ-uDxxZmoNoqonJXXLKvwLq76ZSzgDJz1NcmtcX-Q="

func TestPythonEnvelopeInterop(test *testing.T) {
	key, err := unwrapLocalKey(fixtureWrappedKey, []string{"gateway-test-secret"})
	if err != nil {
		test.Fatal(err)
	}
	if !bytes.Equal(key, []byte(fixtureDataKey)) {
		test.Fatal("unwrapped data key differs from the Python fixture")
	}
	keyID, token, err := envelopeParts("zk1:fixture-key:" + fixtureEncryptedValue)
	if err != nil || keyID != "fixture-key" {
		test.Fatalf("cannot parse envelope: %v", err)
	}
	plaintext, err := decryptFernet(token, key)
	if err != nil || string(plaintext) != "node-private-key-fixture" {
		test.Fatalf("cannot decrypt Python value: %v", err)
	}
}

func TestKeyRotationDecryptsOnlyWithAnExplicitPreviousKey(test *testing.T) {
	if _, err := unwrapLocalKey(fixtureWrappedKey, []string{"rotated-secret"}); err == nil {
		test.Fatal("incorrect secrets key decrypted a data key")
	}
	if _, err := unwrapLocalKey(fixtureWrappedKey, []string{"rotated-secret", "gateway-test-secret"}); err != nil {
		test.Fatalf("explicit previous key was rejected: %v", err)
	}
}

func TestFernetRejectsTamperingAndMalformedFrames(test *testing.T) {
	frame, err := base64.URLEncoding.DecodeString(fixtureEncryptedValue)
	if err != nil {
		test.Fatal(err)
	}
	for index := range frame {
		changed := bytes.Clone(frame)
		changed[index] ^= 1
		if _, err := decryptFernet(base64.URLEncoding.EncodeToString(changed), []byte(fixtureDataKey)); err == nil {
			test.Fatalf("tampered byte %d was accepted", index)
		}
	}
	for _, malformed := range []string{"", "invalid", "gA==", "legacy:" + fixtureEncryptedValue} {
		if _, err := decryptFernet(malformed, []byte(fixtureDataKey)); err == nil {
			test.Fatal("malformed token was accepted")
		}
	}
}

func TestEnvelopeRejectsUnversionedAndIncompleteValues(test *testing.T) {
	for _, malformed := range []string{fixtureEncryptedValue, "zk1:key", "zk1::token", "zk1:key:", "zk0:key:token"} {
		if _, _, err := envelopeParts(malformed); err == nil {
			test.Fatal("invalid envelope was accepted")
		}
	}
	if _, err := unwrapLocalKey("vault:transit/key:token", []string{"gateway-test-secret"}); err == nil {
		test.Fatal("nonlocal wrapping provider was accepted by the local codec")
	}
}
