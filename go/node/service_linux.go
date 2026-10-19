package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

func systemdArgument(value string) string {
	return strings.NewReplacer("%", "%%", "$", "$$").Replace(value)
}

func systemdUnit(bin, home string) string {
	return fmt.Sprintf("[Unit]\nDescription=Zoo remote host\nAfter=network-online.target docker.service ssh.service\nWants=network-online.target\n\n[Service]\nExecStart=%q run --dir %q\nRestart=always\nRestartSec=2\nEnvironment=PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin\n\n[Install]\nWantedBy=multi-user.target\n", systemdArgument(bin), systemdArgument(home))
}

func installService(home string) error {
	if os.Geteuid() != 0 {
		return errors.New("install the Linux system service with sudo")
	}
	if strings.ContainsAny(home, "\r\n\x00") {
		return errors.New("invalid node state path")
	}
	bin := filepath.Join(home, "bin", "zoo-node")
	if err := installBinary(bin); err != nil {
		return err
	}
	if err := os.WriteFile("/etc/systemd/system/zoo-node.service", []byte(systemdUnit(bin, home)), 0o644); err != nil {
		return err
	}
	for _, args := range [][]string{{"daemon-reload"}, {"enable", "zoo-node.service"}, {"restart", "zoo-node.service"}} {
		if out, err := exec.Command("systemctl", args...).CombinedOutput(); err != nil {
			return fmt.Errorf("systemctl: %s: %w", strings.TrimSpace(string(out)), err)
		}
	}
	return nil
}

func runAsService(string) bool { return false }
