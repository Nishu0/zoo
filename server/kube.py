"""Linux sandboxes on Kubernetes: Kata pods with a PersistentVolumeClaim for /home/zoo.

Selected by server.runtime when ZOO_RUNTIME=kube. ZOO_NAMESPACE names the sandbox namespace. Access uses the
service account inside the cluster or a kubeconfig outside it. Runtime ids are k8s:<namespace>/<pod>."""

import base64
import contextlib
import hashlib
import io
import json
import logging
import math
import os
import re
import shlex
import socket
import ssl
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import yaml
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from db.connection import db_manager
from server import objects
from server.errors import ZooError
from server.sizes import DEFAULT as DEFAULT_SIZE
from server.sizes import Size

NAMESPACE = os.environ.get("ZOO_NAMESPACE", "zoo")
STORAGE_CLASS = os.environ.get("ZOO_STORAGE_CLASS", "zoo-local")
HOME_SIZE = os.environ.get("ZOO_KUBERNETES_HOME_SIZE", "10Gi")
SPARE_VOLUMES = int(os.environ.get("ZOO_SPARE_VOLUMES", "4"))
SNAPSHOT_CLASS = os.environ.get("ZOO_SNAPSHOT_CLASS", "zoo-snapshots")
EGRESS_NAMESPACE = os.environ.get("ZOO_KUBERNETES_EGRESS_NAMESPACE", "") or NAMESPACE
EGRESS_SELECTOR = os.environ.get("ZOO_KUBERNETES_EGRESS_SELECTOR", "app.kubernetes.io/component=egress")
PULL_SECRET = os.environ.get("ZOO_KUBERNETES_IMAGE_PULL_SECRET", "")
HELPER_IMAGE = os.environ.get("ZOO_KUBERNETES_HELPER_IMAGE", "")
# the requirements check starts this under the RuntimeClass on each node: running proves the runtime (and, for
# Kata, KVM) works there. The kubelet's own sandbox image, so nodes already have it.
PROBE_IMAGE = os.environ.get("ZOO_KUBERNETES_PROBE_IMAGE", "registry.k8s.io/pause:3.10")
START_TIMEOUT = float(os.environ.get("ZOO_KUBERNETES_START_TIMEOUT", "600"))
CPU_REQUEST = 0.1
IDLE_MEMORY_MB_DESKTOP = int(os.environ.get("ZOO_IDLE_MEMORY_MB_DESKTOP", "700"))
IDLE_MEMORY_MB_CODE = int(os.environ.get("ZOO_IDLE_MEMORY_MB_CODE", "250"))
SHM = "1Gi"
HOME = "/home/zoo"
CONTAINER = "sandbox"
EGRESS_DIR = "/run/zoo-egress"
PREFIX = "k8s:"
LABELS = {"app.kubernetes.io/managed-by": "zoo", "app.kubernetes.io/part-of": "zoo"}
SPARE_NODE = "zoo.dev/spare-node"
# waiting reasons that won't fix themselves
STUCK = {"ErrImagePull", "ImagePullBackOff", "InvalidImageName", "CreateContainerConfigError", "CreateContainerError"}
CHUNK = 64 * 1024
logger = logging.getLogger(__name__)


def guest_url() -> str:
    service = os.environ.get("ZOO_GATEWAY_SERVICE", "zoo-gateway")
    if not service or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", service):
        raise ZooError("unavailable", "ZOO_GATEWAY_SERVICE must name the Kubernetes gateway service")
    host = service if "." in service else f"{service}.{NAMESPACE}.svc"
    return f"ws://{host}:8001/guest/connect"


def owns(runtime_id: str | None) -> bool:
    return bool(runtime_id) and str(runtime_id).startswith(PREFIX)


def runtime_id(namespace: str, name: str) -> str:
    return f"{PREFIX}{namespace}/{name}"


def parse_id(rid: str) -> tuple[str, str]:
    namespace, _, name = rid[len(PREFIX) :].partition("/")
    if not owns(rid) or not namespace or not name:
        raise ZooError("bad_request", "invalid Kubernetes runtime id", "Use k8s:<namespace>/<pod>.")
    return namespace, name


def pod_name(sandbox_id: str) -> str:
    return f"zoo-sandbox-{sandbox_id}"


def claim_name(sandbox_id: str) -> str:
    return f"zoo-home-{sandbox_id}"


def home_config(sandbox_id: str) -> dict:
    with db_manager.session() as db:
        sandbox = db.get_sandbox(id=sandbox_id)
        if sandbox is None:
            pooled = next((row for row in db.list_pool_sandboxes() if row.id == sandbox_id), None)
            if pooled is None:
                raise ZooError(
                    "sandbox_not_found", "sandbox not found", "Create the sandbox before accessing its home."
                )
            config = {"image": pooled.image, **json.loads(pooled.config)}
        else:
            config = json.loads(sandbox.config)
    if not isinstance(config.get("home_claim"), str) or not config["home_claim"]:
        raise ZooError(
            "unavailable", "sandbox has no home claim configured", "Set config.home_claim when creating the sandbox."
        )
    return config


def home_claim(sandbox_id: str) -> str:
    return home_config(sandbox_id)["home_claim"]


def _cleanup_previous_home_claim(db, sandbox) -> dict:
    config = json.loads(sandbox.config)
    current = config.get("home_claim")
    if not isinstance(current, str) or not current:
        raise ZooError(
            "unavailable", "sandbox has no home claim configured", "Set config.home_claim when creating the sandbox."
        )
    validated_home_claims(config)
    if "previous_home_claim" not in config:
        return config
    previous = config["previous_home_claim"]
    if not isinstance(previous, str) or not previous or previous == current:
        raise ZooError(
            "conflict",
            "sandbox has an invalid previous home claim",
            "Repair config.previous_home_claim before cleanup.",
        )
    delete_claim(NAMESPACE, previous, wait=True)
    updated = db.clear_previous_home_claim(sandbox_id=sandbox.id, home_claim=current, previous_home_claim=previous)
    if updated is None:
        raise ZooError("conflict", "sandbox home changed during cleanup", "Retry cleanup of the previous home claim.")
    return json.loads(updated.config)


def cleanup_previous_home_claim(sandbox_id: str) -> dict:
    with db_manager.session() as db:
        sandbox = db.get_sandbox_for_update(id=sandbox_id)
        if sandbox is None:
            raise ZooError("sandbox_not_found", "sandbox not found", "The home claims remain available.")
        return _cleanup_previous_home_claim(db, sandbox)


class KubeError(RuntimeError):
    def __init__(self, status: int, message: str, reason: str = ""):
        super().__init__(message)
        self.status = status
        self.reason = reason


class NotFound(KubeError):
    """A Kubernetes object does not exist."""


# --- the API client ---


def quantity(value: str | float | None) -> float:
    """A Kubernetes quantity (2Gi, 500m, 1e3) as a number of its base unit."""
    if value is None:
        return 0.0
    text = str(value).strip()
    match = re.fullmatch(r"([0-9.]+(?:[eE][-+]?[0-9]+)?)([a-zA-Z]*)", text)
    if not match:
        raise ValueError(f"not a quantity: {value!r}")
    number, suffix = float(match.group(1)), match.group(2)
    binary = {"Ki": 1, "Mi": 2, "Gi": 3, "Ti": 4, "Pi": 5, "Ei": 6}
    decimal = {"n": -3, "u": -2, "m": -1, "": 0, "k": 1, "K": 1, "M": 2, "G": 3, "T": 4, "P": 5, "E": 6}
    if suffix in binary:
        return number * 1024 ** binary[suffix]
    if suffix in decimal:
        return number * 1000 ** decimal[suffix]
    raise ValueError(f"unknown quantity suffix in {value!r}")


class Api:
    """Requests to the Kubernetes API: in the cluster with the pod's service account, else with a kubeconfig."""

    def __init__(self):
        self.lock = threading.Lock()
        self.loaded = False
        self.server = ""
        self.context: ssl.SSLContext | None = None
        self.token_file = ""
        self.token = ""
        self.exec_plugin: dict | None = None
        self.token_expires = 0.0
        self.in_cluster = False
        self.http: httpx.Client | None = None

    def load(self):
        with self.lock:
            if self.loaded:
                return
            token_file = "/var/run/secrets/kubernetes.io/serviceaccount/token"
            if os.environ.get("KUBERNETES_SERVICE_HOST") and os.path.exists(token_file):
                self.in_cluster_config(token_file)
            else:
                self.kubeconfig()
            self.http = httpx.Client(base_url=self.server, verify=self.context or False, timeout=30)
            self.loaded = True

    def in_cluster_config(self, token_file: str):
        host, port = os.environ["KUBERNETES_SERVICE_HOST"], os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        self.server = f"https://{'[' + host + ']' if ':' in host else host}:{port}"
        self.context = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
        # bound tokens rotate, so it is read again on each request
        self.token_file = token_file
        self.in_cluster = True

    def kubeconfig(self):
        path = (os.environ.get("KUBECONFIG") or os.path.expanduser("~/.kube/config")).split(os.pathsep)[0]
        if not os.path.exists(path):
            raise RuntimeError("no Kubernetes access: not in a cluster, and no kubeconfig at " + path)
        with open(path) as f:
            config = yaml.safe_load(f) or {}
        base = os.path.dirname(os.path.abspath(path))
        name = os.environ.get("ZOO_KUBERNETES_CONTEXT") or config.get("current-context")
        context = next((c["context"] for c in config.get("contexts") or [] if c.get("name") == name), None)
        if context is None:
            raise RuntimeError(f"kubeconfig {path} has no context {name!r}")
        cluster = next(c["cluster"] for c in config.get("clusters") or [] if c.get("name") == context.get("cluster"))
        user = next((u["user"] for u in config.get("users") or [] if u.get("name") == context.get("user")), {}) or {}
        self.server = cluster["server"].rstrip("/")
        if self.server.startswith("https://"):
            if cluster.get("insecure-skip-tls-verify"):
                self.context = ssl.create_default_context()
                self.context.check_hostname = False
                self.context.verify_mode = ssl.CERT_NONE
            else:
                self.context = ssl.create_default_context(**ca_of(cluster, base))
            cert, key = file_of(user, "client-certificate", base), file_of(user, "client-key", base)
            if cert and key:
                self.context.load_cert_chain(cert, key)
        self.token = user.get("token") or ""
        if user.get("tokenFile"):
            self.token_file = os.path.join(base, user["tokenFile"])
        self.exec_plugin = user.get("exec")

    def bearer(self) -> str:
        if self.token_file:
            with open(self.token_file) as f:
                return f.read().strip()
        if self.exec_plugin is not None and time.time() >= self.token_expires:
            self.token, self.token_expires = run_exec_plugin(self.exec_plugin)
        return self.token

    def headers(self) -> dict[str, str]:
        token = self.bearer()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def request(
        self, method: str, path: str, body: Any = None, params: dict | None = None, timeout: float = 30
    ) -> dict:
        self.load()
        assert self.http is not None
        headers = self.headers()
        if method == "PATCH":
            headers["Content-Type"] = "application/merge-patch+json"
        response = self.http.request(method, path, json=body, params=params, headers=headers, timeout=timeout)
        if response.status_code >= 400:
            try:
                status = response.json()
            except ValueError:
                status = {"message": response.text}
            message = f"{method} {path}: {status.get('message') or response.reason_phrase}"
            error = NotFound if response.status_code == 404 else KubeError
            raise error(response.status_code, message, status.get("reason", ""))
        return response.json() if response.content else {}

    def get(self, path: str, **params) -> dict:
        return self.request("GET", path, params=params or None)

    def exists(self, path: str) -> dict | None:
        try:
            return self.get(path)
        except NotFound:
            return None

    def open_exec(self, url: str, where: str, attempts: int = 3):
        """The exec websocket. A handshake that gets no answer is tried again: the command hasn't started then."""
        for attempt in range(attempts):
            try:
                return connect(
                    url,
                    ssl=self.context if url.startswith("wss://") else None,
                    additional_headers=self.headers(),
                    subprotocols=["v4.channel.k8s.io"],  # type: ignore[list-item]
                    max_size=None,
                    open_timeout=10,
                )
            except TimeoutError:
                if attempt == attempts - 1:
                    raise KubeError(0, f"exec in {where}: the API server didn't answer") from None
            except WebSocketException as e:
                if "404" in str(e):
                    raise NotFound(404, f"pod {where} not found") from e
                raise KubeError(0, f"exec in {where}: {e}") from e
        raise AssertionError("unreachable")

    def exec_stream(
        self, namespace: str, pod: str, argv: list[str], container: str | None = None, stdin: bytes | None = None
    ) -> Iterator[tuple[int, bytes]]:
        """Runs argv in the pod; yields (1, stdout) and (2, stderr) chunks as they arrive, then (0, exit code)."""
        self.load()
        params = [("command", a) for a in argv] + [("stdout", "true"), ("stderr", "true")]
        if stdin is not None:
            params.append(("stdin", "true"))
        if container:
            params.append(("container", container))
        url = self.server.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        url += f"/api/v1/namespaces/{namespace}/pods/{pod}/exec?" + urllib.parse.urlencode(params)
        connection = self.open_exec(url, f"{namespace}/{pod}")
        writer = None
        if stdin is not None:
            # the command reads exactly len(stdin) bytes: v4 can't close stdin, so nothing waits for its end
            def feed():
                with contextlib.suppress(WebSocketException, OSError):
                    for i in range(0, len(stdin), CHUNK):
                        connection.send(b"\x00" + stdin[i : i + CHUNK])

            writer = threading.Thread(target=feed, daemon=True)
            writer.start()
        status = None
        try:
            for message in connection:
                frame = message if isinstance(message, bytes) else message.encode()
                if len(frame) < 2:
                    continue
                channel, data = frame[0], frame[1:]
                if channel in (1, 2):
                    yield channel, data
                elif channel == 3:
                    status = json.loads(data)
        except WebSocketException as e:
            raise KubeError(0, f"exec in {namespace}/{pod} was cut off: {e}") from e
        finally:
            connection.close()
            if writer is not None:
                writer.join(5)
        yield 0, str(exit_code(status, f"{namespace}/{pod}")).encode()

    def exec(
        self, namespace: str, pod: str, argv: list[str], container: str | None = None, stdin: bytes | None = None
    ) -> tuple[int, bytes, bytes, bytes]:
        """Runs argv in the pod: exit code, stdout, stderr, and both as they interleaved."""
        out, err, both = bytearray(), bytearray(), bytearray()
        code = 0
        for channel, data in self.exec_stream(namespace, pod, argv, container, stdin):
            if channel == 0:
                code = int(data)
                continue
            (out if channel == 1 else err).extend(data)
            both.extend(data)
        return code, bytes(out), bytes(err), bytes(both)


def ca_of(cluster: dict, base: str) -> dict:
    if cluster.get("certificate-authority-data"):
        return {"cadata": base64.b64decode(cluster["certificate-authority-data"]).decode()}
    if cluster.get("certificate-authority"):
        return {"cafile": os.path.join(base, cluster["certificate-authority"])}
    return {}


def file_of(user: dict, key: str, base: str) -> str | None:
    """A kubeconfig credential as a file path, writing inline (-data) values to a private temporary file."""
    if user.get(f"{key}-data"):
        fd, path = tempfile.mkstemp(prefix="zoo-kube-")
        with os.fdopen(fd, "wb") as f:
            f.write(base64.b64decode(user[f"{key}-data"]))
        return path
    if user.get(key):
        return os.path.join(base, user[key])
    return None


def run_exec_plugin(plugin: dict) -> tuple[str, float]:
    """A token from a kubeconfig exec plugin (aws eks get-token, gke-gcloud-auth-plugin, kubelogin)."""
    env = {**os.environ, **{e["name"]: e["value"] for e in plugin.get("env") or []}}
    env["KUBERNETES_EXEC_INFO"] = json.dumps(
        {"apiVersion": plugin.get("apiVersion"), "kind": "ExecCredential", "spec": {"interactive": False}}
    )
    result = subprocess.run(
        [plugin["command"], *(plugin.get("args") or [])],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"kubeconfig exec plugin {plugin['command']} failed: {result.stderr.strip()[-300:]}")
    status = json.loads(result.stdout).get("status") or {}
    expires = status.get("expirationTimestamp")
    until = time.time() + 300
    if expires:
        from datetime import datetime

        until = datetime.fromisoformat(expires).timestamp() - 60
    return status.get("token", ""), until


def exit_code(status: dict | None, where: str) -> int:
    if status is None:
        raise KubeError(0, f"exec in {where} ended without an exit status")
    if status.get("status") == "Success":
        return 0
    if status.get("reason") == "NonZeroExitCode":
        for cause in (status.get("details") or {}).get("causes") or []:
            if cause.get("reason") == "ExitCode":
                return int(cause.get("message", 1))
        return 1
    raise KubeError(0, f"exec in {where}: {status.get('message') or status}")


api = Api()


def pods_path(namespace: str, name: str = "") -> str:
    return f"/api/v1/namespaces/{namespace}/pods" + (f"/{name}" if name else "")


def claims_path(namespace: str, name: str = "") -> str:
    return f"/api/v1/namespaces/{namespace}/persistentvolumeclaims" + (f"/{name}" if name else "")


def snapshots_path(namespace: str, name: str = "") -> str:
    return f"/apis/snapshot.storage.k8s.io/v1/namespaces/{namespace}/volumesnapshots" + (f"/{name}" if name else "")


def pod_event(obj: dict, deleted: bool = False) -> dict | None:
    status = obj.get("status") or {}
    phase = status.get("phase")
    terminated = [
        state["terminated"]
        for container_status in [*(status.get("initContainerStatuses") or []), *(status.get("containerStatuses") or [])]
        if "terminated" in (state := container_status.get("state") or {})
    ]
    failed = phase == "Failed" or any(state.get("exitCode", 0) != 0 for state in terminated)
    sandbox_terminated = any(
        container_status.get("name") == CONTAINER and "terminated" in (container_status.get("state") or {})
        for container_status in status.get("containerStatuses") or []
    )
    stopped = deleted or obj["metadata"].get("deletionTimestamp") or phase == "Succeeded" or sandbox_terminated
    if not failed and not stopped:
        return None
    metadata = obj["metadata"]
    event = {
        "type": "state",
        "runtime_id": runtime_id(metadata.get("namespace") or NAMESPACE, metadata["name"]),
        "sandbox_id": (metadata.get("labels") or {}).get("zoo.dev/sandbox"),
        "status": "failed" if failed else "stopped",
    }
    if failed:
        event["error"] = pod_problem(obj) or status.get("message") or status.get("reason") or "sandbox pod failed"
    return event


class KubeEvents(Iterator[dict]):
    """A resumable sandbox pod watch with cancellable transport reads."""

    def __init__(self, stop: threading.Event):
        self.stop = stop
        self.lock = threading.Lock()
        self.network: Any = None
        self.response: httpx.Response | None = None
        self.client: httpx.Client | None = None
        self.iterator = self._iterate()
        self.cancellation = threading.Thread(target=self._cancel, name="kube-events-cancel", daemon=True)
        self.cancellation.start()

    def __next__(self) -> dict:
        return next(self.iterator)

    def close(self):
        self.stop.set()
        self._close_transport()

    def _cancel(self):
        self.stop.wait()
        self._close_transport()

    def _close_transport(self):
        with self.lock:
            network, self.network = self.network, None
            response, self.response = self.response, None
            client, self.client = self.client, None
        if network is not None:
            with contextlib.suppress(OSError):
                network.get_extra_info("socket").shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                network.close()
        if response is not None:
            with contextlib.suppress(httpx.HTTPError, OSError):
                response.close()
        if client is not None:
            with contextlib.suppress(httpx.HTTPError, OSError):
                client.close()

    def _trace(self, name: str, info: dict):
        if name not in {"connection.connect_tcp.complete", "connection.start_tls.complete"}:
            return
        with self.lock:
            self.network = info["return_value"]
        if self.stop.is_set():
            self._close_transport()

    def _check_response(self, response: httpx.Response):
        if response.status_code < 400:
            return
        response.read()
        try:
            status = response.json()
        except ValueError:
            status = {"message": response.text}
        error = NotFound if response.status_code == 404 else KubeError
        raise error(response.status_code, status.get("message") or response.reason_phrase, status.get("reason", ""))

    def _iterate(self) -> Iterator[dict]:
        version = ""
        delay = 1.0
        path = pods_path(NAMESPACE)
        selector = "app.kubernetes.io/component=sandbox"
        try:
            if self.stop.is_set():
                return
            api.load()
            while not self.stop.is_set():
                expired = False
                try:
                    client = httpx.Client(base_url=api.server, verify=api.context or False, timeout=4)
                    with self.lock:
                        self.client = client
                    if self.stop.is_set():
                        return
                    if not version:
                        response = client.get(
                            path,
                            params={"labelSelector": selector},
                            headers=api.headers(),
                            extensions={"trace": self._trace},
                        )
                        if self.stop.is_set():
                            return
                        self._check_response(response)
                        listing = response.json()
                        version = listing["metadata"]["resourceVersion"]
                        inventory = []
                        for obj in listing.get("items") or []:
                            pod = Pod(NAMESPACE, obj["metadata"]["name"], obj)
                            event = pod_event(obj)
                            inventory.append(
                                event
                                if event is not None
                                else {
                                    "runtime_id": pod.id,
                                    "sandbox_id": pod.labels.get("zoo.dev/sandbox"),
                                    "status": "running" if pod.status == "running" else "stopped",
                                }
                            )
                        if self.stop.is_set():
                            return
                        yield {
                            "type": "reconcile",
                            "running_ids": [item["runtime_id"] for item in inventory if item["status"] == "running"],
                            "sandboxes": inventory,
                        }
                    if self.stop.is_set():
                        return
                    with client.stream(
                        "GET",
                        path,
                        params={
                            "labelSelector": selector,
                            "watch": "true",
                            "resourceVersion": version,
                            "allowWatchBookmarks": "true",
                            "timeoutSeconds": "300",
                        },
                        headers=api.headers(),
                        timeout=httpx.Timeout(4, read=None),
                        extensions={"trace": self._trace},
                    ) as response:
                        with self.lock:
                            self.response = response
                        if self.stop.is_set():
                            return
                        self._check_response(response)
                        delay = 1.0
                        for line in response.iter_lines():
                            if self.stop.is_set():
                                return
                            if not line:
                                continue
                            frame = json.loads(line)
                            obj = frame["object"]
                            if frame["type"] == "ERROR":
                                raise KubeError(
                                    int(obj.get("code") or 0),
                                    obj.get("message") or "pod watch failed",
                                    obj.get("reason", ""),
                                )
                            version = (obj.get("metadata") or {}).get("resourceVersion") or version
                            if frame["type"] not in {"ADDED", "MODIFIED", "DELETED"}:
                                continue
                            event = pod_event(obj, deleted=frame["type"] == "DELETED")
                            if event is not None:
                                yield event
                except KubeError as error:
                    if self.stop.is_set():
                        return
                    if error.status == 410:
                        version = ""
                        expired = True
                    elif error.status in {401, 403, 404}:
                        raise
                    else:
                        logger.warning("Kubernetes pod watch disconnected", extra={"error": str(error)})
                except (httpx.HTTPError, OSError, ValueError) as error:
                    if self.stop.is_set():
                        return
                    logger.warning("Kubernetes pod watch disconnected", extra={"error": str(error)})
                finally:
                    self._close_transport()
                if expired:
                    continue
                if self.stop.wait(delay):
                    return
                delay = min(delay * 2, 30.0)
        finally:
            self.close()


def events(stop: threading.Event) -> KubeEvents:
    return KubeEvents(stop)


def user_argv(argv: list[str], user: str | None, environment: dict | None, workdir: str | None) -> list[str]:
    """Wraps a pods/exec command with its user, environment and working directory."""
    if environment:
        argv = ["env", *[f"{k}={v}" for k, v in environment.items()], *argv]
    if user and user not in ("root", "0"):
        argv = ["runuser", "-u", user, "--", *argv]
    if workdir:
        argv = ["sh", "-c", 'cd "$1" && shift && exec "$@"', "sh", workdir, *argv]
    return argv


class Pod:
    """A Kubernetes pod and its exec and archive operations."""

    def __init__(self, namespace: str, name: str, obj: dict | None = None):
        self.namespace = namespace
        self.name = name
        self.obj = obj if obj is not None else api.get(pods_path(namespace, name))

    @property
    def id(self) -> str:
        return runtime_id(self.namespace, self.name)

    def reload(self):
        self.obj = api.get(pods_path(self.namespace, self.name))

    @property
    def labels(self) -> dict[str, str]:
        return self.obj["metadata"].get("labels") or {}

    @property
    def node(self) -> str:
        return self.obj["spec"].get("nodeName") or ""

    @property
    def ip(self) -> str:
        return self.obj.get("status", {}).get("podIP") or ""

    def container_state(self) -> dict:
        for status in self.obj.get("status", {}).get("containerStatuses") or []:
            if status.get("name") == CONTAINER:
                return status.get("state") or {}
        return {}

    @property
    def status(self) -> str:
        if self.obj["metadata"].get("deletionTimestamp"):
            return "removing"
        if self.obj.get("status", {}).get("phase") == "Running" and "running" in self.container_state():
            return "running"
        return "exited" if self.obj.get("status", {}).get("phase") in ("Succeeded", "Failed") else "created"

    @property
    def environment(self) -> dict[str, str]:
        spec = next(c for c in self.obj["spec"]["containers"] if c["name"] == self.container)
        return {entry["name"]: entry.get("value", "") for entry in spec.get("env") or []}

    @property
    def addresses(self) -> list[str]:
        return sorted(
            {entry["ip"] for entry in self.obj.get("status", {}).get("podIPs") or [] if entry.get("ip")}
            | ({self.ip} if self.ip else set())
        )

    def execute(
        self,
        cmd,
        user: str | None = None,
        environment: dict | None = None,
        workdir: str | None = None,
    ) -> tuple[int, bytes, bytes]:
        argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
        code, out, err, _ = api.exec(
            self.namespace, self.name, user_argv(argv, user, environment, workdir), self.container
        )
        return code, out, err

    @property
    def container(self) -> str:
        names = [c["name"] for c in self.obj["spec"]["containers"]]
        return CONTAINER if CONTAINER in names else names[0]

    def read_archive(self, path: str) -> Iterator[bytes]:
        """Streams a tar of path from the pod."""
        parent, base = os.path.split(path.rstrip("/") or "/")
        stream = api.exec_stream(
            self.namespace, self.name, ["tar", "-C", parent or "/", "-cf", "-", base], self.container
        )

        errors = bytearray()
        for channel, data in stream:
            if channel == 1:
                yield data
            elif channel == 2:
                errors.extend(data)
            elif int(data) != 0:
                raise KubeError(0, errors.decode(errors="replace").strip() or f"tar exited with {int(data)}")

    def write_archive(self, path: str, data: bytes):
        script = f"head -c {len(data)} | tar -C {shlex.quote(path)} -xf -"
        code, _, err, _ = api.exec(self.namespace, self.name, ["sh", "-c", script], self.container, stdin=data)
        if code != 0:
            raise KubeError(0, err.decode(errors="replace").strip() or f"tar exited with {code}")

    def remove(self):
        node = self.node
        delete_pod(self.namespace, self.name)
        if node and self.labels.get("app.kubernetes.io/component") == "sandbox":
            forget_policy(self.id, node)


class HelperPod(Pod):
    """A one-shot root pod with claims mounted for work on a sandbox's home disk."""

    def __init__(self, namespace: str, image: str, volumes: dict[str, str], labels: dict[str, str], node: str = ""):
        self.namespace = namespace
        self.name = f"zoo-helper-{uuid.uuid4().hex[:12]}"
        self.image = image
        self.volumes = volumes
        self.extra_labels = labels
        self.target_node = node
        self.obj = {}

    def start(self):
        node = self.target_node
        mounts, volumes = [], []
        for index, (claim, mount) in enumerate(self.volumes.items()):
            ensure_claim(self.namespace, claim)
            mounts.append({"name": f"v{index}", "mountPath": mount, "readOnly": mount == "/from"})
            volumes.append(
                {"name": f"v{index}", "persistentVolumeClaim": {"claimName": claim, "readOnly": mount == "/from"}}
            )
            selected = (api.get(claims_path(self.namespace, claim))["metadata"].get("annotations") or {}).get(
                "volume.kubernetes.io/selected-node"
            )
            if node and selected and node != selected:
                raise ZooError(
                    "conflict", "home claim is bound to another node", "Use a new claim for the target node."
                )
            node = node or selected or ""
        spec: dict = {
            "runtimeClassName": "kata",
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "terminationGracePeriodSeconds": 1,
            "containers": [
                {
                    "name": CONTAINER,
                    "image": self.image,
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["sleep", "21600"],
                    "securityContext": {"runAsUser": 0, "allowPrivilegeEscalation": False},
                    "volumeMounts": mounts,
                }
            ],
            "volumes": volumes,
        }
        if node:
            spec["affinity"] = on_node(node)
        pull_secrets(spec)
        labels = {**LABELS, "app.kubernetes.io/component": "helper", **sanitize_labels(self.extra_labels)}
        self.obj = api.request(
            "POST", pods_path(self.namespace), {"metadata": {"name": self.name, "labels": labels}, "spec": spec}
        )
        self.obj = wait_running(self.namespace, self.name, START_TIMEOUT)

    def remove(self):
        delete_pod(self.namespace, self.name)


def sanitize_labels(labels: dict[str, str]) -> dict[str, str]:
    return {re.sub(r"[^\w.-]", "-", k): re.sub(r"[^\w.-]", "-", v)[:63] for k, v in (labels or {}).items()}


def container(rid: str) -> Pod:
    return Pod(*parse_id(rid))


def local_server(server):
    if server is not None:
        raise ZooError(
            "not_supported",
            "Kubernetes sandboxes do not run on registered servers",
            "Use the configured Kubernetes cluster.",
        )


def exec_command(
    rid: str,
    cmd: str | list[str],
    user: str | None = "zoo",
    environment: dict[str, str] | None = None,
    workdir: str | None = None,
) -> tuple[int, bytes, bytes]:
    return container(rid).execute(cmd, user=user, environment=environment, workdir=workdir)


def root_exec(rid: str, script: str) -> str:
    code, out, err = exec_command(rid, ["sh", "-c", script], user="root")
    if code != 0:
        raise KubeError(0, (err or out).decode(errors="replace").strip() or f"exit code {code}")
    return out.decode(errors="replace")


def remove_container(rid: str):
    with contextlib.suppress(NotFound):
        container(rid).remove()


def remove_named_container(name: str):
    with contextlib.suppress(NotFound):
        Pod(NAMESPACE, name).remove()


def stop(rid: str):
    remove_container(rid)


def remove_volume(sandbox_id: str, server=None):
    local_server(server)
    with db_manager.session() as db:
        sandbox = db.get_sandbox_for_update(id=sandbox_id)
        if sandbox is not None:
            config = _cleanup_previous_home_claim(db, sandbox)
        else:
            pooled = db.get_pool_sandbox_for_update(id=sandbox_id)
            if pooled is None:
                raise ZooError("sandbox_not_found", "sandbox not found", "Pass the persisted home config for cleanup.")
            config = json.loads(pooled.config)
        remove_home(config)


def validated_home_claims(config: dict) -> list[str]:
    values = [config.get("home_claim")]
    if "previous_home_claim" in config:
        values.insert(0, config["previous_home_claim"])
    names: list[str] = []
    for value in values:
        if (
            not isinstance(value, str)
            or len(value) > 253
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*", value)
            or value in names
        ):
            raise ZooError("conflict", "invalid home claim config", "Pass valid, distinct persisted home claim names.")
        names.append(value)
    return names


def remove_home(config: dict):
    names = validated_home_claims(config)
    for name in names:
        delete_claim(NAMESPACE, name, wait=True)


def is_running(rid: str) -> bool:
    try:
        return container(rid).status == "running"
    except NotFound:
        return False


def export_dir(rid: str, path: str) -> bytes:
    return b"".join(container(rid).read_archive(path))


def read_archive(rid: str, path: str) -> bytes:
    return export_dir(rid, path)


def write_archive(rid: str, path: str, data: bytes) -> bool:
    container(rid).write_archive(path, data)
    return True


def import_dir(rid: str, parent: str, data: bytes):
    root_exec(rid, f"mkdir -p {shlex.quote(parent)} && chown zoo:zoo {shlex.quote(parent)}")
    container(rid).write_archive(parent, data)
    root_exec(rid, f"chown -R zoo:zoo {shlex.quote(parent)}")


def export_home(rid: str) -> Iterator[bytes]:
    return container(rid).read_archive(HOME)


def import_home(rid: str, data: bytes):
    container(rid).write_archive("/home", data)
    root_exec(rid, f"chown -R zoo:zoo {HOME}")


APP_PROCESSES = {
    "firefox": ("firefox", "firefox-esr", "firefox-bin"),
    "chromium": ("chromium", "chromium-browse"),
    "chrome": ("chrome", "google-chrome"),
    "vscode": ("code",),
}


def app_running(rid: str, app: str) -> bool:
    names = "|".join(APP_PROCESSES[app])
    script = f'for f in /proc/[0-9]*/comm; do read -r name < "$f" 2>/dev/null && case "$name" in {names}) exit 0;; esac; done; exit 1'
    code, out, err = exec_command(rid, ["sh", "-c", script], user="root")
    if code not in (0, 1):
        raise KubeError(0, (err or out).decode(errors="replace").strip() or f"exit code {code}")
    return code == 0


def tar_file(name: str, data: bytes, mode: int = 0o600) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        entry = tarfile.TarInfo(name)
        entry.size, entry.mode = len(data), mode
        archive.addfile(entry, io.BytesIO(data))
    return buffer.getvalue()


def write_secrets(rid: str, values: dict[str, str]):
    import_dir(rid, "/run/zoo", tar_file("env.json", json.dumps(values).encode()))


def apply_apps(rid: str, effects: dict[str, str]):
    if not effects:
        return
    root_exec(rid, "mkdir -p -m 700 /etc/zoo")
    container(rid).write_archive("/etc/zoo", tar_file("apps.json", json.dumps(effects).encode()))
    lines = []
    for binary, effect in effects.items():
        if effect == "allow":
            lines.append(f"path=$(command -v {shlex.quote(binary)}) && chmod 755 $(readlink -f $path) || true")
        else:
            lines.append(
                f"path=$(command -v {shlex.quote(binary)}) && chown root:root $(readlink -f $path) && chmod 700 $(readlink -f $path) || true"
            )
    root_exec(rid, "\n".join(lines))


def guest_endpoint(rid: str) -> tuple[str, int] | None:
    url = urllib.parse.urlsplit(container(rid).environment.get("ZOO_GUEST_URL", ""))
    if not url.hostname:
        return None
    return url.hostname, url.port or (443 if url.scheme == "wss" else 80)


def existing_pod(namespace: str, name: str) -> Pod | None:
    found = api.exists(pods_path(namespace, name))
    return Pod(namespace, name, found) if found is not None else None


def on_node(node: str) -> dict:
    """Affinity for one node. Unlike nodeName it goes through the scheduler, which binds a new claim that waits for
    its first consumer, and keeps the RuntimeClass's tolerations in play."""
    term = {"matchFields": [{"key": "metadata.name", "operator": "In", "values": [node]}]}
    return {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": [term]}}}


def pull_secrets(spec: dict):
    if PULL_SECRET:
        spec["imagePullSecrets"] = [{"name": PULL_SECRET}]


def ensure_claim(namespace: str, name: str, source: dict | None = None, storage: str = HOME_SIZE, node: str = ""):
    """Creates the claim unless it exists."""
    if not STORAGE_CLASS:
        raise ZooError("unavailable", "Kubernetes home volumes require a StorageClass", "Set ZOO_STORAGE_CLASS.")
    existing = api.exists(claims_path(namespace, name))
    if existing is not None:
        if existing["metadata"].get("deletionTimestamp"):
            raise ZooError("conflict", "home claim is being deleted", "Wait for volume cleanup before restarting.")
        if node:
            selected = (existing["metadata"].get("annotations") or {}).get("volume.kubernetes.io/selected-node")
            if selected and selected != node:
                raise ZooError(
                    "conflict",
                    "home claim has a different selected node",
                    "Keep the persisted node or move the volume.",
                )
            if (existing.get("status") or {}).get("phase") == "Bound":
                bound_claim(namespace, name, node)
            elif not selected:
                api.request(
                    "PATCH",
                    claims_path(namespace, name),
                    {
                        "metadata": {
                            "resourceVersion": existing["metadata"]["resourceVersion"],
                            "annotations": {"volume.kubernetes.io/selected-node": node},
                        }
                    },
                )
        return
    spec: dict = {
        "accessModes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": storage}},
        "storageClassName": STORAGE_CLASS,
    }
    if source is not None:
        spec["dataSource"] = source
    body = {"metadata": {"name": name, "labels": {**LABELS, "app.kubernetes.io/component": "home"}}, "spec": spec}
    if node:
        body["metadata"]["annotations"] = {"volume.kubernetes.io/selected-node": node}
    try:
        api.request("POST", claims_path(namespace), body)
    except KubeError as e:
        if e.status != 409:
            raise
        ensure_claim(namespace, name, source, storage, node)


def spare_storage_check():
    if SPARE_VOLUMES < 0 or quantity(HOME_SIZE) <= 0:
        raise ZooError(
            "bad_request", "invalid spare volume settings", "Use a nonnegative count and positive home size."
        )
    storage = api.get(f"/apis/storage.k8s.io/v1/storageclasses/{STORAGE_CLASS}")
    if storage.get("volumeBindingMode") != "WaitForFirstConsumer":
        raise ZooError(
            "unavailable",
            "spare home claims require WaitForFirstConsumer storage",
            "Configure ZOO_STORAGE_CLASS with delayed volume binding.",
        )


def spare_node_label(node: str) -> str:
    return hashlib.sha256(node.encode()).hexdigest()[:16]


def create_spare_claim(node: str) -> dict:
    return api.request(
        "POST",
        claims_path(NAMESPACE),
        {
            "metadata": {
                "name": f"zoo-spare-home-{uuid.uuid4().hex}",
                "labels": {**LABELS, "app.kubernetes.io/component": "spare-home", SPARE_NODE: spare_node_label(node)},
                "annotations": {SPARE_NODE: node},
            },
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": HOME_SIZE}},
                "storageClassName": STORAGE_CLASS,
            },
        },
    )


def spare_claim_state(db) -> tuple[list[dict], dict[str, str], set[str]]:
    references: dict[str, str] = {}
    for row in db.list_kube_home_claims():
        config = json.loads(row.config)
        for key in ("home_claim", "previous_home_claim"):
            name = config.get(key)
            if not isinstance(name, str) or not name:
                continue
            if name in references and references[name] != row.id:
                raise ZooError(
                    "conflict", "home claim has multiple owners", "Repair the persisted home claim pointers."
                )
            references[name] = row.id
    mounted = {
        volume["persistentVolumeClaim"]["claimName"]
        for pod in api.get(pods_path(NAMESPACE)).get("items") or []
        for volume in pod.get("spec", {}).get("volumes") or []
        if volume.get("persistentVolumeClaim")
    }
    claims = (
        api.get(claims_path(NAMESPACE), labelSelector=f"app.kubernetes.io/managed-by=zoo,{SPARE_NODE}").get("items")
        or []
    )
    return claims, references, mounted


def available_spare(claim: dict, node: str, references: dict[str, str], mounted: set[str]) -> bool:
    metadata = claim["metadata"]
    labels = metadata.get("labels") or {}
    annotations = metadata.get("annotations") or {}
    spec = claim["spec"]
    return bool(
        metadata["name"] not in references
        and metadata["name"] not in mounted
        and not metadata.get("deletionTimestamp")
        and labels.get("app.kubernetes.io/component") == "spare-home"
        and labels.get(SPARE_NODE) == spare_node_label(node)
        and annotations.get(SPARE_NODE) == node
        and not labels.get("zoo.dev/sandbox")
        and not annotations.get("volume.kubernetes.io/selected-node")
        and not spec.get("volumeName")
        and not spec.get("dataSource")
        and not spec.get("dataSourceRef")
        and spec.get("storageClassName") == STORAGE_CLASS
        and spec.get("accessModes") == ["ReadWriteOnce"]
        and spec.get("volumeMode", "Filesystem") == "Filesystem"
        and quantity((spec.get("resources") or {}).get("requests", {}).get("storage")) == quantity(HOME_SIZE)
        and (claim.get("status") or {}).get("phase", "Pending") == "Pending"
    )


def own_spare_claim(name: str, sandbox_id: str, node: str):
    for _ in range(3):
        claim = api.get(claims_path(NAMESPACE, name))
        metadata = claim["metadata"]
        labels = metadata.get("labels") or {}
        if (
            metadata.get("deletionTimestamp")
            or labels.get(SPARE_NODE) != spare_node_label(node)
            or (metadata.get("annotations") or {}).get(SPARE_NODE) != node
            or labels.get("zoo.dev/sandbox") not in (None, sandbox_id)
        ):
            raise ZooError("conflict", "spare claim ownership changed", "Retry after volume cleanup completes.")
        if labels.get("zoo.dev/sandbox") == sandbox_id and labels.get("app.kubernetes.io/component") == "home":
            return
        try:
            api.request(
                "PATCH",
                claims_path(NAMESPACE, name),
                {
                    "metadata": {
                        "resourceVersion": metadata["resourceVersion"],
                        "labels": {"app.kubernetes.io/component": "home", "zoo.dev/sandbox": sandbox_id},
                    }
                },
            )
            return
        except KubeError as error:
            if error.status != 409:
                raise
    raise ZooError("conflict", "spare claim changed during assignment", "Retry the sandbox start.")


def prepare_home_claim(sandbox_id: str, node: str, size: Size) -> str:
    storage = f"{size.disk_gb}Gi" if size.disk_gb else HOME_SIZE
    spare = False
    with db_manager.session() as db:
        db.lock_kube_placement(namespace=NAMESPACE)
        sandbox = db.get_sandbox_for_update(id=sandbox_id)
        pooled = db.get_pool_sandbox_for_update(id=sandbox_id) if sandbox is None else None
        owner = sandbox if sandbox is not None else pooled
        if owner is None:
            raise ZooError("sandbox_not_found", "sandbox not found", "Create the sandbox before assigning its home.")
        config = json.loads(owner.config)
        name = config.get("home_claim")
        if not isinstance(name, str) or not name or config.get("node_name") != node:
            raise ZooError(
                "conflict", "home placement changed", "Retry after the current lifecycle operation completes."
            )
        existing = api.exists(claims_path(NAMESPACE, name))
        if existing is not None:
            spare = SPARE_NODE in (existing["metadata"].get("labels") or {})
        elif name != claim_name(sandbox_id):
            raise ZooError("unavailable", "persisted home claim is missing", "Restore its data before restarting.")
        elif quantity(storage) == quantity(HOME_SIZE):
            spare_storage_check()
            claims, references, mounted = spare_claim_state(db)
            candidate = next((claim for claim in claims if available_spare(claim, node, references, mounted)), None)
            if candidate is None:
                candidate = create_spare_claim(node)
            parameters = {
                "spare_claim": candidate["metadata"]["name"],
                "sandbox_id": sandbox_id,
                "home_claim": name,
                "node_name": node,
            }
            updated = (
                db.claim_sandbox_spare_home(**parameters)
                if sandbox is not None
                else db.claim_pool_spare_home(**parameters)
            )
            if updated is None:
                raise ZooError("conflict", "sandbox changed during home assignment", "Retry the sandbox start.")
            name = candidate["metadata"]["name"]
            spare = True
    if spare:
        own_spare_claim(name, sandbox_id, node)
    return name


def refill_spare_volumes() -> dict[str, int]:
    spare_storage_check()
    with db_manager.session() as db:
        db.lock_kube_placement(namespace=NAMESPACE)
        nodes = {node["metadata"]["name"] for node in nodes_for_sandboxes()}
        available: dict[str, list[dict]] = {node: [] for node in nodes}
        claims, references, mounted = spare_claim_state(db)
        for claim in claims:
            metadata = claim["metadata"]
            name = metadata["name"]
            node = (metadata.get("annotations") or {}).get(SPARE_NODE)
            if name in references:
                if not metadata.get("deletionTimestamp"):
                    if not isinstance(node, str) or not node:
                        raise ZooError("conflict", "owned spare has no node", "Repair its spare-node annotation.")
                    own_spare_claim(name, references[name], node)
                continue
            if name in mounted or metadata.get("deletionTimestamp"):
                continue
            if isinstance(node, str) and node in nodes and available_spare(claim, node, references, mounted):
                available[node].append(claim)
            else:
                delete_claim(NAMESPACE, name)
        for node, claims in available.items():
            for claim in claims[SPARE_VOLUMES:]:
                delete_claim(NAMESPACE, claim["metadata"]["name"])
            for _ in range(max(0, SPARE_VOLUMES - len(claims))):
                create_spare_claim(node)
        return {node: SPARE_VOLUMES for node in available}


def delete_claim(namespace: str, name: str, wait: bool = False, timeout: float = 120):
    with contextlib.suppress(NotFound):
        api.request("DELETE", claims_path(namespace, name))
    if wait:
        gone(claims_path(namespace, name), timeout)


def gone(path: str, timeout: float):
    deadline = time.monotonic() + timeout
    while api.exists(path) is not None:
        if time.monotonic() > deadline:
            raise RuntimeError(f"{path} is still there after {int(timeout)}s")
        time.sleep(0.5)


def delete_pod(namespace: str, name: str, wait: bool = True, timeout: float = 120):
    with contextlib.suppress(NotFound):
        api.request("DELETE", pods_path(namespace, name), {"gracePeriodSeconds": 5, "propagationPolicy": "Background"})
    if wait:
        gone(pods_path(namespace, name), timeout)


def wait_running(namespace: str, name: str, timeout: float) -> dict:
    """Waits for the pod's containers to run; fails early on what won't fix itself (a missing image)."""
    deadline = time.monotonic() + timeout
    last = ""
    while True:
        obj = api.get(pods_path(namespace, name))
        status = obj.get("status") or {}
        phase = status.get("phase")
        if phase in ("Succeeded", "Failed"):
            raise RuntimeError(f"pod {name} stopped: {pod_problem(obj) or phase}")
        if phase == "Running" and all(
            "running" in (c.get("state") or {}) for c in status.get("containerStatuses") or []
        ):
            return obj
        problem = pod_problem(obj)
        if problem and any(reason in problem for reason in STUCK):
            raise RuntimeError(f"pod {name} can't start: {problem}")
        last = problem or last
        if time.monotonic() > deadline:
            raise RuntimeError(f"pod {name} didn't start within {int(timeout)}s" + (f": {last}" if last else ""))
        time.sleep(0.5)


def pod_problem(obj: dict) -> str:
    """Why a pod isn't running yet, as the scheduler or kubelet put it."""
    status = obj.get("status") or {}
    for condition in status.get("conditions") or []:
        if condition.get("type") == "PodScheduled" and condition.get("status") == "False":
            return f"{condition.get('reason')}: {condition.get('message')}"
    benign = (None, "PodInitializing", "ContainerCreating", "Completed")
    for c in [*(status.get("initContainerStatuses") or []), *(status.get("containerStatuses") or [])]:
        state = c.get("state") or {}
        waiting, terminated = state.get("waiting"), state.get("terminated")
        if waiting is not None and waiting.get("reason") not in benign:
            return f"{waiting.get('reason')}: {waiting.get('message') or ''}".strip(": ")
        if terminated is not None and (terminated.get("exitCode") or terminated.get("reason") not in benign):
            detail = terminated.get("message") or terminated.get("reason") or ""
            return f"{c.get('name')} exited with {terminated.get('exitCode')}" + (f": {detail}" if detail else "")
    return ""


# The image's /home/zoo, copied into a new home claim on its first mount, as Docker does for an empty volume
SEED_HOME = (
    'if [ -z "$(ls -A /seed | grep -v "^lost+found$")" ]; then cp -a /home/zoo/. /seed/; fi; '
    "chown zoo:zoo /seed && chmod 755 /seed"
)


def cpu_quantity(cpus: float) -> str:
    """CPUs as Kubernetes writes them: whole cores as a number, else millicores."""
    return str(int(cpus)) if cpus == int(cpus) else f"{round(cpus * 1000)}m"


def memory_quantity(mb: int) -> str:
    return f"{mb >> 10}Gi" if mb % 1024 == 0 else f"{mb}Mi"


def sandbox_requests(desktop: bool, size: Size) -> dict[str, str]:
    memory = IDLE_MEMORY_MB_DESKTOP if desktop else IDLE_MEMORY_MB_CODE
    if memory <= 0:
        raise ZooError(
            "unavailable",
            "idle memory requests must be positive",
            "Set the ZOO_IDLE_MEMORY_MB settings to positive values.",
        )
    if not math.isfinite(size.cpus) or size.cpus < CPU_REQUEST or size.memory_mb < memory:
        raise ZooError(
            "bad_request",
            f"sandbox limits must allow at least 100m CPU and {memory}Mi memory",
            "Increase the sandbox size to cover its idle resource requests.",
        )
    return {"cpu": "100m", "memory": memory_quantity(memory)}


def resource_requests(resources: dict) -> dict[str, int]:
    return {
        "cpu": math.ceil(quantity(resources.get("cpu")) * 1000),
        "memory": math.ceil(quantity(resources.get("memory"))),
    }


def pod_requests(obj: dict) -> dict[str, int]:
    spec = obj["spec"]
    regular = {"cpu": 0, "memory": 0}
    sidecars = {"cpu": 0, "memory": 0}
    initial = {"cpu": 0, "memory": 0}
    for container_spec in spec.get("containers") or []:
        requested = resource_requests((container_spec.get("resources") or {}).get("requests") or {})
        for resource in regular:
            regular[resource] += requested[resource]
    for container_spec in spec.get("initContainers") or []:
        requested = resource_requests((container_spec.get("resources") or {}).get("requests") or {})
        if container_spec.get("restartPolicy") == "Always":
            for resource in sidecars:
                sidecars[resource] += requested[resource]
            requested = sidecars.copy()
        else:
            requested = {resource: requested[resource] + sidecars[resource] for resource in requested}
        for resource, amount in initial.items():
            initial[resource] = max(amount, requested[resource])
    overhead = resource_requests(spec.get("overhead") or {})
    pod_level = (spec.get("resources") or {}).get("requests") or {}
    return {
        resource: (
            resource_requests(pod_level)[resource]
            if resource in pod_level
            else max(regular[resource] + sidecars[resource], initial[resource])
        )
        + overhead[resource]
        for resource in regular
    }


def selector_matches(requirements: list[dict], values: dict[str, str]) -> bool:
    for requirement in requirements:
        actual = values.get(requirement["key"])
        expected = requirement.get("values") or []
        operator = requirement["operator"]
        if operator == "In":
            matched = actual in expected
        elif operator == "NotIn":
            matched = actual not in expected
        elif operator == "Exists":
            matched = actual is not None
        elif operator == "DoesNotExist":
            matched = actual is None
        elif operator in {"Gt", "Lt"}:
            try:
                matched = (
                    actual is not None
                    and len(expected) == 1
                    and (int(actual) > int(expected[0]) if operator == "Gt" else int(actual) < int(expected[0]))
                )
            except ValueError:
                matched = False
        else:
            raise ZooError(
                "upstream_error",
                "volume has an unsupported node affinity operator",
                "Check the persistent volume's node affinity.",
            )
        if not matched:
            return False
    return True


def node_affinity_matches(node: dict, affinity: dict) -> bool:
    required = affinity.get("required")
    if required is None:
        return True
    labels = node["metadata"].get("labels") or {}
    fields = {"metadata.name": node["metadata"]["name"]}
    return any(
        bool(term.get("matchExpressions") or term.get("matchFields"))
        and selector_matches(term.get("matchExpressions") or [], labels)
        and selector_matches(term.get("matchFields") or [], fields)
        for term in required.get("nodeSelectorTerms") or []
    )


def bound_claim(namespace: str, name: str, node_name: str) -> dict:
    claim = api.get(claims_path(namespace, name))
    if claim.get("status", {}).get("phase") != "Bound" or not claim["spec"].get("volumeName"):
        raise ZooError(
            "unavailable",
            "sandbox home claim is not bound",
            "Wait for its selected-node provisioner to bind the claim.",
        )
    selected = (claim["metadata"].get("annotations") or {}).get("volume.kubernetes.io/selected-node")
    if selected and selected != node_name:
        raise ZooError(
            "conflict",
            "home claim is bound to another node",
            "Restart on the volume's persisted node or move its home.",
        )
    volume = api.get(f"/api/v1/persistentvolumes/{claim['spec']['volumeName']}")
    node = api.get(f"/api/v1/nodes/{node_name}")
    if not node_affinity_matches(node, volume["spec"].get("nodeAffinity") or {}):
        raise ZooError(
            "conflict",
            "home volume affinity excludes the selected node",
            "Restart on the volume's persisted node or move its home.",
        )
    return claim


def wait_bound(namespace: str, name: str, node_name: str, timeout: float = START_TIMEOUT) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        claim = api.get(claims_path(namespace, name))
        phase = (claim.get("status") or {}).get("phase")
        if phase == "Bound":
            return bound_claim(namespace, name, node_name)
        selected = (claim["metadata"].get("annotations") or {}).get("volume.kubernetes.io/selected-node")
        if phase == "Lost" or claim["metadata"].get("deletionTimestamp") or selected != node_name:
            raise ZooError(
                "unavailable",
                "home claim lost its selected-node binding",
                "Check the CSI provisioner and selected node.",
            )
        if time.monotonic() >= deadline:
            raise ZooError(
                "timeout",
                "home claim did not bind on the selected node",
                "Check TopoLVM capacity and the CSI provisioner.",
            )
        time.sleep(0.1)


def reserve_node(sandbox_id: str, desktop: bool, size: Size) -> str:
    requested = resource_requests(sandbox_requests(desktop, size))
    runtime = api.get("/apis/node.k8s.io/v1/runtimeclasses/kata")
    overhead = resource_requests((runtime.get("overhead") or {}).get("podFixed") or {})
    requested = {resource: requested[resource] + overhead[resource] for resource in requested}
    with db_manager.session() as db:
        db.lock_kube_placement(namespace=NAMESPACE)
        sandbox = db.get_sandbox_for_update(id=sandbox_id)
        pooled = db.get_pool_sandbox_for_update(id=sandbox_id) if sandbox is None else None
        owner = sandbox if sandbox is not None else pooled
        if owner is None:
            raise ZooError("sandbox_not_found", "sandbox not found", "Create the sandbox before placing it.")
        config = json.loads(owner.config)
        claim_name = config.get("home_claim")
        if not isinstance(claim_name, str) or not claim_name:
            raise ZooError(
                "unavailable",
                "sandbox has no home claim configured",
                "Set config.home_claim when creating the sandbox.",
            )
        claim = api.exists(claims_path(NAMESPACE, claim_name))
        volume = None
        if claim is not None and (claim.get("status") or {}).get("phase") == "Bound":
            volume_name = claim["spec"].get("volumeName")
            if not volume_name:
                raise ZooError(
                    "upstream_error", "bound claim has no volume name", "Check the persistent volume binding."
                )
            volume = api.get(f"/api/v1/persistentvolumes/{volume_name}")
        selected = ((claim or {}).get("metadata", {}).get("annotations") or {}).get(
            "volume.kubernetes.io/selected-node"
        )
        persisted = config.get("node_name")
        if persisted and selected and persisted != selected:
            raise ZooError(
                "conflict",
                "persisted node differs from the home claim's selected node",
                "Move the home volume before changing its node.",
            )
        pinned = persisted or selected
        nodes = {node["metadata"]["name"]: node for node in nodes_for_sandboxes()}
        capacity = {
            name: {
                "cpu": math.floor(quantity(node["status"]["allocatable"]["cpu"]) * 1000),
                "memory": math.floor(quantity(node["status"]["allocatable"]["memory"])),
            }
            for name, node in nodes.items()
        }
        active = (
            api.get("/api/v1/pods", fieldSelector="status.phase!=Succeeded,status.phase!=Failed").get("items") or []
        )
        visible: dict[str, str] = {}
        for pod in active:
            metadata = pod["metadata"]
            identity = (
                (metadata.get("labels") or {}).get("zoo.dev/sandbox")
                if metadata.get("namespace") == NAMESPACE
                else None
            )
            node_name = pod["spec"].get("nodeName")
            if identity and node_name:
                visible[identity] = node_name
            if identity == sandbox_id:
                continue
            if node_name in capacity:
                usage = pod_requests(pod)
                for resource in usage:
                    capacity[node_name][resource] -= usage[resource]
        for reservation in db.list_kube_node_reservations():
            if reservation.id == sandbox_id:
                continue
            reserved = json.loads(reservation.config)
            node_name = reserved["node_name"]
            if visible.get(reservation.id) == node_name:
                continue
            if node_name in capacity:
                for resource in requested:
                    capacity[node_name][resource] -= reserved["node_reservation"][resource]
        eligible = [
            name
            for name in nodes
            if (not pinned or name == pinned)
            and (volume is None or node_affinity_matches(nodes[name], volume["spec"].get("nodeAffinity") or {}))
            and all(capacity[name][resource] >= requested[resource] for resource in requested)
        ]
        if not eligible:
            raise ZooError(
                "unavailable",
                "no eligible node has capacity for this sandbox",
                "Free CPU and memory on its persisted node, or add a Kata-capable node.",
            )
        node_name = max(sorted(eligible), key=lambda name: (capacity[name]["memory"], capacity[name]["cpu"]))
        parameters = {
            "node_name": node_name,
            "reservation": json.dumps(requested),
            "sandbox_id": sandbox_id,
            "home_claim": claim_name,
        }
        updated = (
            db.reserve_sandbox_node(**parameters) if sandbox is not None else db.reserve_pool_sandbox_node(**parameters)
        )
        if updated is None:
            raise ZooError(
                "conflict",
                "sandbox changed during node placement",
                "Retry after its current lifecycle operation completes.",
            )
    return node_name


def release_node_reservation(sandbox_id: str, node_name: str):
    with db_manager.session() as db:
        db.lock_kube_placement(namespace=NAMESPACE)
        db.clear_sandbox_node_reservation(sandbox_id=sandbox_id, node_name=node_name)
        db.clear_pool_sandbox_node_reservation(sandbox_id=sandbox_id, node_name=node_name)


def sandbox_pod(
    name: str, image: str, sandbox_id: str, env: dict[str, str], desktop: bool, size: Size = DEFAULT_SIZE
) -> dict:
    config = home_config(sandbox_id)
    claim = config["home_claim"]
    node_name = config.get("node_name")
    if not node_name:
        raise ZooError(
            "unavailable",
            "sandbox has no persisted node",
            "Reserve a node and bind its home claim before creating the pod.",
        )
    bound_claim(NAMESPACE, claim, node_name)
    requested = sandbox_requests(desktop, size)
    home = {"name": "home", "mountPath": HOME}
    security = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["NET_RAW"]}}
    container: dict = {
        "name": CONTAINER,
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        "env": [{"name": k, "value": v} for k, v in env.items()],
        "resources": {
            "requests": requested,
            "limits": {"cpu": cpu_quantity(size.cpus), "memory": memory_quantity(size.memory_mb)},
        },
        "securityContext": security,
        "volumeMounts": [home, {"name": "shm", "mountPath": "/dev/shm"}],
    }
    spec: dict = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "enableServiceLinks": False,
        "terminationGracePeriodSeconds": 10,
        "runtimeClassName": "kata",
        "priorityClassName": "zoo-sandbox",
        "nodeName": node_name,
        "initContainers": [
            {
                "name": "home",
                "image": image,
                "imagePullPolicy": "IfNotPresent",
                "command": ["sh", "-c", SEED_HOME],
                "resources": container["resources"],
                "securityContext": {**security, "runAsUser": 0},
                "volumeMounts": [{"name": "home", "mountPath": "/seed"}],
            }
        ],
        "containers": [container],
        "volumes": [
            {"name": "home", "persistentVolumeClaim": {"claimName": claim}},
            {"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": SHM}},
        ],
    }
    pull_secrets(spec)
    labels = {**LABELS, "app.kubernetes.io/component": "sandbox", "zoo.dev/sandbox": sandbox_id}
    return {"metadata": {"name": name, "labels": labels}, "spec": spec}


def run_container(
    name: str,
    image: str,
    sandbox_id: str,
    env: dict[str, str],
    server=None,
    desktop: bool = True,
    size: Size = DEFAULT_SIZE,
) -> tuple[str, str | None]:
    """Starts or adopts the sandbox's Kata pod; returns its runtime id and, for a desktop, its address."""
    local_server(server)
    sandbox_requests(desktop, size)
    api.load()
    if desktop and not api.in_cluster and "ZOO_GUEST_TOKEN" not in env:
        raise ZooError(
            "guest_unavailable",
            "desktops outside the cluster require the sandbox's guest",
            "Configure ZOO_GATEWAY_SERVICE to the gateway's Kubernetes service.",
        )
    config = home_config(sandbox_id)
    found = existing_pod(NAMESPACE, name)
    if found is not None:
        spec = next(c for c in found.obj["spec"]["containers"] if c["name"] == CONTAINER)
        if (
            found.status != "running"
            or found.labels.get("zoo.dev/sandbox") != sandbox_id
            or spec["image"] != image
            or found.obj["spec"].get("runtimeClassName") != "kata"
            or not config.get("node_name")
            or not any(
                (volume.get("persistentVolumeClaim") or {}).get("claimName") == config["home_claim"]
                for volume in found.obj["spec"].get("volumes") or []
            )
            or (config.get("node_name") and found.node != config["node_name"])
        ):
            # a pod left by an earlier attempt that can't be adopted
            delete_pod(NAMESPACE, name)
            found = None
    if found is None:
        node_name = reserve_node(sandbox_id, desktop, size)
        try:
            prepare_home_claim(sandbox_id, node_name, size)
            config = home_config(sandbox_id)
            ensure_claim(
                NAMESPACE,
                config["home_claim"],
                storage=f"{size.disk_gb}Gi" if size.disk_gb else HOME_SIZE,
                node=node_name,
            )
            wait_bound(NAMESPACE, config["home_claim"], node_name, START_TIMEOUT)
            api.request("POST", pods_path(NAMESPACE), sandbox_pod(name, image, sandbox_id, env, desktop, size))
        finally:
            release_node_reservation(sandbox_id, node_name)
        found = Pod(NAMESPACE, name, wait_running(NAMESPACE, name, START_TIMEOUT))
    else:
        bound_claim(NAMESPACE, config["home_claim"], found.node)
        release_node_reservation(sandbox_id, found.node)
    return found.id, found.ip if desktop else None


def pause(rid: str):
    """Stops every process in a pooled sandbox but its init, so it holds its memory but uses no CPU."""
    namespace, name = parse_id(rid)
    code, _, err, _ = api.exec(namespace, name, ["sh", "-c", "kill -STOP -1"], CONTAINER)
    if code != 0:
        raise RuntimeError(f"couldn't pause {name}: {err.decode(errors='replace').strip()}")


def resume(rid: str):
    namespace, name = parse_id(rid)
    code, _, err, _ = api.exec(namespace, name, ["sh", "-c", "kill -CONT -1"], CONTAINER)
    if code != 0:
        raise RuntimeError(f"couldn't resume {name}: {err.decode(errors='replace').strip()}")


# --- snapshots through CSI ---


def snapshot_name(snapshot_id: str) -> str:
    return f"zoo-volume-{snapshot_id}"


def snapshot_prefix(snapshot_id: str) -> str:
    return f"snapshots/{snapshot_id}/"


HOME_PART_BYTES = 1 << 30
UPLOAD_HOME = r"""
export SHELL=/bin/bash
cd /from
tar -czf - . | split -d -a 4 -b "$PART" --filter '
    cat > /tmp/part && url=$(sed -n "$((10#${FILE#p} + 1))p" /tmp/urls) && [ -n "$url" ] &&
    curl -fsS --retry 3 -T /tmp/part "$url" && echo "$FILE"' - p
"""
DOWNLOAD_HOME = r"""
find /to -mindepth 1 -delete
while read -r url; do curl -fsS --retry 3 "$url"; done < /tmp/urls | tar -xzf - -C /to
"""


def helper_image(sandbox_id: str) -> str:
    image = HELPER_IMAGE or home_config(sandbox_id).get("image")
    if not image:
        raise ZooError(
            "unavailable", "sandbox has no helper image configured", "Set config.image or ZOO_KUBERNETES_HELPER_IMAGE."
        )
    return image


def helper_check(pod: Pod, script: str, environment: dict[str, str] | None = None) -> str:
    code, out, err = pod.execute(["bash", "-c", "set -euo pipefail\n" + script], user="root", environment=environment)
    if code != 0:
        raise ZooError(
            "upstream_error",
            (err or out).decode(errors="replace").strip()[-500:] or f"helper exited with {code}",
            "Check the helper pod and object storage connectivity.",
        )
    return out.decode(errors="replace")


def snapshot_class():
    if not SNAPSHOT_CLASS:
        raise ZooError(
            "unavailable",
            "Kubernetes snapshots require a VolumeSnapshotClass",
            "Set ZOO_SNAPSHOT_CLASS.",
        )


def snapshot(sandbox_id: str, snapshot_id: str, server=None) -> int:
    local_server(server)
    snapshot_class()
    claim = api.get(claims_path(NAMESPACE, home_claim(sandbox_id)))
    node = (claim["metadata"].get("annotations") or {}).get("volume.kubernetes.io/selected-node") or ""
    name = snapshot_name(snapshot_id)
    temporary = f"zoo-volume-copy-{uuid.uuid4().hex}"
    try:
        with contextlib.ExitStack() as cleanup:
            cleanup.callback(remove_csi_snapshot, name)
            storage = csi_snapshot(sandbox_id, snapshot_id, name)
            storage = max(storage, int(quantity(claim["spec"]["resources"]["requests"]["storage"])))
            cleanup.callback(delete_claim, NAMESPACE, temporary, wait=True)
            ensure_claim(
                NAMESPACE,
                temporary,
                {"apiGroup": "snapshot.storage.k8s.io", "kind": "VolumeSnapshot", "name": name},
                storage=str(storage),
                node=node,
            )
            reader = HelperPod(
                NAMESPACE, helper_image(sandbox_id), {temporary: "/from"}, {"zoo.dev/snapshot": snapshot_id}, node=node
            )
            cleanup.callback(reader.remove)
            reader.start()
            size = int(helper_check(reader, "du -sb /from | cut -f1").strip())
            remove_snapshot(snapshot_id)
            keys = [f"{snapshot_prefix(snapshot_id)}home.{index:04d}" for index in range(size // HOME_PART_BYTES + 2)]
            urls = "\n".join(objects.presign("PUT", key) for key in keys) + "\n"
            reader.write_archive("/tmp", tar_file("urls", urls.encode()))
            helper_check(reader, UPLOAD_HOME, {"PART": str(HOME_PART_BYTES)})
            return size
    except Exception:
        remove_snapshot(snapshot_id)
        raise


def restore_snapshot(sandbox_id: str, snapshot_id: str, server=None):
    local_server(server)
    if existing_pod(NAMESPACE, pod_name(sandbox_id)) is not None:
        raise ZooError(
            "conflict",
            "the sandbox pod must be removed before restoring its home",
            "Stop the sandbox before restoring.",
        )
    config = home_config(sandbox_id)
    with db_manager.session() as db:
        sandbox = db.get_sandbox(id=sandbox_id)
        if sandbox is None:
            raise ZooError("sandbox_not_found", "sandbox not found", "Create the sandbox before restoring its home.")
        storage = f"{sandbox.disk_gb}Gi" if sandbox.disk_gb else HOME_SIZE
    ensure_claim(NAMESPACE, config["home_claim"], storage=storage, node=config.get("node_name") or "")
    restore_claim(sandbox_id, config["home_claim"], snapshot_id, config.get("node_name") or "")


def restore_claim(sandbox_id: str, claim: str, snapshot_id: str, node: str = ""):
    prefix = snapshot_prefix(snapshot_id)
    keys = sorted(key for key in objects.keys(prefix) if re.fullmatch(r"home\.[0-9]{4}", key[len(prefix) :]))
    if not keys:
        raise ZooError(
            "not_found", "the snapshot is gone from object storage", "Create a new snapshot before restoring."
        )
    if keys != [f"{prefix}home.{index:04d}" for index in range(len(keys))]:
        raise ZooError(
            "upstream_error", "the snapshot is missing home archive parts", "Create a new complete snapshot."
        )
    writer = HelperPod(
        NAMESPACE, helper_image(sandbox_id), {claim: "/to"}, {"zoo.dev/snapshot": snapshot_id}, node=node
    )
    try:
        writer.start()
        downloads = "\n".join(objects.presign("GET", key) for key in keys) + "\n"
        writer.write_archive("/tmp", tar_file("urls", downloads.encode()))
        helper_check(writer, DOWNLOAD_HOME)
    finally:
        writer.remove()


def remove_snapshot(snapshot_id: str, server=None):
    local_server(server)
    objects.delete_prefix(snapshot_prefix(snapshot_id))


def move_volume(sandbox_id: str, node_name: str, snapshot_id: str | None = None) -> str:
    """Restores a portable snapshot on the target node, commits the home pointer, then deletes the source claim."""
    config = cleanup_previous_home_claim(sandbox_id)
    if node_name not in {node["metadata"]["name"] for node in nodes_for_sandboxes()}:
        raise ZooError("bad_request", "target node cannot run sandbox pods", "Choose a ready node that supports Kata.")
    if existing_pod(NAMESPACE, pod_name(sandbox_id)) is not None:
        raise ZooError(
            "conflict", "the sandbox pod must be removed before moving its home", "Stop the sandbox before moving."
        )
    source = config["home_claim"]
    claim = api.get(claims_path(NAMESPACE, source))
    selected = (claim["metadata"].get("annotations") or {}).get("volume.kubernetes.io/selected-node")
    if config.get("node_name") == node_name and selected == node_name:
        return source
    storage = claim["spec"]["resources"]["requests"]["storage"]
    transfer = snapshot_id or str(uuid.uuid4())
    target = f"zoo-home-{sandbox_id}-{uuid.uuid4().hex[:12]}"
    committed = False
    switching = False
    try:
        snapshot(sandbox_id, transfer)
        ensure_claim(NAMESPACE, target, storage=storage, node=node_name)
        restore_claim(sandbox_id, target, transfer, node=node_name)
        switching = True
        with db_manager.session() as db:
            updated = db.switch_sandbox_home_claim(
                target_claim=target, node_name=node_name, source_claim=source, sandbox_id=sandbox_id
            )
            if updated is None:
                switching = False
                raise ZooError(
                    "conflict", "sandbox placement or status changed during the move", "Stop the sandbox and retry."
                )
        committed = True
        cleanup_previous_home_claim(sandbox_id)
        return target
    finally:
        if not committed and not switching:
            delete_claim(NAMESPACE, target, wait=True)
        if snapshot_id is None:
            remove_snapshot(transfer)


def csi_snapshot(sandbox_id: str, snapshot_id: str, name: str, timeout: float = 600) -> int:
    """A CSI VolumeSnapshot of the sandbox's home claim; returns its size."""
    snapshot_class()
    configured = api.get(f"/apis/snapshot.storage.k8s.io/v1/volumesnapshotclasses/{SNAPSHOT_CLASS}")
    if configured.get("deletionPolicy") != "Delete":
        raise ZooError(
            "unavailable",
            "temporary CSI snapshots require deletionPolicy Delete",
            "Set deletionPolicy: Delete on the configured VolumeSnapshotClass.",
        )
    body = {
        "apiVersion": "snapshot.storage.k8s.io/v1",
        "kind": "VolumeSnapshot",
        "metadata": {
            "name": name,
            "labels": {**LABELS, "zoo.dev/sandbox": sandbox_id, "zoo.dev/snapshot": snapshot_id},
        },
        "spec": {
            "volumeSnapshotClassName": SNAPSHOT_CLASS,
            "source": {"persistentVolumeClaimName": home_claim(sandbox_id)},
        },
    }
    api.request("POST", snapshots_path(NAMESPACE), body)
    deadline = time.monotonic() + timeout
    while True:
        status = api.get(snapshots_path(NAMESPACE, name)).get("status") or {}
        if status.get("error", {}).get("message"):
            remove_csi_snapshot(name)
            raise RuntimeError(f"the volume snapshot failed: {status['error']['message']}")
        if status.get("readyToUse"):
            return int(quantity(status.get("restoreSize")))
        if time.monotonic() > deadline:
            remove_csi_snapshot(name)
            raise RuntimeError(f"the volume snapshot wasn't ready within {int(timeout)}s")
        time.sleep(1)


def remove_csi_snapshot(name: str):
    with contextlib.suppress(NotFound):
        api.request("DELETE", snapshots_path(NAMESPACE, name))
    gone(snapshots_path(NAMESPACE, name), 120)


# --- network policy through the node's egress daemon ---


def egress_daemon(node: str) -> Pod:
    found = (
        api.get(pods_path(EGRESS_NAMESPACE), labelSelector=EGRESS_SELECTOR, fieldSelector=f"spec.nodeName={node}").get(
            "items"
        )
        or []
    )
    running = [Pod(EGRESS_NAMESPACE, p["metadata"]["name"], p) for p in found]
    running = [p for p in running if p.obj.get("status", {}).get("phase") == "Running"]
    if not running:
        raise ZooError(
            "unavailable",
            f"the egress daemon is not running on node {node}",
            "Start the egress DaemonSet on every sandbox node.",
        )
    return running[0]


def apply_network(rid: str, default_action: str, allow_dns: bool, rules: list[tuple[str, str, str]]):
    """Hands the policy to the egress daemon on the sandbox's node (the same daemon as on a Docker host, run by the
    chart as a DaemonSet), which enforces it outside the sandbox."""
    from server import egress

    pod = container(rid)
    addrs = pod.addresses
    if not addrs:
        raise ZooError("unavailable", "the sandbox has no network address", "Wait for the sandbox pod to become ready.")
    endpoint = guest_endpoint(rid)
    resolved = egress.addresses(endpoint[0]) if endpoint else []
    data = egress.policy(rid, addrs, default_action, allow_dns, rules, egress.always(endpoint, resolved))
    daemon = egress_daemon(pod.node)
    prune_policies(daemon)
    daemon.write_archive(EGRESS_DIR, tar_file(egress.file_name(rid), data))


def prune_policies(daemon: Pod):
    """Drops the policies of sandboxes no longer on the daemon's node, so a pod given the same address later
    doesn't inherit one."""
    from server import egress

    code, out, err = daemon.execute(["ls", EGRESS_DIR])
    if code != 0:
        raise KubeError(0, err.decode(errors="replace").strip() or f"could not list {EGRESS_DIR}")
    names = out.decode(errors="replace").split()
    pods = (
        api.get(
            pods_path(NAMESPACE),
            labelSelector="app.kubernetes.io/component=sandbox",
            fieldSelector=f"spec.nodeName={daemon.node}",
        ).get("items")
        or []
    )
    live = {egress.file_name(runtime_id(NAMESPACE, p["metadata"]["name"])) for p in pods}
    stale = [f"{EGRESS_DIR}/{n}" for n in names if n.endswith(".json") and n not in live]
    if stale:
        code, _, err = daemon.execute(["rm", "-f", *stale])
        if code != 0:
            raise KubeError(0, err.decode(errors="replace").strip() or "could not prune network policies")


def forget_policy(rid: str, node: str):
    from server import egress

    # no daemon there means no policy either; a later prune catches anything left
    with contextlib.suppress(Exception):
        egress_daemon(node).execute(["rm", "-f", f"{EGRESS_DIR}/{egress.file_name(rid)}"])


# --- capacity and requirements ---


_free: dict[str, Any] = {}


def nodes_for_sandboxes() -> list[dict]:
    """Ready, schedulable nodes the RuntimeClass can place pods on."""
    found = api.get("/apis/node.k8s.io/v1/runtimeclasses/kata")
    selector = (found.get("scheduling") or {}).get("nodeSelector") or {}
    tolerations = (found.get("scheduling") or {}).get("tolerations") or []
    out = []
    for node in api.get("/api/v1/nodes").get("items") or []:
        labels = node["metadata"].get("labels") or {}
        ready = any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in node.get("status", {}).get("conditions") or []
        )
        tolerated = all(
            any(
                (not tolerance.get("effect") or tolerance["effect"] == taint["effect"])
                and (
                    tolerance.get("operator", "Equal") == "Exists"
                    and (not tolerance.get("key") or tolerance["key"] == taint["key"])
                    or tolerance.get("operator", "Equal") == "Equal"
                    and tolerance.get("key") == taint["key"]
                    and tolerance.get("value", "") == taint.get("value", "")
                )
                for tolerance in tolerations
            )
            for taint in node["spec"].get("taints") or []
            if taint.get("effect") in {"NoSchedule", "NoExecute"}
        )
        if (
            ready
            and tolerated
            and not node["spec"].get("unschedulable")
            and all(labels.get(key) == value for key, value in selector.items())
        ):
            out.append(node)
    return out


def free_memory(ttl: float = 10) -> int | None:
    """The most memory any one sandbox node has left unrequested, for placing against other hosts."""
    if _free and time.monotonic() - _free["at"] < ttl:
        return _free["bytes"]
    try:
        nodes = {n["metadata"]["name"]: quantity(n["status"]["allocatable"]["memory"]) for n in nodes_for_sandboxes()}
        pods = api.get("/api/v1/pods", fieldSelector="status.phase!=Succeeded,status.phase!=Failed").get("items") or []
    except Exception:
        return None
    for pod in pods:
        node = pod["spec"].get("nodeName")
        if node in nodes:
            requested = sum(
                quantity((c.get("resources") or {}).get("requests", {}).get("memory"))
                for c in pod["spec"]["containers"]
            )
            requested += quantity((pod["spec"].get("overhead") or {}).get("memory"))
            nodes[node] -= requested
    free = int(max(nodes.values())) if nodes else 0
    _free.update(at=time.monotonic(), bytes=free)
    return free


def pressured_nodes(share: float) -> set[str]:
    """Sandbox nodes with less than `share` of their allocatable memory left unrequested."""
    nodes = {n["metadata"]["name"]: quantity(n["status"]["allocatable"]["memory"]) for n in nodes_for_sandboxes()}
    left = dict(nodes)
    pods = api.get("/api/v1/pods", fieldSelector="status.phase!=Succeeded,status.phase!=Failed").get("items") or []
    for pod in pods:
        node = pod["spec"].get("nodeName")
        if node in left:
            left[node] -= sum(
                quantity((c.get("resources") or {}).get("requests", {}).get("memory"))
                for c in pod["spec"]["containers"]
            )
            left[node] -= quantity((pod["spec"].get("overhead") or {}).get("memory"))
    return {name for name, total in nodes.items() if total and left[name] < share * total}


def info() -> dict:
    """Cluster capacity for the runtime monitoring interface."""
    nodes = nodes_for_sandboxes()
    first = nodes[0]["status"]["nodeInfo"] if nodes else {}
    running = (
        api.get(
            pods_path(NAMESPACE),
            labelSelector="app.kubernetes.io/component=sandbox",
            fieldSelector="status.phase=Running",
        ).get("items")
        or []
    )
    return {
        "Name": f"Kubernetes ({len(nodes)} node{'s' if len(nodes) != 1 else ''})",
        "OperatingSystem": first.get("osImage", "unknown"),
        "Architecture": first.get("architecture", "unknown"),
        "ServerVersion": first.get("kubeletVersion", "unknown"),
        "NCPU": int(sum(quantity(n["status"]["allocatable"]["cpu"]) for n in nodes)),
        "MemTotal": int(sum(quantity(n["status"]["allocatable"]["memory"]) for n in nodes)),
        "ContainersRunning": len(running),
        "Images": 0,
    }


def ping():
    api.get("/version")


def stats(rid: str) -> dict | None:
    from server.guest import hub

    guest = hub.for_runtime(rid)
    if guest is None or not guest.metrics:
        return None
    usage = {
        "cpu_percent": 0.0,
        "memory_usage": 0,
        "memory_limit": 0,
        "memory_percent": 0.0,
        "network_rx": 0,
        "network_tx": 0,
        "pids": 0,
    }
    return {
        **usage,
        **{key: value for key, value in guest.metrics.items() if value is not None},
        "status": "running",
        "collected_at": datetime.now(UTC).isoformat(),
    }


def check(probe: bool = False) -> list[dict]:
    """Reports every cluster prerequisite as {name, ok, detail}; probe also boots Kata on each sandbox node."""
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str):
        checks.append({"name": name, "ok": ok, "detail": detail})

    try:
        version = api.get("/version")
        add("api", True, f"Kubernetes {version.get('gitVersion', '')}")
    except Exception as e:
        add("api", False, str(e))
    try:
        api.get(pods_path(NAMESPACE), limit="1")
        add("namespace", True, NAMESPACE)
    except Exception as e:
        add("namespace", False, f"{NAMESPACE}: {e}")
    try:
        found = api.exists("/apis/node.k8s.io/v1/runtimeclasses/kata")
        handler = (found or {}).get("handler", "")
        add(
            "runtime class",
            found is not None,
            f"kata (handler {handler})" if found else "no RuntimeClass kata: install Kata Containers (kata-deploy)",
        )
    except Exception as e:
        add("runtime class", False, str(e))
    nodes: list[dict] = []
    try:
        nodes = nodes_for_sandboxes()
        add("nodes", bool(nodes), f"{len(nodes)} ready node{'s' if len(nodes) != 1 else ''} can run sandboxes")
    except Exception as e:
        add("nodes", False, str(e))
    missing_kvm = [node["metadata"]["name"] for node in nodes if not kvm_hint(node)]
    add(
        "kvm",
        bool(nodes) and not missing_kvm,
        "no sandbox nodes available to verify KVM"
        if not nodes
        else f"nodes without KVM evidence: {', '.join(missing_kvm)}; Kata needs bare metal or nested virtualization"
        if missing_kvm
        else f"all {len(nodes)} sandbox nodes show KVM (kata-deploy or node-feature-discovery labels)",
    )
    try:
        if not STORAGE_CLASS:
            raise ZooError("unavailable", "Kubernetes home volumes require a StorageClass", "Set ZOO_STORAGE_CLASS.")
        found = api.exists(f"/apis/storage.k8s.io/v1/storageclasses/{STORAGE_CLASS}")
        add("storage", found is not None, f"StorageClass {STORAGE_CLASS}")
    except Exception as e:
        add("storage", False, str(e))
    try:
        snapshot_class()
        found = api.exists(f"/apis/snapshot.storage.k8s.io/v1/volumesnapshotclasses/{SNAPSHOT_CLASS}")
        add("snapshots", found is not None, f"VolumeSnapshotClass {SNAPSHOT_CLASS}")
    except Exception as e:
        add("snapshots", False, str(e))
    try:
        daemons = api.get(pods_path(EGRESS_NAMESPACE), labelSelector=EGRESS_SELECTOR).get("items") or []
        on = {p["spec"].get("nodeName") for p in daemons if (p.get("status") or {}).get("phase") == "Running"}
        missing = [n["metadata"]["name"] for n in nodes if n["metadata"]["name"] not in on]
        add(
            "egress",
            bool(nodes) and not missing,
            "no sandbox nodes available to verify egress"
            if not nodes
            else f"not running on {', '.join(missing)}"
            if missing
            else "on every sandbox node",
        )
    except Exception as e:
        add("egress", False, str(e))
    if probe:
        for node in nodes:
            ok, detail = probe_node(node["metadata"]["name"])
            add(f"node {node['metadata']['name']}", ok, detail)
    return checks


def kvm_hint(node: dict) -> bool:
    labels = node["metadata"].get("labels") or {}
    allocatable = node.get("status", {}).get("allocatable") or {}
    return (
        labels.get("katacontainers.io/kata-runtime") == "true"
        or labels.get("feature.node.kubernetes.io/cpu-cpuid.VMX") == "true"
        or labels.get("feature.node.kubernetes.io/cpu-cpuid.SVM") == "true"
        or quantity(allocatable.get("devices.kubevirt.io/kvm")) > 0
    )


def probe_node(node: str, timeout: float = 120) -> tuple[bool, str]:
    name = f"zoo-probe-{uuid.uuid4().hex[:8]}"
    spec: dict = {
        "runtimeClassName": "kata",
        "affinity": on_node(node),
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "terminationGracePeriodSeconds": 0,
        "containers": [
            {"name": "probe", "image": PROBE_IMAGE, "resources": {"limits": {"cpu": "100m", "memory": "64Mi"}}}
        ],
    }
    pull_secrets(spec)
    labels = {**LABELS, "app.kubernetes.io/component": "probe"}
    started = time.monotonic()
    try:
        api.request("POST", pods_path(NAMESPACE), {"metadata": {"name": name, "labels": labels}, "spec": spec})
        wait_running(NAMESPACE, name, timeout)
        return True, f"a kata pod started in {time.monotonic() - started:.1f}s"
    except Exception as e:
        return False, str(e)
    finally:
        with contextlib.suppress(Exception):
            delete_pod(NAMESPACE, name, timeout=60)


def main(argv: list[str]) -> int:
    """`python -m server.kube check [--probe]`: the requirements check, for the Helm chart's test hook."""
    if argv[:1] != ["check"]:
        print("usage: ZOO_RUNTIME=kube ZOO_NAMESPACE=<namespace> python -m server.kube check [--probe]")
        return 2
    checks = check(probe="--probe" in argv)
    for c in checks:
        print(f"{'ok  ' if c['ok'] else 'FAIL'} {c['name']}: {c['detail']}")
    return 0 if all(c["ok"] for c in checks) else 1


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
