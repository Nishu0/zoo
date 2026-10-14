"""Explicitly destructive, scoped Zoo fault injection while the SDK performance client runs."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sdk/python"))

from perf import cleanup
from zoo_sdk import Zoo

GUEST_PATTERN = r"^(/usr/local/bin/)?zoo-guest( -user zoo)?$"
DEADLINE = math.inf


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--runtime", choices=("docker", "kube"), required=True)
    result.add_argument("--url", default=os.environ.get("ZOO_URL"))
    result.add_argument("--key", default=os.environ.get("ZOO_API_KEY"), help="API key, preferably via ZOO_API_KEY.")
    result.add_argument("--project", help="Required Docker Compose project containing only this installation.")
    result.add_argument("--namespace", help="Required Kubernetes control-plane namespace.")
    result.add_argument("--sandbox-namespace", help="Required Kubernetes sandbox namespace.")
    result.add_argument("--release", help="Required Helm instance label for control-plane pods.")
    result.add_argument("--context", help="Explicit kubeconfig context; omitted uses the current context.")
    result.add_argument("--kind", choices=("code", "desktop", "browser"), default="desktop")
    result.add_argument("--runs", type=int, default=100)
    result.add_argument("--pattern", choices=("sequential", "staggered", "burst"), default="staggered")
    result.add_argument("--burst", type=int, default=50)
    result.add_argument("--faults", type=int, default=8)
    result.add_argument("--initial-delay", type=float, default=1)
    result.add_argument("--interval", type=float, default=2)
    result.add_argument("--hold", type=float, default=1, help="Docker control-plane kill-to-start delay.")
    result.add_argument("--max-seconds", type=float, default=3600, help="Bound the entire experiment.")
    result.add_argument("--seed", type=int, default=0)
    result.add_argument("--json", type=Path, help="Save performance observations and injected-fault outcomes.")
    result.add_argument(
        "--confirm-destructive",
        action="store_true",
        help="Required: this disrupts control-plane clients and deletes this experiment's sandboxes.",
    )
    return result


def command(arguments: list[str], data: str | None = None, *, recovery: bool = False) -> str:
    budget = 30.0 if recovery else min(30.0, DEADLINE - time.monotonic())
    if budget <= 0:
        raise TimeoutError("chaos experiment deadline exceeded")
    result = subprocess.run(arguments, input=data, check=True, capture_output=True, text=True, timeout=budget)
    return result.stdout.strip()


def kubectl(arguments: argparse.Namespace, namespace: str) -> list[str]:
    return ["kubectl", *(["--context", arguments.context] if arguments.context else []), "-n", namespace]


def validate(arguments: argparse.Namespace) -> None:
    if not arguments.confirm_destructive:
        raise ValueError("--confirm-destructive is required; use a dedicated non-production installation")
    if not arguments.url or not arguments.key:
        raise ValueError("--url and --key (or ZOO_URL and ZOO_API_KEY) are required")
    if min(arguments.runs, arguments.burst, arguments.faults, arguments.interval, arguments.max_seconds) <= 0:
        raise ValueError("runs, burst, faults, interval and max-seconds must be positive")
    if min(arguments.initial_delay, arguments.hold) < 0:
        raise ValueError("initial-delay and hold must not be negative")
    if arguments.runtime == "docker" and not arguments.project:
        raise ValueError("Docker chaos requires an explicit --project")
    if arguments.runtime == "kube" and not all((arguments.namespace, arguments.sandbox_namespace, arguments.release)):
        raise ValueError("Kubernetes chaos requires --namespace, --sandbox-namespace and --release")
    binary = "docker" if arguments.runtime == "docker" else "kubectl"
    if not shutil.which(binary):
        raise ValueError(f"required host tool missing: {binary}")


def sandbox_ids(arguments: argparse.Namespace, run_id: str) -> list[str]:
    budget = min(5.0, DEADLINE - time.monotonic())
    if budget <= 0:
        raise TimeoutError("chaos experiment deadline exceeded")
    with Zoo(api_key=arguments.key, base_url=arguments.url, max_retries=0, timeout=budget) as client:
        owned = []
        for box in client.iter_sandboxes({"zoo.perf": run_id}):
            if time.monotonic() >= DEADLINE:
                raise TimeoutError("chaos target discovery deadline exceeded")
            if box.status == "running":
                owned.append(box.id)
        return owned


def docker_targets(arguments: argparse.Namespace, component: str, run_id: str, generator: random.Random) -> list[str]:
    if component in ("gateway", "api"):
        return command(
            [
                "docker",
                "ps",
                "--quiet",
                "--no-trunc",
                "--filter",
                f"label=com.docker.compose.project={arguments.project}",
                "--filter",
                f"label=com.docker.compose.service={component}",
            ]
        ).split()
    owned = sandbox_ids(arguments, run_id)
    if not owned:
        return []
    sandbox_id = generator.choice(owned)
    return command(["docker", "ps", "--quiet", "--no-trunc", "--filter", f"label=zoo.sandbox={sandbox_id}"]).split()


def kube_targets(arguments: argparse.Namespace, component: str, run_id: str) -> list[dict[str, Any]]:
    namespace = arguments.namespace if component in ("gateway", "api") else arguments.sandbox_namespace
    selector = (
        f"app.kubernetes.io/instance={arguments.release},app.kubernetes.io/component={component}"
        if component in ("gateway", "api")
        else "app.kubernetes.io/component=sandbox"
    )
    pods = json.loads(command([*kubectl(arguments, namespace), "get", "pods", "-l", selector, "-o", "json"]))["items"]
    owned = set(sandbox_ids(arguments, run_id)) if component in ("sandbox", "guest") else None
    return [
        pod
        for pod in pods
        if pod.get("status", {}).get("phase") == "Running"
        and not pod["metadata"].get("deletionTimestamp")
        and (owned is None or pod["metadata"].get("labels", {}).get("zoo.dev/sandbox") in owned)
    ]


def inject(arguments: argparse.Namespace, component: str, run_id: str, generator: random.Random) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {"component": component, "at": datetime.now(UTC).isoformat(), "success": False}
    try:
        if arguments.runtime == "docker":
            targets = docker_targets(arguments, component, run_id, generator)
            if not targets:
                return {**result, "skipped": True, "reason": "no scoped running target"}
            target = generator.choice(targets)
            result["target"] = target
            if component == "guest":
                command(["docker", "exec", "--user", "0", target, "pkill", "-KILL", "-f", GUEST_PATTERN])
            elif component in ("gateway", "api"):
                try:
                    command(["docker", "kill", "--signal=KILL", target])
                    time.sleep(min(arguments.hold, max(0.0, DEADLINE - time.monotonic())))
                finally:
                    command(["docker", "start", target], recovery=True)
                    result["restart_requested"] = True
            else:
                command(["docker", "kill", "--signal=KILL", target])
        else:
            pods = kube_targets(arguments, component, run_id)
            if not pods:
                return {**result, "skipped": True, "reason": "no scoped running target"}
            target = generator.choice(pods)
            name = target["metadata"]["name"]
            namespace = target["metadata"]["namespace"]
            result.update(target=name, pod_uid=target["metadata"]["uid"], namespace=namespace)
            prefix = kubectl(arguments, namespace)
            if component == "guest":
                command([*prefix, "exec", name, "--container", "sandbox", "--", "pkill", "-KILL", "-f", GUEST_PATTERN])
            else:
                body = {
                    "apiVersion": "v1",
                    "kind": "DeleteOptions",
                    "gracePeriodSeconds": 0,
                    "preconditions": {"uid": result["pod_uid"]},
                }
                command(
                    [*prefix, "delete", f"--raw=/api/v1/namespaces/{namespace}/pods/{name}", "-f", "-"],
                    data=json.dumps(body),
                )
        result["success"] = True
    except Exception as error:
        result["error"] = str(error)
    result["seconds"] = time.monotonic() - started
    return result


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    global DEADLINE
    validate(arguments)
    run_id = uuid.uuid4().hex
    generator = random.Random(arguments.seed)
    faults = []
    components = ["gateway", "api", "sandbox", "guest"]
    generator.shuffle(components)
    with tempfile.TemporaryDirectory(prefix="zoo-chaos-") as directory:
        result_path = Path(directory) / "perf.json"
        output_path = Path(directory) / "perf.log"
        environment = {**os.environ, "ZOO_URL": arguments.url, "ZOO_API_KEY": arguments.key}
        launch = [
            sys.executable,
            str(ROOT / "scripts/perf.py"),
            "--kind",
            arguments.kind,
            "--runs",
            str(arguments.runs),
            "--pattern",
            arguments.pattern,
            "--burst",
            str(arguments.burst),
            "--run-id",
            run_id,
            "--json",
            str(result_path),
        ]
        started = time.monotonic()
        DEADLINE = started + arguments.max_seconds
        with output_path.open("w") as output:
            process = subprocess.Popen(launch, cwd=ROOT, env=environment, stdout=output, stderr=subprocess.STDOUT)
            timed_out = False
            try:
                due = started + arguments.initial_delay
                while process.poll() is None:
                    if time.monotonic() - started >= arguments.max_seconds:
                        timed_out = True
                        process.terminate()
                        break
                    if len(faults) < arguments.faults and time.monotonic() >= due:
                        component = components[len(faults) % len(components)]
                        outcome = inject(arguments, component, run_id, generator)
                        faults.append(outcome)
                        print(json.dumps(outcome), flush=True)
                        due = time.monotonic() + arguments.interval
                    time.sleep(0.05)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
        performance = json.loads(result_path.read_text()) if result_path.exists() else None
        cleanup_errors = cleanup(argparse.Namespace(key=arguments.key, url=arguments.url, run_id=run_id))
        successful = {item["component"] for item in faults if item["success"]}
        return {
            "runtime": arguments.runtime,
            "run_id": run_id,
            "seed": arguments.seed,
            "project": arguments.project,
            "namespace": arguments.namespace,
            "sandbox_namespace": arguments.sandbox_namespace,
            "release": arguments.release,
            "context": arguments.context,
            "timed_out": timed_out,
            "perf_exit_code": process.returncode,
            "perf_output": output_path.read_text(),
            "faults": faults,
            "requested_faults": arguments.faults,
            "all_components_injected": successful == set(components),
            "performance": performance,
            "cleanup_errors": cleanup_errors,
            "limitations": [
                "Control-plane disruption affects other clients of the explicitly selected installation.",
                "Sandbox and guest faults target only API sandboxes labeled with this run ID.",
                "Guest-process kill is a reconnect/guest-unavailable experiment, not VM pause.",
                "This measures observed SDK success rates, not a long-term availability or reconnect SLA.",
            ],
        }


def main() -> int:
    command_parser = parser()
    arguments = command_parser.parse_args()
    try:
        report = run(arguments)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        command_parser.exit(2, f"chaos: {error}\n")
    print(report["perf_output"])
    print(
        f"Faults injected: {sum(item['success'] for item in report['faults'])}/{report['requested_faults']}; all components covered: {report['all_components_injected']}"
    )
    if arguments.json:
        arguments.json.write_text(json.dumps(report, indent=2) + "\n")
    return int(
        report["timed_out"]
        or report["perf_exit_code"] != 0
        or not report["all_components_injected"]
        or len(report["faults"]) != report["requested_faults"]
        or bool(report["cleanup_errors"])
        or any(not item["success"] for item in report["faults"])
    )


if __name__ == "__main__":
    raise SystemExit(main())
