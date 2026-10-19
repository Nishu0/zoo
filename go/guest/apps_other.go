//go:build !linux

package main

import "log"

// runApps is Linux only: macOS and Windows sandboxes get their app policy over SSH (server/macos.py, windows.py).
func runApps(path string) { log.Fatal("-apps is only for Linux sandboxes") }
