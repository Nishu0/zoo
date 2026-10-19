package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestReadEnv(t *testing.T) {
	path := filepath.Join(t.TempDir(), "guest.env")
	if env := readEnv(path); configured(env) {
		t.Fatalf("a missing file configured the guest: %v", env)
	}
	os.WriteFile(path, []byte("ZOO_GUEST_URL=ws://h/guest/connect?a=b\nZOO_GUEST_TOKEN=tok\n\nZOO_SANDBOX_ID=sb\n"), 0o600)
	env := readEnv(path)
	if !configured(env) || env["ZOO_GUEST_URL"] != "ws://h/guest/connect?a=b" {
		t.Fatalf("got %v", env)
	}
}

func TestExecStdin(t *testing.T) {
	args, _ := json.Marshal(execArgs{Argv: []string{"cat"}})
	result, out, err := execOp(call{args: args, payload: []byte("from stdin")})
	if err != nil || string(out) != "from stdin" || result.(map[string]any)["exit_code"] != 0 {
		t.Fatalf("got %v %q %v", result, out, err)
	}
}
