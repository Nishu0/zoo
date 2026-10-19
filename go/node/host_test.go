//go:build !windows

package main

import (
	"context"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestDockerDriverRequiresLinuxEngine(t *testing.T) {
	for _, engine := range []string{"linux 29.0.0", "windows 29.0.0", "linux", ""} {
		t.Run(engine, func(t *testing.T) {
			dir := t.TempDir()
			script := "#!/bin/sh\nprintf '%s\\n' '" + engine + "'\n"
			if err := os.WriteFile(filepath.Join(dir, "docker"), []byte(script), 0755); err != nil {
				t.Fatal(err)
			}
			t.Setenv("PATH", dir)
			available, detail := (dockerDriver{}).Probe(context.Background())
			if available != strings.HasPrefix(engine, "linux ") {
				t.Fatalf("engine %q: available=%v detail=%s", engine, available, detail)
			}
		})
	}
}

func TestDockerDriverListsLabeledSandboxes(t *testing.T) {
	dir := t.TempDir()
	script := `#!/bin/sh
[ "$1" = ps ] && [ "$2" = --filter ] && [ "$3" = label=zoo.sandbox ] && [ "$4" = --format ] && [ "$5" = '{{.Label "zoo.sandbox"}}' ] || exit 2
printf 'sandbox-a\nsandbox-b\n'
`
	if err := os.WriteFile(filepath.Join(dir, "docker"), []byte(script), 0755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir)
	got, err := (dockerDriver{}).Sandboxes(context.Background())
	if err != nil || !reflect.DeepEqual(got, []string{"sandbox-a", "sandbox-b"}) {
		t.Fatalf("got %v, %v", got, err)
	}
}
