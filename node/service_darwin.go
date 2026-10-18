package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

const label = "dev.zoo.node"

func plist(bin, home string) string {
	return fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>%s</string>
<key>ProgramArguments</key><array><string>%s</string><string>run</string><string>--dir</string><string>%s</string></array>
<key>EnvironmentVariables</key><dict><key>PATH</key><string>/Applications/Docker.app/Contents/Resources/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>%s</string>
<key>StandardErrorPath</key><string>%s</string>
</dict></plist>
`, label, bin, home, filepath.Join(home, "node.log"), filepath.Join(home, "node.log"))
}

// installService runs zoo-node as a LaunchAgent of the user who owns the VMs and the SSH account.
func installService(home string) error {
	if os.Geteuid() == 0 {
		return errors.New("on a Mac, run this as the user that runs the VMs, not as root")
	}
	user, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	bin := filepath.Join(home, "bin", "zoo-node")
	if err := installBinary(bin); err != nil {
		return err
	}
	path := filepath.Join(user, "Library", "LaunchAgents", label+".plist")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	if err := os.WriteFile(path, []byte(plist(bin, home)), 0o644); err != nil {
		return err
	}
	uid := fmt.Sprint(os.Getuid())
	// the GUI session when someone is logged in, else the user's background session (over SSH)
	var failure string
	for _, domain := range []string{"gui/" + uid, "user/" + uid} {
		exec.Command("launchctl", "bootout", domain+"/"+label).Run()
		out, err := exec.Command("launchctl", "bootstrap", domain, path).CombinedOutput()
		if err == nil {
			return nil
		}
		failure = strings.TrimSpace(string(out))
	}
	return fmt.Errorf("launchctl bootstrap: %s", failure)
}

func runAsService(string) bool { return false }
