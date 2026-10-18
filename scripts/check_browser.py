"""Boot a browser image and verify Firefox fills the display without desktop apps.

Usage: python scripts/check_browser.py IMAGE
"""

import sys
import time

from check_desktop import docker


def main(image: str) -> None:
    started = docker(
        "run",
        "--detach",
        "--network",
        "none",
        "--env",
        "ZOO_BROWSER_HOME=about:blank",
        "--env",
        "ZOO_BROWSER_WAIT=1",
        image,
    )
    if started.returncode:
        raise RuntimeError(started.stderr)
    container = started.stdout.strip()
    try:
        waiting = docker("exec", container, "sh", "-c", "sleep 2; ! pgrep -x firefox-esr")
        if waiting.returncode:
            raise AssertionError("Firefox opened before the API could restore its profile")
        released = docker("exec", container, "touch", "/tmp/zoo-browser-start")
        if released.returncode:
            raise RuntimeError(released.stderr)
        deadline = time.monotonic() + 30
        while True:
            result = docker(
                "exec",
                container,
                "sh",
                "-c",
                "for app in xfce4-panel xfdesktop xfce4-terminal thunar; do "
                'if command -v "$app" >/dev/null; then exit 1; fi; done; '
                "pgrep -x firefox-esr >/dev/null && DISPLAY=:1 xwininfo -root -tree",
            )
            visible = any(
                '"firefox-esr"' in line.lower() and "1280x720+0+0" in line for line in result.stdout.splitlines()
            )
            if result.returncode == 0 and visible:
                print("PASS: Firefox fills the display; desktop shell, terminal, and file manager are absent")
                return
            if time.monotonic() >= deadline:
                raise AssertionError("Browser startup failed\n" + result.stdout + result.stderr)
            time.sleep(0.5)
    finally:
        removed = docker("rm", "--force", "--volumes", container)
        if removed.returncode:
            raise RuntimeError(removed.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/check_browser.py IMAGE")
    main(sys.argv[1])
