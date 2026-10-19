"""The API's entry point imports on its own, the way `python main.py` starts it, without a Docker daemon (a Zoo on
Kubernetes has none)."""

import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_main_imports_without_docker():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {
        **os.environ,
        "DOCKER_HOST": "unix:///nonexistent/docker.sock",
        "JWT_SECRET": "startup-test-secret-that-is-long-enough",
        # `python main.py` builds the app twice (uvicorn imports main again): the metrics port is bound once
        "ZOO_METRICS_PORT": str(port),
    }
    script = "import main; from apps.server.server import Server; Server()"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
