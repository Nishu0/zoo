import base64
import json
import os
import shlex
import time

import requests


class ZooError(Exception):
    pass


class Zoo:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.base_url = (base_url or os.environ.get("ZOO_URL", "http://localhost:8000")).rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_key or os.environ['ZOO_API_KEY']}"

    def request(self, method: str, path: str, **kwargs):
        res = self.session.request(method, f"{self.base_url}{path}", timeout=kwargs.pop("timeout", 330), **kwargs)
        if not res.ok:
            try:
                detail = res.json().get("detail")
            except ValueError:
                detail = res.text
            raise ZooError(f"{res.status_code}: {detail}")
        return res

    def sandboxes(self, labels: dict[str, str] | None = None) -> list["Sandbox"]:
        """The workspace's sandboxes, only those with all of `labels` when given."""
        params = [("label", f"{k}={v}") for k, v in (labels or {}).items()]
        return [Sandbox(self, s) for s in self.request("GET", "/sandboxes", params=params).json()]

    def sandbox(self, sandbox_id: str) -> "Sandbox":
        return Sandbox(self, self.request("GET", f"/sandboxes/{sandbox_id}").json())

    def create(
        self,
        name: str | None = None,
        kind: str = "desktop",
        server_id: str | None = None,
        profile_ids: list[str] | None = None,
        wait: bool = True,
        timeout: int = 180,
        labels: dict[str, str] | None = None,
    ) -> "Sandbox":
        body = {
            "name": name,
            "kind": kind,
            "server_id": server_id,
            "profile_ids": profile_ids or [],
            "labels": labels or {},
        }
        sandbox = Sandbox(self, self.request("POST", "/sandboxes", json=body).json())
        return sandbox.wait(timeout) if wait else sandbox

    def servers(self) -> list[dict]:
        return self.request("GET", "/servers").json()

    def add_server(self, name: str, docker_url: str, bind_address: str) -> dict:
        body = {"name": name, "docker_url": docker_url, "bind_address": bind_address}
        return self.request("POST", "/servers", json=body).json()

    def profiles(self) -> list[dict]:
        return self.request("GET", "/profiles").json()

    def tools(self) -> list[dict]:
        return self.request("GET", "/tools").json()

    def events(self, after: int | None = None, sandbox_id: str | None = None, type: str | None = None):
        """Follows the workspace's events (sandbox.state, run.started, run.step, run.finished, takeover.started,
        takeover.ended) as they happen, from after `after` (a seq) or from now. Pass the last event's seq back to
        resume after a disconnect."""
        params = {k: v for k, v in {"after": after, "sandbox_id": sandbox_id, "type": type}.items() if v is not None}
        res = self.request("GET", "/events/stream", params=params, stream=True, timeout=None)
        for raw in res.iter_lines():
            line = raw.decode()
            if line.startswith("data: "):
                yield json.loads(line[6:])

    def usage(self, since: str, until: str, group_by: str = "sandbox", metric: str | None = None) -> list[dict]:
        """Metered usage between two ISO times, by sandbox, day or a label (group_by="label:end_user")."""
        params = {"from": since, "to": until, "group_by": group_by, "metric": metric}
        return self.request("GET", "/usage", params={k: v for k, v in params.items() if v}).json()["rows"]

    def export_trajectories(self, path: str, wait: bool = True, timeout: int = 3600, **filters) -> dict:
        """Exports finished agent runs (run_id=, or sandbox_id=, labels=, from_=, to=) as a .tar.gz of JSONL and
        frames, and saves it to `path` once the server has built it."""
        body = {("from" if k == "from_" else k): v for k, v in filters.items()}
        export = self.request("POST", "/agent/exports", json=body).json()
        deadline = time.monotonic() + timeout
        while wait and export["state"] in ("queued", "running"):
            if time.monotonic() > deadline:
                raise ZooError(f"export {export['id']} wasn't ready within {timeout}s")
            time.sleep(2)
            export = self.request("GET", f"/agent/exports/{export['id']}").json()
        if export["state"] == "failed":
            raise ZooError(f"export failed: {export['error']}")
        if wait:
            res = self.request("GET", f"/agent/exports/{export['id']}/download", stream=True, timeout=None)
            with open(path, "wb") as f:
                f.writelines(res.iter_content(1 << 20))
        return export

    def benchmark(self, suite: str | dict, models: list[dict], parallel: int = 2, wait: bool = True) -> dict:
        """Runs a task suite (YAML/JSON text, or parsed) against models ([{"provider", "model"}]) in parallel
        sandboxes; returns the benchmark with its per-model summary and per-task results."""
        body = {"suite": suite, "models": models, "parallel": parallel}
        benchmark = self.request("POST", "/benchmarks", json=body).json()
        while True:
            detail = self.request("GET", f"/benchmarks/{benchmark['id']}").json()
            if not wait or detail["state"] not in ("queued", "running"):
                return detail
            time.sleep(10)


class Sandbox:
    def __init__(self, client: Zoo, data: dict):
        self.client = client
        self.data = data

    def __getattr__(self, name: str):
        if name in ("id", "name", "kind", "server_id", "status", "error_message"):
            return self.data[name]
        return lambda **kwargs: self.tool(name, **kwargs)

    def __repr__(self):
        return f"Sandbox({self.id!r}, {self.name!r}, {self.status!r})"

    def refresh(self) -> "Sandbox":
        self.data = self.client.request("GET", f"/sandboxes/{self.id}").json()
        return self

    def wait(self, timeout: int = 120) -> "Sandbox":
        deadline = time.monotonic() + timeout
        while self.refresh().status in ("pending", "provisioning"):
            if time.monotonic() > deadline:
                raise ZooError(f"sandbox {self.id} not ready after {timeout}s")
            time.sleep(1)
        if self.status != "running":
            raise ZooError(f"sandbox {self.id} is {self.status}: {self.error_message}")
        return self

    def tool(self, name: str, **args):
        return self.client.request("POST", f"/sandboxes/{self.id}/tools/{name}", json=args).json()

    def exec(self, command: str, timeout: int = 30) -> dict:
        body = {"command": command, "timeout": timeout}
        path = f"/sandboxes/{self.id}/tools/execute_command"
        return self.client.request("POST", path, json=body, timeout=timeout + 30).json()

    def claude(self, prompt: str, cwd: str = "~/work", timeout: int = 900, args: str = "") -> dict:
        command = (
            f"mkdir -p {cwd} && cd {cwd} && claude -p {shlex.quote(prompt)} "
            f"--output-format json --dangerously-skip-permissions {args}"
        )
        result = self.exec(command, timeout=timeout)
        if result["timed_out"]:
            raise ZooError(f"claude timed out after {timeout}s, see ~/.claude/debug in the sandbox")
        try:
            return json.loads(result["stdout"])
        except ValueError:
            raise ZooError(f"claude failed: {result['stderr'] or result['stdout']}")

    def screenshot(self) -> bytes:
        return base64.b64decode(self.tool("screenshot"))

    def ask(self, message: str, model: str | None = None, provider: str | None = None):
        """Runs the server-side CUA agent on this sandbox and yields its events as they stream in:
        {"type": "user" | "reasoning" | "action" | "text" | "status" | "error", "text": ...}, then
        {"type": "usage", "steps", "tokens", "cost", "elapsed", "max_steps", ...} after each model call and
        {"type": "done", "state": "succeeded" | "failed" | "cancelled"} at the end. An action event's "id" fetches the
        screenshot it was decided on with step_screenshot()."""
        body = {"message": message, "model": model, "provider": provider, "stream": True}
        res = self.client.request("POST", f"/sandboxes/{self.id}/agent", json=body, stream=True, timeout=None)
        for line in res.iter_lines(decode_unicode=True):
            if line and line.startswith("data: "):
                yield json.loads(line[6:])

    def stop_agent(self):
        self.client.request("POST", f"/sandboxes/{self.id}/agent/stop")

    def takeover(self) -> dict:
        """Pauses the running agent before its next action, so a person can use the screen."""
        return self.client.request("POST", f"/sandboxes/{self.id}/agent/takeover").json()

    def release(self) -> dict:
        """Hands the screen back; the agent carries on from it as it is now."""
        return self.client.request("POST", f"/sandboxes/{self.id}/agent/release").json()

    def viewer_link(self, mode: str = "view", expires_in: int = 60, end_user: str | None = None) -> dict:
        """A link to this sandbox's screen for someone without a Zoo account: "view" only watches, "control" can
        type and click (and take over from the agent). Its "url" opens in a browser or an iframe; expires_in is in
        minutes."""
        body = {"mode": mode, "expires_in_minutes": expires_in, "end_user": end_user}
        return self.client.request("POST", f"/sandboxes/{self.id}/viewer-links", json=body).json()

    def set_labels(self, labels: dict[str, str]) -> "Sandbox":
        """Replaces the sandbox's labels."""
        self.data = self.client.request("PATCH", f"/sandboxes/{self.id}", json={"labels": labels}).json()
        return self

    def agent_state(self) -> dict:
        """The conversation, and the latest run with its usage and limits."""
        return self.client.request("GET", f"/sandboxes/{self.id}/agent").json()

    def step_screenshot(self, message_id: str) -> bytes:
        """The screen the agent saw before an action (an action message's or event's id)."""
        return self.client.request("GET", f"/sandboxes/{self.id}/agent/messages/{message_id}/screenshot").content

    def hotkey(self, *keys: str) -> dict:
        return self.tool("hotkey", keys=list(keys))

    def start(self, wait: bool = True) -> "Sandbox":
        self.data = self.client.request("POST", f"/sandboxes/{self.id}/start").json()
        return self.wait() if wait else self

    def stop(self, wait: bool = True, timeout: int = 120) -> "Sandbox":
        """Stops the sandbox. Stopping runs as a job on the server, so by default this waits until it has stopped."""
        self.data = self.client.request("POST", f"/sandboxes/{self.id}/stop").json()
        deadline = time.monotonic() + timeout
        while wait and (self.data.get("job") or self.status == "running"):
            if time.monotonic() > deadline:
                raise ZooError(f"sandbox {self.id} didn't stop within {timeout}s")
            time.sleep(1)
            self.refresh()
        return self

    def delete(self, wait: bool = True, timeout: int = 120):
        """Deletes the sandbox. Deleting runs as a job on the server, so by default this waits until it is gone."""
        self.client.request("DELETE", f"/sandboxes/{self.id}")
        deadline = time.monotonic() + timeout
        while wait:
            try:
                self.refresh()
            except ZooError:
                return  # 404: deleted
            if time.monotonic() > deadline:
                raise ZooError(f"sandbox {self.id} wasn't deleted within {timeout}s")
            time.sleep(1)

    def set_secret(self, name: str, value: str):
        return self.client.request("PUT", f"/sandboxes/{self.id}/secrets", json={"name": name, "value": value}).json()

    def set_permission(self, permission: str, action: str, effect: str):
        body = {"permission": permission, "action": action, "effect": effect}
        return self.client.request("PUT", f"/sandboxes/{self.id}/permissions", json=body).json()

    def set_network(self, default_action: str = "allow", allow_dns: bool = True):
        body = {"default_action": default_action, "allow_dns": allow_dns}
        return self.client.request("PUT", f"/sandboxes/{self.id}/network", json=body).json()

    def add_rule(self, rule_type: str, value: str, effect: str = "allow"):
        body = {"rule_type": rule_type, "value": value, "effect": effect}
        return self.client.request("POST", f"/sandboxes/{self.id}/network/rules", json=body).json()

    def backup(self, path: str):
        """Exports the home folder as a tar file; snapshots (`snapshot()`) are the faster way to keep a copy."""
        with open(path, "wb") as f:
            f.writelines(self.client.request("GET", f"/sandboxes/{self.id}/backup", stream=True).iter_content(1 << 16))

    def restore(self, path: str):
        with open(path, "rb") as f:
            self.client.request("POST", f"/sandboxes/{self.id}/restore", data=f.read())

    def snapshots(self) -> list[dict]:
        return self.client.request("GET", f"/sandboxes/{self.id}/snapshots").json()

    def snapshot(self, name: str = "", wait: bool = True, timeout: int = 1800) -> dict:
        """Snapshots the home disk on the sandbox's server (a VM must be stopped). Waits until it is ready."""
        snap = self.client.request("POST", f"/sandboxes/{self.id}/snapshots", json={"name": name}).json()
        deadline = time.monotonic() + timeout
        while wait and snap["state"] == "creating":
            if time.monotonic() > deadline:
                raise ZooError(f"snapshot {snap['id']} wasn't ready within {timeout}s")
            time.sleep(1)
            snap = next((s for s in self.snapshots() if s["id"] == snap["id"]), snap)
        if snap["state"] == "failed":
            raise ZooError(f"snapshot failed: {snap['error']}")
        return snap

    def restore_snapshot(self, snapshot_id: str, wait: bool = True, timeout: int = 1800) -> "Sandbox":
        """Replaces the home disk with a snapshot's. The sandbox must be stopped."""
        self.data = self.client.request("POST", f"/sandboxes/{self.id}/snapshots/{snapshot_id}/restore").json()
        deadline = time.monotonic() + timeout
        while wait and self.data.get("job"):
            if time.monotonic() > deadline:
                raise ZooError(f"restoring {snapshot_id} didn't finish within {timeout}s")
            time.sleep(1)
            self.refresh()
        if self.status == "failed":
            raise ZooError(self.data.get("error_message") or "restore failed")
        return self

    def delete_snapshot(self, snapshot_id: str):
        self.client.request("DELETE", f"/sandboxes/{self.id}/snapshots/{snapshot_id}")

    def move(self, server_id: str | None) -> "Sandbox":
        self.data = self.client.request("POST", f"/sandboxes/{self.id}/move", json={"server_id": server_id}).json()
        return self

    def save_profile(self, name: str, app: str = "firefox", profile_id: str | None = None) -> dict:
        """Saves the app's profile from this sandbox. Saving under an existing name (or a profile_id) adds a version."""
        body = {"name": name, "app": app, "profile_id": profile_id}
        return self.client.request("POST", f"/sandboxes/{self.id}/profiles", json=body).json()

    def apply_profile(self, profile_id: str, version: int | None = None) -> dict:
        """Loads a profile, the latest version unless one is given. The app must not be running (409)."""
        params = {"version": version} if version else None
        return self.client.request("POST", f"/sandboxes/{self.id}/profiles/{profile_id}", params=params).json()
