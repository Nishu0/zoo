"""Smoke-test a real desktop image: python scripts/check_desktop.py IMAGE.

Boots an isolated, disposable container without a Zoo stack or guest credentials.
Checks D-Bus service activation and the visible desktop, which API mocks cannot cover.
"""

import subprocess
import sys
import time


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=30, check=False)


def main(image: str) -> None:
    started = docker("run", "--detach", "--network", "none", image)
    if started.returncode:
        raise RuntimeError(started.stderr)
    container = started.stdout.strip()
    try:
        deadline = time.monotonic() + 15
        while True:
            result = docker(
                "exec",
                "--user",
                "zoo",
                "--env",
                "HOME=/home/zoo",
                "--env",
                "DISPLAY=:1",
                "--env",
                "DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/zoo-session-bus",
                container,
                "sh",
                "-c",
                "xfconf-query -c zoo-startup-check -p /ready --create -t bool -s true && xwininfo -root -tree",
            )
            # xfdesktop's full-size window is titled Desktop; its hidden 10x10 helper is not enough.
            visible = any('"Desktop"' in line and "1280x720" in line for line in result.stdout.splitlines())
            if result.returncode == 0 and visible:
                print("PASS: session services can save settings and the desktop window is visible")
                return
            if time.monotonic() >= deadline:
                raise AssertionError(
                    "Desktop startup failed: settings service unavailable or desktop window missing\n"
                    + result.stdout
                    + result.stderr
                )
            time.sleep(0.5)
    finally:
        removed = docker("rm", "--force", "--volumes", container)
        if removed.returncode:
            raise RuntimeError(removed.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/check_desktop.py IMAGE")
    main(sys.argv[1])
