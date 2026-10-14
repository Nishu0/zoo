package main

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"fmt"
	"math/big"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

func loadNodeAuthority(ctx context.Context, pool *pgxpool.Pool, settings config) (*nodeAuthority, error) {
	tx, err := pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtextextended('zoo-node-ca', 0))"); err != nil {
		return nil, err
	}
	var certificate, ciphertext string
	err = tx.QueryRow(ctx, "SELECT certificate, key_ciphertext FROM node_authority WHERE id = 1").Scan(&certificate, &ciphertext)
	if errors.Is(err, pgx.ErrNoRows) {
		createdCertificate, privateKey, failure := createAuthorityMaterial()
		if failure != nil {
			return nil, failure
		}
		encrypted, failure := sealSystemSecret(ctx, tx, settings, privateKey)
		if failure != nil {
			return nil, failure
		}
		if _, err := tx.Exec(ctx, "INSERT INTO node_authority (id, certificate, key_ciphertext) VALUES (1, $1, $2) ON CONFLICT (id) DO NOTHING", createdCertificate, encrypted); err != nil {
			return nil, err
		}
		err = tx.QueryRow(ctx, "SELECT certificate, key_ciphertext FROM node_authority WHERE id = 1").Scan(&certificate, &ciphertext)
	}
	if err != nil {
		return nil, err
	}
	keyID, token, err := envelopeParts(ciphertext)
	if err != nil {
		return nil, err
	}
	var wrapped string
	if err := tx.QueryRow(ctx, "SELECT wrapped FROM secret_keys WHERE id = $1 AND scope = 'system'", keyID).Scan(&wrapped); err != nil {
		return nil, fmt.Errorf("node CA data key is unavailable: %w", err)
	}
	dataKey, err := unwrapDataKey(ctx, wrapped, append([]string{settings.secretsKey}, settings.previousKeys...))
	if err != nil {
		return nil, err
	}
	privateKey, err := decryptFernet(token, dataKey)
	if err != nil {
		return nil, err
	}
	authority, err := parseAuthorityMaterial(certificate, privateKey)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, err
	}
	return authority, nil
}

func sealSystemSecret(ctx context.Context, tx pgx.Tx, settings config, plaintext []byte) (string, error) {
	var keyID, wrapped string
	err := tx.QueryRow(ctx, "SELECT id, wrapped FROM secret_keys WHERE scope = 'system'").Scan(&keyID, &wrapped)
	if errors.Is(err, pgx.ErrNoRows) {
		randomKey := make([]byte, 32)
		if _, err := rand.Read(randomKey); err != nil {
			return "", err
		}
		encoded := []byte(base64.URLEncoding.EncodeToString(randomKey))
		wrapped, err = wrapDataKey(ctx, encoded)
		if err != nil {
			return "", err
		}
		randomID := make([]byte, 16)
		if _, err := rand.Read(randomID); err != nil {
			return "", err
		}
		keyID = hex.EncodeToString(randomID)
		if _, err := tx.Exec(ctx, "INSERT INTO secret_keys (id, scope, wrapped) VALUES ($1, 'system', $2) ON CONFLICT (scope) DO NOTHING", keyID, wrapped); err != nil {
			return "", err
		}
		err = tx.QueryRow(ctx, "SELECT id, wrapped FROM secret_keys WHERE scope = 'system'").Scan(&keyID, &wrapped)
	}
	if err != nil {
		return "", err
	}
	dataKey, err := unwrapDataKey(ctx, wrapped, append([]string{settings.secretsKey}, settings.previousKeys...))
	if err != nil {
		return "", err
	}
	token, err := encryptFernet(plaintext, dataKey)
	if err != nil {
		return "", err
	}
	return "zk1:" + keyID + ":" + token, nil
}

func createAuthorityMaterial() (string, []byte, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return "", nil, err
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 159))
	if err != nil {
		return "", nil, err
	}
	serial.Add(serial, big.NewInt(1))
	now := time.Now().UTC()
	name := pkix.Name{CommonName: "Zoo node CA"}
	template := &x509.Certificate{
		SerialNumber: serial, Subject: name, NotBefore: now.Add(-5 * time.Minute), NotAfter: now.Add(3650 * 24 * time.Hour),
		IsCA: true, BasicConstraintsValid: true, MaxPathLenZero: true,
		KeyUsage: x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign | x509.KeyUsageCRLSign,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		return "", nil, err
	}
	keyDER, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return "", nil, err
	}
	certificate := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	privateKey := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER})
	return string(certificate), privateKey, nil
}

func parseAuthorityMaterial(certificate string, privateKey []byte) (*nodeAuthority, error) {
	block, rest := pem.Decode([]byte(certificate))
	if block == nil || block.Type != "CERTIFICATE" || len(rest) != 0 {
		return nil, errors.New("node CA is not a single PEM certificate")
	}
	parsed, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return nil, err
	}
	block, rest = pem.Decode(privateKey)
	if block == nil || block.Type != "PRIVATE KEY" || len(rest) != 0 {
		return nil, errors.New("node CA key is not a single PKCS8 PEM key")
	}
	key, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return nil, err
	}
	signer, ok := key.(*ecdsa.PrivateKey)
	if !ok {
		return nil, errors.New("node CA key must be ECDSA")
	}
	return newNodeAuthority(parsed, signer)
}
