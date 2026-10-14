package main

var services = []string{"exec", "pty", "files", "metrics", "tunnel", "a11y", "windows"}

func init() {
	handlers["window"] = windowOp
	handlers["desktop_ready"] = desktopReadyOp
}
