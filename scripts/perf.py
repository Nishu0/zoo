"""Measure Zoo through its SDK on an explicitly supplied installation; never provision it."""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import sys
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sdk/python"))

from zoo_sdk import Zoo
from zoo_sdk.client import Sandbox
from zoo_sdk.errors import ZooError
from zoo_sdk.types import SandboxKind

CREATE_CEILING = 10.0
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
LINUX_KINDS = {"desktop", "browser", "code"}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--url", default=os.environ.get("ZOO_URL"), help="Public API base URL, without /v1.")
    result.add_argument("--key", default=os.environ.get("ZOO_API_KEY"), help="API key, or set ZOO_API_KEY.")
    result.add_argument("--kind", choices=("code", "desktop", "browser", "macos", "windows"), default="desktop")
    result.add_argument("--server-id", help="Enrolled native host for macos/windows measurements.")
    result.add_argument("--runs", type=int, default=100)
    result.add_argument("--pattern", choices=("sequential", "staggered", "burst"), default="sequential")
    result.add_argument("--burst", type=int, default=50, help="Burst size and maximum in-flight samples.")
    result.add_argument(
        "--cold",
        action="store_true",
        help="Admin: set this kind's local pool to zero, wait for drain, then restore its size.",
    )
    result.add_argument("--pool-timeout", type=float, default=180, help="Seconds to wait for the cold pool drain.")
    result.add_argument("--tool-timeout", type=float, default=10)
    result.add_argument("--json", type=Path, help="Write all observations, failures and summaries to this file.")
    result.add_argument(
        "--run-id", default=uuid.uuid4().hex, help="Unique zoo.perf label; chaos.py supplies its own ID."
    )
    return result


def validate(arguments: argparse.Namespace) -> None:
    if not arguments.url or not arguments.key:
        raise ValueError("--url and --key (or ZOO_URL and ZOO_API_KEY) are required")
    target = urlsplit(arguments.url)
    if (
        target.scheme not in ("http", "https")
        or not target.hostname
        or target.username
        or target.query
        or target.fragment
    ):
        raise ValueError("--url must be an HTTP(S) API base URL without credentials, query or fragment")
    if not arguments.run_id or len(arguments.run_id) > 255:
        raise ValueError("run-id must contain 1–255 characters")
    if arguments.runs < 1 or arguments.burst < 1 or min(arguments.pool_timeout, arguments.tool_timeout) <= 0:
        raise ValueError("runs, burst and timeouts must be positive")
    if arguments.kind not in LINUX_KINDS and not arguments.server_id:
        raise ValueError("native guest measurements require --server-id")
    if arguments.cold and (arguments.kind not in LINUX_KINDS or arguments.server_id):
        raise ValueError("--cold drains only the installation's local Linux pool, not native saved-state slots")


def remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("10 second create-to-interactive ceiling exceeded")
    return value


def interactive(box: Sandbox, kind: str, timeout: float) -> None:
    if kind == "code":
        result = box.tool("execute_command", command="true", timeout=1, request_timeout=timeout)
        if result.get("exit_code") != 0 or result.get("timed_out"):
            raise RuntimeError("execute_command(true) did not complete successfully")
    else:
        image = base64.b64decode(box.tool("screenshot", request_timeout=timeout), validate=True)
        if not image.startswith(PNG_SIGNATURE):
            raise RuntimeError("screenshot returned no PNG")


def wait_interactive(client: Zoo, box: Sandbox, kind: str, deadline: float) -> None:
    while True:
        budget = remaining(deadline)
        if box.status == "running":
            try:
                interactive(box, kind, budget)
                remaining(deadline)
                return
            except ZooError as error:
                if error.status in (401, 403, 404, 422):
                    raise
        elif box.status not in ("pending", "provisioning"):
            raise RuntimeError(f"sandbox reached {box.status}: {box.error_message}")
        time.sleep(min(0.02, remaining(deadline)))
        box.data = client._json("GET", f"/sandboxes/{box.id}", timeout=remaining(deadline))


def observation(metric: str, started: float, success: bool, **extra: Any) -> dict[str, Any]:
    return {"metric": metric, "seconds": time.monotonic() - started, "success": success, **extra}


def tool_samples(box: Sandbox, kind: str, timeout: float) -> list[dict[str, Any]]:
    data = b"z" * 1_000_000
    path = f".zoo-perf/{uuid.uuid4().hex}.bin"

    def execute() -> None:
        command = "cmd /c exit 0" if kind == "windows" else "true"
        result = box.tool(
            "execute_command", command=command, timeout=max(1, math.ceil(timeout)), request_timeout=timeout
        )
        if result.get("exit_code") != 0 or result.get("timed_out"):
            raise RuntimeError("execute_command failed")

    def screenshot() -> None:
        if not box.screenshot().startswith(PNG_SIGNATURE):
            raise RuntimeError("screenshot returned no PNG")

    def read() -> None:
        if box.read_file(path, timeout=timeout) != data:
            raise RuntimeError("1 MB file content did not match")

    calls = [
        ("execute_command", execute),
        ("write_file_1mb", lambda: box.write_file(path, data, timeout=timeout)),
        ("read_file_1mb", read),
    ]
    if kind != "code":
        calls += [
            ("screenshot", screenshot),
            ("click", lambda: box.tool("click", x=0, y=0, request_timeout=timeout)),
            ("type_text_20", lambda: box.tool("type_text", text="zoo-perf-12345678901", request_timeout=timeout)),
            ("accessibility_tree", lambda: box.tool("accessibility_tree", request_timeout=timeout)),
        ]
    samples = []
    for metric, call in calls:
        started = time.monotonic()
        try:
            call()
            samples.append(observation(metric, started, True))
        except Exception as error:
            samples.append(observation(metric, started, False, error=str(error)))
    return samples


@contextmanager
def pool_mode(arguments: argparse.Namespace) -> Iterator[dict[str, Any]]:
    if not arguments.cold:
        yield {"mode": "pool_enabled_unverified", "warm_claim_verified": False}
        return
    with Zoo(api_key=arguments.key, base_url=arguments.url, timeout=10, max_retries=0) as client:
        entries = client._json("GET", "/pool")
        entry = next((item for item in entries if item["kind"] == arguments.kind and item["server_id"] is None), None)
        if entry is None:
            raise RuntimeError("local pool entry missing; --cold requires an installation admin key")
        original_size = entry["size"]
        body = {"kind": arguments.kind, "server_id": None, "size": 0}
        try:
            client._json("PUT", "/pool", json=body)
            deadline = time.monotonic() + arguments.pool_timeout
            while True:
                current = next(
                    item
                    for item in client._json("GET", "/pool")
                    if item["kind"] == arguments.kind and item["server_id"] is None
                )
                if current["size"] == 0 and current["idle"] == 0 and current["booting"] == 0:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("pool did not drain; no cold measurements were started")
                time.sleep(0.25)
            yield {"mode": "cold", "original_pool_size": original_size, "warm_claim_verified": False}
        finally:
            client._json("PUT", "/pool", json={**body, "size": original_size})


def run_sample(arguments: argparse.Namespace, index: int, ready: threading.Barrier | None = None) -> dict[str, Any]:
    with Zoo(api_key=arguments.key, base_url=arguments.url, timeout=CREATE_CEILING, max_retries=0) as client:
        box = None
        samples = []
        if ready is not None:
            ready.wait(timeout=30)
        started = time.monotonic()
        deadline = started + CREATE_CEILING
        try:
            box = client.create(
                kind=cast(SandboxKind, arguments.kind),
                server_id=arguments.server_id,
                wait=False,
                labels={"zoo.perf": arguments.run_id, "zoo.perf.sample": str(index)},
            )
            wait_interactive(client, box, arguments.kind, deadline)
            samples.append(observation("tti", started, True))
            client.config.timeout = arguments.tool_timeout
            samples.extend(tool_samples(box, arguments.kind, arguments.tool_timeout))
        except Exception as error:
            elapsed = time.monotonic() - started
            samples.append(
                {
                    "metric": "tti",
                    "seconds": CREATE_CEILING,
                    "observed_seconds": elapsed,
                    "success": False,
                    "error": str(error),
                }
            )
        finally:
            cleanup_error = None
            if box is not None:
                try:
                    client.request("DELETE", f"/sandboxes/{box.id}", timeout=10)
                except Exception as error:
                    cleanup_error = str(error)
        return {
            "index": index,
            "sandbox_id": box.id if box else None,
            "cleanup_error": cleanup_error,
            "samples": samples,
        }


def cleanup(arguments: argparse.Namespace) -> list[dict[str, str]]:
    errors = []
    with Zoo(api_key=arguments.key, base_url=arguments.url, timeout=10, max_retries=0) as client:
        try:
            for box in client.sandboxes({"zoo.perf": arguments.run_id}):
                try:
                    box.delete(wait=False)
                except Exception as error:
                    errors.append({"sandbox_id": box.id, "error": str(error)})
        except Exception as error:
            errors.append({"error": "scoped cleanup could not list remaining sandboxes: " + str(error)})
    return errors


def quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        for sample in run["samples"]:
            metrics[sample["metric"]].append(sample)
    return {
        name: {
            "attempts": len(samples),
            "successes": sum(sample["success"] for sample in samples),
            "success_rate": sum(sample["success"] for sample in samples) / len(samples),
            "p50_seconds": quantile([sample["seconds"] for sample in samples], 0.5),
            "p95_seconds": quantile([sample["seconds"] for sample in samples], 0.95),
            "p99_seconds": quantile([sample["seconds"] for sample in samples], 0.99),
        }
        for name, samples in metrics.items()
    }


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    validate(arguments)
    with Zoo(api_key=arguments.key, base_url=arguments.url, timeout=10, max_retries=0) as client:
        if client.sandboxes({"zoo.perf": arguments.run_id}):
            raise ValueError("run-id already labels sandboxes; use a unique ID rather than adopt or delete them")
    timestamp = datetime.now(UTC).isoformat()
    runs = []
    cleanup_errors = []
    try:
        with pool_mode(arguments) as pool:
            if arguments.pattern == "sequential":
                runs = [run_sample(arguments, index) for index in range(arguments.runs)]
            else:
                with ThreadPoolExecutor(max_workers=min(arguments.burst, arguments.runs)) as executor:
                    if arguments.pattern == "staggered":
                        origin = time.monotonic()
                        futures = []
                        for index in range(arguments.runs):
                            time.sleep(max(0, origin + index * 0.2 - time.monotonic()))
                            futures.append(executor.submit(run_sample, arguments, index))
                        runs = [future.result() for future in futures]
                    else:
                        for offset in range(0, arguments.runs, arguments.burst):
                            ready = threading.Barrier(min(arguments.burst, arguments.runs - offset))
                            futures = [
                                executor.submit(run_sample, arguments, index, ready)
                                for index in range(offset, min(offset + arguments.burst, arguments.runs))
                            ]
                            runs.extend(future.result() for future in futures)
    finally:
        cleanup_errors = cleanup(arguments)
    return {
        "started_at": timestamp,
        "finished_at": datetime.now(UTC).isoformat(),
        "url": arguments.url,
        "kind": arguments.kind,
        "server_id": arguments.server_id,
        "pattern": arguments.pattern,
        "burst": arguments.burst,
        "run_id": arguments.run_id,
        "pool": pool,
        "create_ceiling_seconds": CREATE_CEILING,
        "cleanup_errors": cleanup_errors,
        "cleanup": "deletion requested for run-labeled sandboxes, not confirmed complete",
        "measurement": "client end-to-end wall time; includes transport, polling and PNG validation, not server-only latency",
        "warnings": [
            "Timed-out creates may finish after cleanup; inspect this exact run label after quiescing the experiment.",
            "Warm claims are not attested by the current sandbox response; non-cold results are not certified warm.",
            "Staggered submission is every 200 ms; the in-flight cap can delay actual starts.",
            "Native guest cold boots have a separate 60 second target; this suite retains the specified 10 second create failure ceiling.",
        ],
        "summary": summarize(runs),
        "runs": runs,
    }


def main() -> int:
    command = parser()
    arguments = command.parse_args()
    try:
        report = run(arguments)
    except (ValueError, RuntimeError, ZooError, TimeoutError) as error:
        command.exit(2, f"perf: {error}\n")
    for metric, values in report["summary"].items():
        print(
            f"{metric}: p50={values['p50_seconds'] * 1000:.2f} ms p95={values['p95_seconds'] * 1000:.2f} ms p99={values['p99_seconds'] * 1000:.2f} ms success={values['successes']}/{values['attempts']} ({values['success_rate']:.2%})"
        )
    for warning in report["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)
    if arguments.json:
        arguments.json.write_text(json.dumps(report, indent=2) + "\n")
    return int(
        any(values["successes"] < values["attempts"] for values in report["summary"].values())
        or bool(report["cleanup_errors"])
        or any(item["cleanup_error"] for item in report["runs"])
    )


if __name__ == "__main__":
    raise SystemExit(main())
