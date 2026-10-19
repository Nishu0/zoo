"""Run source development against local Kata infrastructure; never bootstrap a host."""

import argparse
import fcntl
import json
import os
import platform
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import tomllib
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "dev"
NAMESPACE = "zoo-dev"
SANDBOX_NAMESPACE = "zoo-dev-sandboxes"
CHART = ROOT / "deploy" / "helm" / "zoo"
PROJECT = "zoo-dev"
SECRET_KEYS = ("JWT_SECRET", "ZOO_SECRETS_KEY", "POSTGRES_PASSWORD", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")


def command(arguments: list[str], *, data: str | None = None, capture: bool = False) -> str:
    print("+ " + shlex.join(arguments), flush=True)
    result = subprocess.run(arguments, input=data, text=True, capture_output=capture, check=True, cwd=ROOT)
    return result.stdout if capture else ""


def compose(environment_file: Path) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(environment_file),
        "-p",
        PROJECT,
        "-f",
        str(ROOT / "compose.yml"),
        "-f",
        str(ROOT / "scripts" / "dev-compose.yaml"),
    ]


def configuration(mode: str) -> tuple[dict[str, str], Path]:
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    environment_file = STATE / "dev.env"
    if environment_file.exists():
        stored = dotenv_values(environment_file)
        if any(not stored.get(key) for key in SECRET_KEYS):
            raise RuntimeError(
                "development secrets are incomplete; restore them rather than reset keys for an existing database"
            )
        values = {key: str(stored[key]) for key in SECRET_KEYS}
    else:
        values = {key: secrets.token_hex(32) for key in SECRET_KEYS}
    api_url = os.environ.get("ZOO_DEV_API_URL", "http://localhost:8000")
    parsed = urlsplit(api_url)
    if parsed.scheme != "http" or not parsed.hostname or parsed.port != 8000 or parsed.path not in ("", "/"):
        raise RuntimeError("ZOO_DEV_API_URL must be http://HOST:8000; the development proxy does not terminate TLS")
    if parsed.username or parsed.query or parsed.fragment:
        raise RuntimeError("ZOO_DEV_API_URL must not contain credentials, a query or a fragment")
    web_host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    version = os.environ.get("VERSION") or tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    values.update(
        {
            "ZOO_VERSION": version,
            "ZOO_REGISTRY": "zoo-dev",
            "ZOO_RUNTIME": mode,
            "ZOO_ROLE": "api",
            "PYTHON_DOTENV_DISABLED": "1",
            "ZOO_KMS": "",
            "ZOO_SECRETS_KEY_PREVIOUS": "",
            "AWS_SESSION_TOKEN": "",
            "ZOO_DOMAIN": "",
            "COMPOSE_PROFILES": "",
            "ADMIN_EMAILS": "dev@example.com",
            "DATABASE_URL": f"postgresql://zoo:{values['POSTGRES_PASSWORD']}@127.0.0.1:5432/zoo",
            "ZOO_OBJECT_STORE": "s3://zoo",
            "ZOO_S3_ENDPOINT": "http://127.0.0.1:8333",
            "ZOO_API_URL": api_url.rstrip("/"),
            "CORS_ORIGINS": f"http://{web_host}:3000",
            "ZOO_NODE_HOST": os.environ.get("ZOO_DEV_NODE_HOST", ""),
            "ZOO_NODE_PORT": "7443",
            "ZOO_NODE_DIST": str(ROOT / "node" / "dist"),
            "ZOO_GATEWAY_PEERS": "localhost",
            "ZOO_GATEWAY_IP": "127.0.0.1",
            "ZOO_GATEWAY_GRPC_PORT": "7444",
            "ZOO_GATEWAY_HTTP_PORT": "8001",
            "ZOO_GATEWAY_PEER_HTTP_PORT": "8002",
            "ZOO_NETWORK": "zoo",
            "ZOO_NAMESPACE": SANDBOX_NAMESPACE,
            "ZOO_STORAGE_CLASS": "zoo-local",
            "ZOO_SNAPSHOT_CLASS": "zoo-snapshots",
            "ZOO_SANDBOX_IMAGE": f"zoo-dev/zoo-sandbox-desktop:{version}",
            "ZOO_CODE_IMAGE": f"zoo-dev/zoo-sandbox-code:{version}",
            "HOST": "127.0.0.1",
            "PORT": "8003",
            "RELOAD": "1",
        }
    )
    temporary = STATE / "dev.env.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")
    os.replace(temporary, environment_file)
    os.environ.update(values)
    return values, environment_file


class Processes:
    def __init__(self):
        self.children: list[subprocess.Popen[str]] = []

    def start(self, arguments: list[str], cwd: Path = ROOT, environment: dict[str, str] | None = None):
        print("+ " + shlex.join(arguments), flush=True)
        self.children.append(subprocess.Popen(arguments, cwd=cwd, env=environment, text=True, start_new_session=True))

    def check(self):
        for child in self.children:
            if child.poll() is not None:
                raise RuntimeError(f"development subprocess exited: {child.args}")

    def close(self):
        for child in self.children:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 20
        for child in self.children:
            try:
                child.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()

    def ready(self, port: int):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            self.check()
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    return
            except OSError:
                time.sleep(0.5)
        raise RuntimeError(f"development port {port} did not become ready")


def namespace(name: str, *, remove: bool = False, check_only: bool = False):
    found = json.loads(
        command(["kubectl", "get", "namespace", name, "--ignore-not-found", "-o", "json"], capture=True) or "null"
    )
    if found and found["metadata"].get("labels", {}).get("zoo.dev/managed") != "true":
        raise RuntimeError(f"namespace {name} is not owned by this development helper")
    if check_only:
        return
    if remove:
        if found:
            command(["kubectl", "delete", "namespace", name, "--wait=true"])
    else:
        labels = {"zoo.dev/managed": "true"}
        if name == SANDBOX_NAMESPACE:
            labels["pod-security.kubernetes.io/enforce"] = "privileged"
        command(
            ["kubectl", "apply", "-f", "-"],
            data=json.dumps({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": name, "labels": labels}}),
        )


def prepare_kube(values: dict[str, str], children: Processes):
    for port in (5432, 8333, 7443, 7444, 8001, 8002, 8003, 8004):
        try:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(f"port {port} is already occupied; stop other development dependencies first") from error
    server = command(
        ["kubectl", "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"], capture=True
    )
    if urlsplit(server).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("devkube requires a local k3s kubeconfig, not a remote or production context")
    runtime = json.loads(command(["kubectl", "get", "runtimeclass", "kata", "-o", "json"], capture=True))
    if runtime["handler"] != "kata-clh":
        raise RuntimeError("devkube requires the configured kata-clh RuntimeClass named kata")
    command(["kubectl", "get", "storageclass", "zoo-local"])
    command(["kubectl", "get", "volumesnapshotclass", "zoo-snapshots"])
    overrides = STATE / "values.json"
    parsed = urlsplit(values["ZOO_API_URL"])
    overrides.write_text(
        json.dumps(
            {
                "image": {"tag": values["ZOO_VERSION"]},
                "ingress": {"apiHost": parsed.netloc, "host": f"{parsed.hostname}:3000"},
                "gateway": {"nodes": {"host": values["ZOO_NODE_HOST"]}},
            }
        )
    )
    helm = ["helm", "-n", NAMESPACE]
    options = ["-f", str(CHART / "values-dev.yaml"), "-f", str(overrides)]
    rendered = command([*helm, "template", PROJECT, str(CHART), *options], capture=True)
    resources = [item for item in yaml.safe_load_all(rendered) if item]
    gateway = next(
        item
        for item in resources
        if item and item.get("kind") == "Deployment" and item["metadata"]["name"] == "zoo-dev-gateway"
    )
    if gateway["spec"]["template"]["spec"]["containers"][0]["image"] != f"zoo-dev/zoo-gateway:{values['ZOO_VERSION']}":
        raise RuntimeError("the chart must deploy the native Go gateway image before devkube")
    gateway_env = {item["name"]: item for item in gateway["spec"]["template"]["spec"]["containers"][0]["env"]}
    own_ip = gateway_env.get("ZOO_GATEWAY_IP", {}).get("valueFrom", {}).get("fieldRef", {}).get("fieldPath")
    peers = gateway_env.get("ZOO_GATEWAY_PEERS", {}).get("value", "")
    service = next(
        item for item in resources if item["kind"] == "Service" and item["metadata"]["name"] == "zoo-dev-gateway"
    )
    ports = {item["port"] for item in service["spec"]["ports"]}
    if own_ip != "status.podIP" or not peers or peers == "localhost" or not {7444, 8001} <= ports:
        raise RuntimeError(
            "the chart must provide headless gateway peer DNS, downward-API pod IP, and service ports 7444/8001"
        )
    peer_service = next(
        item for item in resources if item["kind"] == "Service" and item["metadata"]["name"] == peers.split(".")[0]
    )
    if peer_service["spec"].get("clusterIP") != "None" or 8002 not in {
        item["port"] for item in peer_service["spec"]["ports"]
    }:
        raise RuntimeError("the gateway peer service must be headless and expose TLS peer HTTP port 8002")
    for image, dockerfile in (
        ("api", "Dockerfile.api"),
        ("gateway", "Dockerfile.gateway"),
        ("egress", "Dockerfile.egress"),
        ("sandbox-desktop", "Dockerfile"),
        ("sandbox-code", "Dockerfile.code"),
    ):
        command(["docker", "build", "-f", dockerfile, "-t", f"zoo-dev/zoo-{image}:{values['ZOO_VERSION']}", "."])
    context = STATE / "gateway-context"
    shutil.copytree(ROOT / "node" / "dist", context / "node-dist", dirs_exist_ok=True)
    (context / "Dockerfile").write_text(
        f"FROM zoo-dev/zoo-gateway:{values['ZOO_VERSION']}\nCOPY node-dist/ /app/node/dist/\n"
    )
    command(["docker", "build", "-t", f"zoo-dev/zoo-gateway:{values['ZOO_VERSION']}", str(context)])
    images = [
        f"zoo-dev/zoo-{image}:{values['ZOO_VERSION']}"
        for image in ("api", "gateway", "egress", "sandbox-desktop", "sandbox-code")
    ]
    with subprocess.Popen(["docker", "save", *images], stdout=subprocess.PIPE) as archive:
        subprocess.run(
            ["sudo", "k3s", "ctr", "--namespace", "k8s.io", "images", "import", "-"], stdin=archive.stdout, check=True
        )
        if archive.stdout:
            archive.stdout.close()
        if archive.wait() != 0:
            raise RuntimeError("docker save failed while importing development images")
    namespace(NAMESPACE)
    namespace(SANDBOX_NAMESPACE)
    database = values["DATABASE_URL"].replace("127.0.0.1", "zoo-postgres")
    credentials = json.dumps(
        {
            "identities": [
                {
                    "name": "zoo",
                    "credentials": [
                        {"accessKey": values["AWS_ACCESS_KEY_ID"], "secretKey": values["AWS_SECRET_ACCESS_KEY"]}
                    ],
                    "actions": ["Admin", "Read", "Write", "List", "Tagging"],
                }
            ]
        }
    )
    shared_secrets = {key: values[key] for key in SECRET_KEYS}
    secret_items = [
        {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": NAMESPACE}, "stringData": data}
        for name, data in (
            ("zoo-config", shared_secrets),
            ("zoo-database", {"url": database}),
            ("zoo-s3", {"s3.json": credentials}),
        )
    ]
    command(
        ["kubectl", "apply", "-f", "-"], data=json.dumps({"apiVersion": "v1", "kind": "List", "items": secret_items})
    )
    bootstrap = STATE / "bootstrap.yaml"
    command(
        [
            "bash",
            "-c",
            'source "$1"; NAMESPACE="$2"; write_bootstrap "$3"',
            "zoo-dev",
            str(ROOT / "install.sh"),
            NAMESPACE,
            str(bootstrap),
        ]
    )
    command(["kubectl", "apply", "-f", str(bootstrap)])
    for workload in ("zoo-postgres", "zoo-object-store"):
        command(["kubectl", "-n", NAMESPACE, "rollout", "status", f"statefulset/{workload}", "--timeout=300s"])
    command(["kubectl", "-n", NAMESPACE, "wait", "--for=condition=complete", "job/zoo-bucket", "--timeout=180s"])
    for service, mapping in (("zoo-postgres", "5432:5432"), ("zoo-object-store", "8333:8333")):
        children.start(
            ["kubectl", "-n", NAMESPACE, "port-forward", f"service/{service}", mapping, "--address=127.0.0.1"]
        )
        children.ready(int(mapping.split(":")[0]))
    command(["uv", "run", "python", "-m", "db.migrate"])
    command([*helm, "upgrade", "--install", PROJECT, str(CHART), *options, "--wait", "--timeout=10m"])
    children.start(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "port-forward",
            "service/zoo-dev-gateway",
            "7444:7444",
            "8001:8001",
            "--address=127.0.0.1",
        ]
    )
    children.ready(7444)
    children.ready(8001)
    children.start(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "port-forward",
            f"service/{peer_service['metadata']['name']}",
            "8002:8002",
            "--address=127.0.0.1",
        ]
    )
    children.ready(8002)
    children.start(
        ["kubectl", "-n", NAMESPACE, "port-forward", "service/zoo-dev-nodes", "7443:7443", "--address=0.0.0.0"]
    )
    children.ready(7443)


def process_start(pid: int) -> str:
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def stop(mode: str):
    record_file = STATE / "process.json"
    if record_file.exists():
        record = json.loads(record_file.read_text())
        if record["mode"] != mode:
            raise RuntimeError(f"the active development flow is {record['mode']}, not {mode}")
        try:
            if process_start(record["pid"]) == record["start"]:
                os.kill(record["pid"], signal.SIGTERM)
                deadline = time.monotonic() + 30
                while record_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.2)
                if record_file.exists():
                    raise RuntimeError("source development supervisor did not stop; inspect its terminal")
        except FileNotFoundError:
            record_file.unlink(missing_ok=True)
    environment_file = STATE / "dev.env"
    if not environment_file.exists():
        return
    os.environ.update({key: value for key, value in dotenv_values(environment_file).items() if value is not None})
    if mode == "docker":
        command([*compose(environment_file), "down"])
    else:
        for name in (NAMESPACE, SANDBOX_NAMESPACE):
            namespace(name, check_only=True)
        command(["helm", "uninstall", PROJECT, "-n", NAMESPACE, "--ignore-not-found"])
        namespace(SANDBOX_NAMESPACE, remove=True)
        namespace(NAMESPACE, remove=True)
        command([*compose(environment_file), "stop", "caddy"])


def interrupted(_signal: int, _frame: object):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("flow", choices=("docker", "docker-down", "kube", "kube-down"))
    arguments = parser.parse_args()
    if platform.system() != "Linux":
        raise RuntimeError("run development inside a Linux KVM host; Macs require an M3+ macOS 15+ nested-KVM Lima VM")
    if arguments.flow.endswith("-down"):
        stop(arguments.flow.removesuffix("-down"))
        return
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        raise RuntimeError("readable/writable /dev/kvm is required; no runc development mode")
    if Path("/sys/kernel/mm/ksm/run").read_text().strip() != "1":
        raise RuntimeError("enable host KSM before development")
    if arguments.flow == "kube" and platform.machine() not in ("x86_64", "amd64"):
        raise RuntimeError(
            "pinned Kata clh ships only on amd64; native ARM Lima supports Docker/QEMU development, not devkube"
        )
    for executable in ("docker", "uv", "bun", "goose") + (
        ("kubectl", "helm", "k3s", "sudo") if arguments.flow == "kube" else ()
    ):
        if shutil.which(executable) is None:
            raise RuntimeError(f"missing development tool: {executable}")
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / "run.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "another source development flow is active; stop it with the matching down target"
            ) from error
        record_file = STATE / "process.json"
        record_file.write_text(
            json.dumps({"pid": os.getpid(), "start": process_start(os.getpid()), "mode": arguments.flow})
        )
        children = Processes()
        signal.signal(signal.SIGTERM, interrupted)
        try:
            values, environment_file = configuration(arguments.flow)
            network = command(["docker", "network", "ls", "--filter", "name=^zoo$", "-q"], capture=True).strip()
            if (
                network
                and command(
                    [
                        "docker",
                        "network",
                        "inspect",
                        network,
                        "--format",
                        '{{index .Labels "com.docker.compose.project"}}',
                    ],
                    capture=True,
                ).strip()
                != PROJECT
            ):
                raise RuntimeError(
                    "the zoo Docker network belongs to another installation; use a dedicated development host"
                )
            if arguments.flow == "docker":
                runtime_info = json.loads(command(["docker", "info", "--format", "{{json .Runtimes}}"], capture=True))
                if "io.containerd.kata.v2" not in runtime_info:
                    raise RuntimeError("register io.containerd.kata.v2 in local Docker before make dev")
                command([*compose(environment_file), "build", "gateway", "egress", "sandbox-image", "code-image"])
                command([*compose(environment_file), "up", "-d", "--wait", "postgres", "seaweedfs", "egress"])
                command([*compose(environment_file), "run", "--rm", "--no-deps", "seaweedfs-init"])
                command(["uv", "run", "python", "-m", "db.migrate"])
                command([*compose(environment_file), "up", "-d", "--wait", "gateway"])
            else:
                prepare_kube(values, children)
            children.ready(8001)
            with urllib.request.urlopen("http://127.0.0.1:8001/healthz", timeout=5):
                pass
            command([*compose(environment_file), "up", "-d", "--no-deps", "caddy"])
            subprocess.run(["bun", "install", "--frozen-lockfile"], cwd=ROOT / "apps" / "web", check=True)
            children.start(["uv", "run", "python", "main.py"])
            worker_environment = {**os.environ, "ZOO_ROLE": "worker", "PORT": "8004"}
            for field in ("CERT", "KEY"):
                source = os.environ.get(f"ZOO_DEV_VM_CONTROL_WORKER_{field}")
                if source:
                    worker_environment[f"ZOO_VM_CONTROL_CLIENT_{field}"] = source
            if os.environ.get("ZOO_VM_CONTROL_CLIENT_CERT") and not all(
                os.environ.get(f"ZOO_DEV_VM_CONTROL_WORKER_{field}") for field in ("CERT", "KEY")
            ):
                raise RuntimeError(
                    "source worker needs its own ZOO_DEV_VM_CONTROL_WORKER_CERT/KEY, not the API certificate"
                )
            children.start(["uv", "run", "python", "main.py"], environment=worker_environment)
            children.start(["bun", "run", "dev", "--host", "0.0.0.0"], cwd=ROOT / "apps" / "web")
            print(
                f"API: {values['ZOO_API_URL']}  Dashboard: {values['CORS_ORIGINS']}\nCtrl-C stops source processes; use the matching make down target for dependencies.",
                flush=True,
            )
            while True:
                children.check()
                time.sleep(0.5)
        finally:
            try:
                children.close()
            finally:
                record_file.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"development failed: {error}", file=sys.stderr)
        sys.exit(1)
