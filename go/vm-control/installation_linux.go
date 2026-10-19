package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"runtime"
	"syscall"
)

type installedShim struct {
	Protocol        string   `json:"protocol"`
	KataVersion     string   `json:"kata_version"`
	KataRevision    string   `json:"kata_revision"`
	SourceSHA256    string   `json:"source_sha256"`
	LifecycleSHA256 string   `json:"lifecycle_patch_sha256"`
	OverlaySHA256   string   `json:"overlay_sha256"`
	BinarySHA256    string   `json:"binary_sha256"`
	Architecture    string   `json:"architecture"`
	Runtime         string   `json:"runtime"`
	Hypervisors     []string `json:"hypervisors"`
}

func installationFile(path string) (*os.File, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	owner, valid := info.Sys().(*syscall.Stat_t)
	if !valid || !info.Mode().IsRegular() || info.Mode().Perm()&0022 != 0 || owner.Uid != 0 {
		return nil, errors.New("installed shim artifact must be a root-owned non-writable regular file")
	}
	return os.Open(path)
}

func verifyInstallation(manifestPath, binaryPath string) error {
	manifest, err := installationFile(manifestPath)
	if err != nil {
		return err
	}
	defer manifest.Close()
	var installed installedShim
	decoder := json.NewDecoder(io.LimitReader(manifest, 8193))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&installed); err != nil || decoder.Decode(new(any)) != io.EOF {
		return errors.New("bounded pinned shim manifest required")
	}
	if installed.Protocol != protocol || installed.KataVersion != kataVersion || installed.KataRevision != kataRevision ||
		installed.Architecture != runtime.GOARCH || installed.Runtime != "go-rootful" || !runtimeIDPattern.MatchString(installed.BinarySHA256) {
		return errors.New("installed shim does not match the required pinned Go protocol and architecture")
	}
	if len(installed.Hypervisors) != 2 || installed.Hypervisors[0] != "qemu" || installed.Hypervisors[1] != "clh" {
		return errors.New("installed shim must support verified QEMU and CLH state")
	}
	binary, err := installationFile(binaryPath)
	if err != nil {
		return err
	}
	defer binary.Close()
	digest := sha256.New()
	count, err := io.Copy(digest, io.LimitReader(binary, 128*1024*1024+1))
	if err != nil {
		return err
	}
	if count > 128*1024*1024 || hex.EncodeToString(digest.Sum(nil)) != installed.BinarySHA256 {
		return errors.New("installed shim binary does not match its pinned manifest")
	}
	return nil
}
