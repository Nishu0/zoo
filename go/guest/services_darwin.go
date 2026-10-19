package main

// A macOS VM's screen, mouse and keyboard are driven from the host over the VM's own VNC server, so the guest
// serves only what otherwise needs SSH, plus its metrics (the API's heartbeat). a11y and windows join once the
// guest has the Accessibility permission.
var services = []string{"exec", "pty", "files", "metrics"}
