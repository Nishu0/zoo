//go:build !windows

package main

import (
	"os"
	"path/filepath"
	"slices"
	"testing"
)

func TestSecretChangesReachNewCommands(t *testing.T) {
	secretsFile = filepath.Join(t.TempDir(), "env.json")

	if env := secretEnv(); env != nil {
		t.Fatalf("no secrets file should mean no secrets, got %v", env)
	}

	os.WriteFile(secretsFile, []byte(`{"NEW_TOKEN":"fresh"}`), 0o600)
	if got := secretEnv(); !slices.Equal(got, []string{"NEW_TOKEN=fresh"}) {
		t.Fatalf("secretEnv = %v", got)
	}

	// a half-written file is no secrets rather than garbage
	os.WriteFile(secretsFile, []byte(`{"NEW_TO`), 0o600)
	if env := secretEnv(); env != nil {
		t.Fatalf("a torn file should read as none, got %v", env)
	}
}
