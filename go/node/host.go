package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"time"
)

// A target is something on the host the API can open a byte stream to.
type target struct {
	network, address string
}

func (t target) dial(ctx context.Context) (net.Conn, error) {
	var d net.Dialer
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	return d.DialContext(ctx, t.network, t.address)
}

func (t target) check(ctx context.Context) error {
	conn, err := t.dial(ctx)
	if err != nil {
		return err
	}
	return conn.Close()
}

// hostTargets reaches the SSH server used by the remote sandbox backends.
func hostTargets() map[string]target {
	return map[string]target{"ssh": {"tcp", "127.0.0.1:22"}}
}

// A Driver is one of the host's sandbox runtimes. Sandboxes are created and changed by the API's backends over
// the node's tunnels; drivers tell the API which runtimes the host has and which sandboxes each is running.
type Driver interface {
	Name() string
	// Probe says whether the runtime is usable here, with its version, or why not.
	Probe(ctx context.Context) (bool, string)
	// Sandboxes are the ids of the sandboxes it is running.
	Sandboxes(ctx context.Context) ([]string, error)
}

func hostDrivers() []Driver {
	switch runtime.GOOS {
	case "darwin":
		return []Driver{&zoovmDriver{}, &dockerDriver{}}
	case "windows":
		return []Driver{&hypervDriver{}, &dockerDriver{}}
	case "linux":
		return []Driver{&dockerDriver{}}
	default:
		panic("unsupported node operating system")
	}
}

// Sandbox VMs are named zoo-<sandbox id>; base VMs and templates are not sandboxes.
var vmName = regexp.MustCompile(`^zoo-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$`)

func sandboxIDs(names []string) []string {
	var ids []string
	for _, name := range names {
		if m := vmName.FindStringSubmatch(strings.TrimSpace(name)); m != nil {
			ids = append(ids, m[1])
		}
	}
	return ids
}

func output(ctx context.Context, name string, args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, name, args...).Output()
	var exit *exec.ExitError
	if errors.As(err, &exit) && len(exit.Stderr) > 0 {
		return out, fmt.Errorf("%s: %s", name, strings.TrimSpace(string(exit.Stderr)))
	}
	return out, err
}

// zoovm: macOS VMs on Apple's Virtualization framework (macos/zoovm).
type zoovmDriver struct{}

// Docker Desktop on Mac/Windows must be in Linux-container mode.
type dockerDriver struct{}

func (dockerDriver) Name() string { return "docker" }

func dockerOutput(ctx context.Context, args ...string) ([]byte, error) {
	name := "docker"
	if runtime.GOOS == "windows" {
		if _, err := exec.LookPath(name); err != nil {
			name = filepath.Join(os.Getenv("ProgramFiles"), "Docker", "Docker", "resources", "bin", "docker.exe")
		}
	}
	out, err := output(ctx, name, args...)
	// LocalSystem has no Docker Desktop user context. Its Linux-engine pipe reaches
	// the same engine as the SSH account's desktop-linux context, without TCP.
	if err != nil && runtime.GOOS == "windows" {
		return output(ctx, name, append([]string{"--host", `npipe:////./pipe/dockerDesktopLinuxEngine`}, args...)...)
	}
	return out, err
}

func (dockerDriver) Probe(ctx context.Context) (bool, string) {
	out, err := dockerOutput(ctx, "info", "--format", "{{.OSType}} {{.ServerVersion}}")
	if err != nil {
		return false, err.Error()
	}
	fields := strings.Fields(string(out))
	if len(fields) != 2 || fields[0] != "linux" {
		return false, "Docker must run Linux containers"
	}
	return true, "Linux Docker " + fields[1]
}

func (dockerDriver) Sandboxes(ctx context.Context) ([]string, error) {
	out, err := dockerOutput(ctx, "ps", "--filter", "label=zoo.sandbox", "--format", `{{.Label "zoo.sandbox"}}`)
	if err != nil {
		return nil, err
	}
	return strings.Fields(string(out)), nil
}

func (zoovmDriver) Name() string { return "zoovm" }

func zoovmPath() string {
	if path, err := exec.LookPath("zoovm"); err == nil {
		return path
	}
	return "/usr/local/bin/zoovm"
}

func (z zoovmDriver) list(ctx context.Context) ([]struct{ Name, State string }, error) {
	out, err := output(ctx, zoovmPath(), "list")
	if err != nil {
		return nil, err
	}
	var rows []struct{ Name, State string }
	if err := json.Unmarshal(out, &rows); err != nil {
		return nil, fmt.Errorf("zoovm list: %w", err)
	}
	return rows, nil
}

func (z zoovmDriver) Probe(ctx context.Context) (bool, string) {
	if _, err := z.list(ctx); err != nil {
		return false, err.Error()
	}
	return true, "zoovm"
}

func (z zoovmDriver) Sandboxes(ctx context.Context) ([]string, error) {
	rows, err := z.list(ctx)
	if err != nil {
		return nil, err
	}
	var names []string
	for _, r := range rows {
		if r.State == "running" {
			names = append(names, r.Name)
		}
	}
	return sandboxIDs(names), nil
}

// hyperv: Windows VMs on Hyper-V, marked with the notes zoovm.ps1 gives them.
type hypervDriver struct{}

func (hypervDriver) Name() string { return "hyperv" }

func powershell(ctx context.Context, script string) ([]byte, error) {
	return output(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script)
}

func (hypervDriver) Probe(ctx context.Context) (bool, string) {
	out, err := powershell(ctx, "(Get-Module -ListAvailable Hyper-V | Select-Object -First 1).Version.ToString()")
	if err != nil || len(strings.TrimSpace(string(out))) == 0 {
		return false, "Hyper-V's PowerShell module isn't installed"
	}
	return true, "Hyper-V " + strings.TrimSpace(string(out))
}

func (hypervDriver) Sandboxes(ctx context.Context) ([]string, error) {
	out, err := powershell(ctx, "Get-VM | Where-Object { $_.Notes -eq 'zoo' -and $_.State -eq 'Running' } | ForEach-Object Name")
	if err != nil {
		return nil, err
	}
	return sandboxIDs(strings.Split(string(out), "\n")), nil
}

// Host capacity, read by stats_<os>.go.
type stats struct {
	memoryTotal, memoryAvailable uint64
	diskTotal, diskFree          uint64
	load                         float64
}

// diskPath is where the host stores sandbox VM disks.
func diskPath() string {
	if runtime.GOOS == "windows" {
		return os.Getenv("SystemDrive") + `\`
	}
	home, _ := os.UserHomeDir()
	return home
}
