"""An in-memory stand-in for the Docker backend, so API tests run without Docker, Kata or VMs.

It replaces the native functions server/runtime.py dispatches to for Linux sandboxes.
Tools are served by fake implementations that record calls.
"""

import base64
import io
import itertools
import json
import tarfile
from dataclasses import dataclass, field
from typing import Any

from apps.server import docker, guest, sandbox_api
from apps.server.registry import Tool

# a 1x1 PNG, enough for anything that decodes the screenshot
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
APPS = [
    {"name": "Firefox", "binary": "firefox-esr"},
    {"name": "Terminal", "binary": "xfce4-terminal"},
]


@dataclass
class Container:
    sandbox_id: str
    image: str
    env: dict[str, str]
    desktop: bool
    running: bool = True
    paused: bool = False
    network: tuple | None = None
    apps: dict[str, str] = field(default_factory=dict)
    dirs: dict[str, bytes] = field(default_factory=dict)
    home: bytes = b""
    # profile apps running in the sandbox, for the profile lock check
    running_apps: set[str] = field(default_factory=set)


class FakeRuntime:
    def __init__(self):
        self.reset()

    def reset(self):
        self.containers: dict[str, Container] = {}
        self.volumes: set[str] = set()
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_boot: str | None = None
        self.fail_tool: str | None = None
        # runtime call name -> how many more times it fails, like a host that drops off for a moment
        self.flaky: dict[str, int] = {}
        self.unreachable = False
        # nodes the pause sweeper sees short of memory
        self.pressured: set[str] = set()
        self.ids = itertools.count(1)
        # snapshot id -> sandbox id
        self.snapshots: dict[str, str] = {}

    def install(self, mp):
        """Patches every runtime entry point; `mp` is a pytest MonkeyPatch. The real Docker functions stay in
        `originals`, for tests of the Docker backend itself."""
        patched = (
            "remove_container",
            "remove_volume",
            "export_dir",
            "import_dir",
            "app_running",
            "is_running",
            "apply_network",
            "apply_apps",
            "export_home",
            "import_home",
            "run_container",
            "pause",
            "resume",
            "pressured_nodes",
            "snapshot",
            "restore_snapshot",
            "remove_snapshot",
            "write_secrets",
        )
        self.originals = {name: getattr(docker, name) for name in ("container", *patched)}
        for name in patched:
            mp.setattr(docker, name, getattr(self, name))
        mp.setattr(
            sandbox_api,
            "start_browser",
            lambda runtime_id, url: self.calls.append(("open_url", runtime_id, {"url": url})),
        )
        fake = self
        mp.setattr(Tool, "impl", lambda tool, runtime_id: fake.tool(tool))
        # a fake container's desktop is up as soon as it runs
        mp.setattr(guest.hub, "wait_for_desktop", lambda sandbox_id, timeout=30: True)

    def flake(self, name: str):
        if self.flaky.get(name, 0) > 0:
            self.flaky[name] -= 1
            raise ConnectionError(f"{name}: connection reset by peer")

    # Docker backend

    def run_container(self, name, image, sandbox_id, env, desktop=True, size=None):
        if self.fail_boot:
            raise RuntimeError(self.fail_boot)
        self.flake("run_container")
        runtime_id = f"fake-{next(self.ids)}"
        self.containers[runtime_id] = Container(sandbox_id, image, dict(env), desktop)
        self.volumes.add(sandbox_id)
        return runtime_id, "127.0.0.1"

    def pause(self, runtime_id):
        self.flake("pause")
        self.containers[runtime_id].paused = True

    def resume(self, runtime_id):
        self.containers[runtime_id].paused = False

    def pressured_nodes(self, share):
        return set(self.pressured)

    def remove_container(self, runtime_id):
        self.containers.pop(runtime_id, None)

    def remove_volume(self, sandbox_id):
        self.flake("remove_volume")
        self.volumes.discard(sandbox_id)

    def snapshot(self, sandbox_id, snapshot_id):
        self.flake("snapshot")
        self.snapshots[snapshot_id] = sandbox_id
        self.calls.append(("snapshot", sandbox_id, {"snapshot_id": snapshot_id}))
        return 4096

    def restore_snapshot(self, sandbox_id, snapshot_id):
        self.flake("restore_snapshot")
        if self.snapshots.get(snapshot_id) != sandbox_id:
            raise RuntimeError("the snapshot's volume is gone from the host")
        self.calls.append(("restore_snapshot", sandbox_id, {"snapshot_id": snapshot_id}))

    def remove_snapshot(self, snapshot_id):
        self.snapshots.pop(snapshot_id, None)
        self.calls.append(("remove_snapshot", snapshot_id, {}))

    def is_running(self, runtime_id):
        if self.unreachable:
            raise ConnectionError("ssh: connect to host box port 22: No route to host")
        container = self.containers.get(runtime_id)
        return container is not None and container.running

    def write_secrets(self, runtime_id, values):
        self.import_dir(runtime_id, "/run/zoo", docker.tar_file("env.json", json.dumps(values).encode()))

    @staticmethod
    def secrets_of(container: Container) -> dict[str, str]:
        if "/run/zoo" not in container.dirs:
            return {}
        with tarfile.open(fileobj=io.BytesIO(container.dirs["/run/zoo"])) as tar:
            member = tar.extractfile("env.json")
            return json.load(member) if member else {}

    def apply_network(self, runtime_id, default_action, allow_dns, rules):
        self.containers[runtime_id].network = (default_action, allow_dns, list(rules))

    def apply_apps(self, runtime_id, effects):
        self.containers[runtime_id].apps = dict(effects)

    def export_dir(self, runtime_id, path):
        if path not in self.containers[runtime_id].dirs:
            raise FileNotFoundError(path)
        return self.containers[runtime_id].dirs[path]

    def import_dir(self, runtime_id, parent, data):
        self.containers[runtime_id].dirs[parent] = data

    def app_running(self, runtime_id, app):
        return app in self.containers[runtime_id].running_apps

    def export_home(self, runtime_id):
        return iter([self.containers[runtime_id].home])

    def import_home(self, runtime_id, data):
        self.containers[runtime_id].home = data

    # Tools

    def tool(self, tool: Tool):
        def run(runtime_id: str, **args) -> Any:
            self.calls.append((tool.name, runtime_id, args))
            if self.fail_tool:
                raise RuntimeError(self.fail_tool)
            if tool.name == "screenshot":
                return base64.b64encode(PNG).decode()
            if tool.name == "installed_apps":
                return {"gui_apps": APPS}
            if tool.name == "execute_command":
                container = self.containers[runtime_id]
                # the guest adds the secrets the API wrote to /run/zoo/env.json to every command's environment
                env = {**container.env, **self.secrets_of(container)}
                stdout = "\n".join(f"{k}={v}" for k, v in env.items()) if args["command"] == "env" else args["command"]
                return {"exit_code": 0, "stdout": stdout, "stderr": "", "timed_out": False}
            return {"tool": tool.name, "args": args}

        return run

    def tool_calls(self, name: str) -> list[dict]:
        return [args for tool, _, args in self.calls if tool == name]
