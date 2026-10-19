package main

import (
	"bytes"
	"encoding/json"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// runApps is the sandbox's root policy service (supervisord's zoo-policy): it enforces the app policy the API
// writes to path, a root-only file. A denied program becomes root-only (so the zoo user can't run or copy it) and
// any copy already running is killed; the guest serving the agent runs as zoo and can change none of it.
func runApps(path string) {
	var last []byte
	var denied map[string]bool
	for {
		if data, err := os.ReadFile(path); err == nil && !bytes.Equal(data, last) {
			var effects map[string]string
			if err := json.Unmarshal(data, &effects); err != nil {
				log.Printf("apps: %v", err)
			} else {
				last = data
				denied = map[string]bool{}
				for binary, effect := range effects {
					if real := programPath(binary); real != "" {
						if effect == "allow" {
							os.Chmod(real, 0o755)
						} else {
							denied[real] = true
						}
					}
				}
			}
		}
		for real := range denied {
			// again every time, in case anything put the bits back
			os.Chown(real, 0, 0)
			os.Chmod(real, 0o700)
		}
		if len(denied) > 0 {
			killDenied(denied)
		}
		time.Sleep(time.Second)
	}
}

func programPath(binary string) string {
	found, err := exec.LookPath(binary)
	if err != nil {
		return ""
	}
	real, err := filepath.EvalSymlinks(found)
	if err != nil {
		return ""
	}
	return real
}

// killDenied matches processes by the program they started from (argv[0]): /proc/<pid>/exe of the zoo user's
// processes needs CAP_SYS_PTRACE, which sandboxes don't have.
func killDenied(denied map[string]bool) {
	names := map[string]bool{}
	for real := range denied {
		names[filepath.Base(real)] = true
	}
	entries, _ := os.ReadDir("/proc")
	self := os.Getpid()
	for _, e := range entries {
		pid, err := strconv.Atoi(e.Name())
		if err != nil || pid == self || pid == 1 {
			continue
		}
		cmdline, err := os.ReadFile("/proc/" + e.Name() + "/cmdline")
		if err != nil || len(cmdline) == 0 {
			continue
		}
		argv0, _, _ := strings.Cut(string(cmdline), "\x00")
		if denied[argv0] || names[filepath.Base(argv0)] {
			syscall.Kill(pid, syscall.SIGKILL)
		}
	}
}
