package main

import (
	"crypto/ecdsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"fmt"
	"hash"
	"os"
	"path/filepath"
	"runtime"

	"github.com/chann44/zoo/node/nodepb"
)

// executable is the running binary, which an update replaces (tests point it elsewhere).
var executable = os.Executable

// A zoo-node build arriving from the API, written next to the running binary.
type pendingUpdate struct {
	version string
	sha256  string
	size    uint64
	file    *os.File
	hash    hash.Hash
	written uint64
}

func (s *session) receiveUpdate(chunk *nodepb.UpdateChunk) {
	n := s.node
	if s.update == nil || s.update.version != chunk.Version {
		s.dropUpdate()
		exe, err := executable()
		if err != nil {
			n.log.Error("update: can't find this binary", "error", err)
			return
		}
		file, err := os.CreateTemp(filepath.Dir(exe), ".zoo-node-update-*")
		if err != nil {
			n.log.Error("update: can't write next to this binary", "error", err)
			return
		}
		s.update = &pendingUpdate{version: chunk.Version, sha256: chunk.Sha256, size: chunk.Size, file: file, hash: sha256.New()}
		n.log.Info("receiving an update", "version", chunk.Version, "bytes", chunk.Size)
	}
	u := s.update
	if _, err := u.file.Write(chunk.Data); err != nil {
		n.log.Error("update: write failed", "error", err)
		s.dropUpdate()
		return
	}
	u.hash.Write(chunk.Data)
	u.written += uint64(len(chunk.Data))
	if !chunk.Last {
		return
	}
	err := u.finish()
	s.update = nil
	if err != nil {
		n.log.Error("update rejected", "version", u.version, "error", err)
		os.Remove(u.file.Name())
		return
	}
	n.log.Info("updated", "version", u.version)
	n.once.Do(func() { close(n.updated) })
}

// removeStaleUpdates deletes builds left half-written next to the binary, as when two API endpoints pushed the same
// update and the node restarted on the first.
func removeStaleUpdates() {
	exe, err := executable()
	if err != nil {
		return
	}
	stale, _ := filepath.Glob(filepath.Join(filepath.Dir(exe), ".zoo-node-update-*"))
	for _, path := range stale {
		os.Remove(path)
	}
}

func (s *session) dropUpdate() {
	if s.update != nil {
		s.update.file.Close()
		os.Remove(s.update.file.Name())
		s.update = nil
	}
}

// finish checks the build and swaps it in for the running binary.
func (u *pendingUpdate) finish() error {
	if err := u.file.Close(); err != nil {
		return err
	}
	if u.written != u.size {
		return fmt.Errorf("got %d bytes of %d", u.written, u.size)
	}
	if got := hex.EncodeToString(u.hash.Sum(nil)); got != u.sha256 {
		return fmt.Errorf("checksum mismatch: got %s, want %s", got, u.sha256)
	}
	if err := os.Chmod(u.file.Name(), 0o755); err != nil {
		return err
	}
	exe, err := executable()
	if err != nil {
		return err
	}
	return swap(exe, u.file.Name())
}

// swap puts next in place of exe. A running binary can be replaced by rename on Unix; Windows only lets it be
// renamed, so the old one moves aside to <exe>.old first (removed on the next update).
func swap(exe, next string) error {
	if runtime.GOOS != "windows" {
		return os.Rename(next, exe)
	}
	old := exe + ".old"
	os.Remove(old)
	if err := os.Rename(exe, old); err != nil {
		return err
	}
	if err := os.Rename(next, exe); err != nil {
		os.Rename(old, exe)
		return err
	}
	return nil
}

// Certificate renewal: a new key, a CSR for it, and the API's certificate back on the same stream.
type renewal struct {
	key *ecdsa.PrivateKey
}

func (s *session) renew() {
	n := s.node
	if !n.renewing.TryLock() {
		return
	}
	key, csr, err := newCSR()
	if err != nil {
		n.renewing.Unlock()
		n.log.Error("renewal: making a key failed", "error", err)
		return
	}
	s.renewal = &renewal{key: key}
	n.log.Info("renewing the node certificate")
	s.send(&nodepb.NodeMessage{Body: &nodepb.NodeMessage_Renew{Renew: &nodepb.Renew{Csr: csr}}})
}

func (s *session) renewed(r *nodepb.Renewal) {
	n := s.node
	pending := s.renewal
	s.renewal = nil
	if pending == nil {
		return
	}
	defer n.renewing.Unlock()
	if err := n.saveRenewal(pending.key, r); err != nil {
		n.log.Error("renewal failed", "error", err)
		return
	}
	n.log.Info("node certificate renewed")
}

func (n *Node) saveRenewal(key *ecdsa.PrivateKey, r *nodepb.Renewal) error {
	if r.Error != "" {
		return errors.New(r.Error)
	}
	block, _ := pem.Decode(r.Certificate)
	if block == nil {
		return errors.New("no certificate in the renewal")
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return err
	}
	if pub, ok := cert.PublicKey.(*ecdsa.PublicKey); !ok || !pub.Equal(&key.PublicKey) || cert.Subject.CommonName != n.cfg.NodeID {
		return errors.New("the renewed certificate isn't for this node's new key")
	}
	pool, err := caPool(n.home)
	if err != nil {
		return err
	}
	if _, err := cert.Verify(x509.VerifyOptions{Roots: pool, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}}); err != nil {
		return fmt.Errorf("the renewed certificate doesn't chain to the node CA: %w", err)
	}
	keyPEM, err := encodeKey(key)
	if err != nil {
		return err
	}
	_, keyFile, certFile, _ := paths(n.home)
	// The key is renamed into place before the certificate; if the node stops in between, node.crt.new still
	// pairs with the new key and loadCredentials falls back to it.
	if err := writeFile(certFile+".new", r.Certificate); err != nil {
		return err
	}
	if err := writeFile(keyFile+".new", keyPEM); err != nil {
		return err
	}
	if err := os.Rename(keyFile+".new", keyFile); err != nil {
		return err
	}
	return os.Rename(certFile+".new", certFile)
}
