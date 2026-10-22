"""Zoo sandbox benchmark — mirrors the agent-sandbox industry methodology.

Replicates the metric definitions used by the public benchmark suites:
  - nickaggarwal/sandbox-test (Mar 2026): iteration loop, fan-out, pause/resume,
    filesystem I/O, concurrent exec, destroy
  - wasmerio/wasmer-sdk RESULTS-2026-09-01: separates sandbox *creation* from
    *time-to-first-command* from *warm per-command* latency; p50/p95, 30 samples,
    sequential; warm artifacts (images pre-pulled), new isolation boundary each sample
  - tensorlakeai/sandbox-sqlite-bench: disk-speed proxy via small-file I/O

Usage:
    uv run python benchmarks/bench.py --kind code            # Docker sandboxes (E2B/Daytona/Modal class)
    uv run python benchmarks/bench.py --kind macos           # macOS VMs (clone-from-template class)
    uv run python benchmarks/bench.py --fanout 10            # match the published fan-out size

Prereqs: the stack from compose.local.yml is up and `uv run python main.py` is
running; mint a key first: `uv run python benchmarks/mint_key.py`.
"""

import argparse
import concurrent.futures
import json
import pathlib
import statistics
import time

import httpx
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

KEY_FILE = pathlib.Path(__file__).resolve().parent / ".bench-key"
POLL = 0.2


def pct(samples: list[float], p: float) -> float:
    if not samples:
        return float("nan")
    s = sorted(samples)
    return s[max(0, round(p / 100 * len(s)) - 1)]


def fmt(samples: list[float]) -> str:
    if not samples:
        return "—"
    return f"{pct(samples, 50):7.0f} / {pct(samples, 95):7.0f}"


class Bench:
    def __init__(self, base: str, key: str, kind: str):
        self.c = httpx.Client(
            base_url=base, headers={"Authorization": f"Bearer {key}"}, timeout=120
        )
        self.v = "/v1"  # API prefix
        self.kind = kind
        self.made: list[str] = []

    # ---- API helpers (each returns elapsed ms) -------------------------------
    def create(self, name: str) -> tuple[str, float]:
        t = time.monotonic()
        r = self.c.post(
            self.v + "/sandboxes",
            json={"name": name, "kind": self.kind, "idle_timeout_minutes": 60},
        )
        r.raise_for_status()
        sid = r.json()["id"]
        self.made.append(sid)
        return sid, (time.monotonic() - t) * 1000

    def wait_running(self, sid: str, deadline: float = 180.0) -> tuple[str, float]:
        t = time.monotonic()
        end = t + deadline
        while time.monotonic() < end:
            st = self.c.get(f"{self.v}/sandboxes/{sid}").json()["status"]
            if st == "running":
                return st, (time.monotonic() - t) * 1000
            if st in ("failed", "error"):
                raise RuntimeError(f"sandbox {sid} reached {st}")
            time.sleep(POLL)
        raise TimeoutError(f"sandbox {sid} not running after {deadline}s")

    def exec(self, sid: str, command: str, timeout: int = 60) -> tuple[httpx.Response, float]:
        t = time.monotonic()
        r = self.c.post(f"{self.v}/sandboxes/{sid}/exec", json={"command": command, "timeout": timeout})
        return r, (time.monotonic() - t) * 1000

    def put_file(self, sid: str, path: str, body: bytes) -> float:
        t = time.monotonic()
        r = self.c.put(f"{self.v}/sandboxes/{sid}/files/content", params={"path": path}, content=body)
        r.raise_for_status()
        return (time.monotonic() - t) * 1000

    def stop(self, sid: str) -> float:
        t = time.monotonic()
        self.c.post(f"{self.v}/sandboxes/{sid}/stop").raise_for_status()
        while self.c.get(f"{self.v}/sandboxes/{sid}").json()["status"] != "stopped":
            time.sleep(POLL)
        return (time.monotonic() - t) * 1000

    def start(self, sid: str) -> float:
        t = time.monotonic()
        self.c.post(f"{self.v}/sandboxes/{sid}/start").raise_for_status()
        while self.c.get(f"{self.v}/sandboxes/{sid}").json()["status"] != "running":
            time.sleep(POLL)
        return (time.monotonic() - t) * 1000

    def delete(self, sid: str) -> float:
        t = time.monotonic()
        self.c.delete(f"{self.v}/sandboxes/{sid}").raise_for_status()
        return (time.monotonic() - t) * 1000

    def cleanup(self):
        for sid in self.made:
            try:
                self.delete(sid)
            except Exception:
                pass
        self.made.clear()


# ---- phases ------------------------------------------------------------------
def phase_cold_and_first(b: Bench, n: int) -> dict[str, list[float]]:
    """Sequential creates: API latency, boot-to-running, first command end-to-end."""
    api, boot, first = [], [], []
    for i in range(n):
        sid, ms = b.create(f"bench-cold-{i}")
        api.append(ms)
        t0 = time.monotonic()
        _, ms = b.wait_running(sid)
        boot.append(ms)
        r, ms = b.exec(sid, "echo ok")
        r.raise_for_status()
        first.append((time.monotonic() - t0) * 1000 + api[-1])
        print(f"  sample {i + 1}/{n}: create {api[-1]:.0f}ms  boot {boot[-1]:.0f}ms  e2e {first[-1]:.0f}ms")
        if b.kind == "macos":
            # Apple allows at most 2 macOS VMs per Mac: measure, then release
            b.delete(sid)
            b.made.remove(sid)
    return {"create_api": api, "boot_to_running": boot, "create_plus_first_command": first}


def phase_warm(b: Bench, sid: str, n: int) -> list[float]:
    """Warm per-command round-trip on a running sandbox."""
    out = []
    for _ in range(n):
        r, ms = b.exec(sid, "true")
        r.raise_for_status()
        out.append(ms)
    return out


def phase_files(b: Bench, sid: str) -> dict[str, list[float]]:
    """Disk-speed proxy: one 100KB upload/download plus 20 small sequential writes."""
    blob = b"zoo-bench" * 11_600  # ~104KB
    up = [b.put_file(sid, "/tmp/bench-100k.bin", blob)]
    t = time.monotonic()
    r = b.c.get(f"{b.v}/sandboxes/{sid}/files/content", params={"path": "/tmp/bench-100k.bin"})
    r.raise_for_status()
    down = [(time.monotonic() - t) * 1000]
    small = []
    for i in range(20):
        small.append(b.put_file(sid, f"/tmp/bench-small-{i}.txt", b"x" * 1024))
    return {"put_100k": up, "get_100k": down, "put_1k": small}


def phase_iteration(b: Bench, sid: str, n: int) -> list[float]:
    """Agent edit→run→verify cycles (nickaggarwal's 'iteration loop')."""
    cycles = []
    for i in range(n):
        t = time.monotonic()
        b.put_file(sid, "/tmp/bench_app.py", f"print('iter {i}')\n".encode())
        r, _ = b.exec(sid, "python3 /tmp/bench_app.py")
        r.raise_for_status()
        assert f"iter {i}" in r.json()["stdout"], r.text
        cycles.append((time.monotonic() - t) * 1000)
    return cycles


def phase_batch(b: Bench, sid: str, n: int) -> dict[str, list[float]]:
    """The same edit→run→verify cycle as one batch round trip (POST /exec/batch)."""
    cycles = []
    for i in range(n):
        t = time.monotonic()
        r = b.c.post(
            f"{b.v}/sandboxes/{sid}/exec/batch",
            json={
                "commands": [
                    {"command": f"echo \"print('iter {i}')\" > /tmp/bench_app.py"},
                    {"command": "python3 /tmp/bench_app.py"},
                    {"command": "python3 /tmp/bench_app.py"},
                ],
                "stop_on_error": True,
            },
        )
        r.raise_for_status()
        body = r.json()
        assert f"iter {i}" in body["results"][-1]["stdout"], body
        cycles.append((time.monotonic() - t) * 1000)
    return cycles


def phase_fanout(b: Bench, k: int) -> dict[str, float]:
    """Create K sandboxes concurrently, exec on each, destroy all (total pipeline)."""
    t = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=k) as pool:
        ids = list(pool.map(lambda i: b.create(f"bench-fan-{i}")[0], range(k)))
        pool.map(lambda sid: b.wait_running(sid), ids)
        created = (time.monotonic() - t) * 1000
        for _ in range(30):  # boot job bookkeeping can lag status=running by a beat (macOS: seconds)
            r, _ = b.exec(ids[0], "echo fan")
            if r.status_code != 409:
                break
            time.sleep(1.0)
        r.raise_for_status()
        return {"fanout_total": (time.monotonic() - t) * 1000, "fanout_created": created, "ids": ids}


def phase_pause(b: Bench, sid: str) -> dict[str, list[float]]:
    return {"stop": [b.stop(sid)], "start": [b.start(sid)]}


# ---- driver ------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--kind", default="code", choices=["code", "desktop", "browser", "macos"])
    ap.add_argument("--samples", type=int, default=10, help="cold-create samples (wasmer uses 30)")
    ap.add_argument("--warm", type=int, default=30, help="warm command samples")
    ap.add_argument("--fanout", type=int, default=5, help="published benchmark uses 10")
    ap.add_argument("--skip", default="", help="comma list: cold,files,iteration,fanout,pause")
    args = ap.parse_args()
    skip = set(args.skip.split(",")) - {""}

    key = KEY_FILE.read_text().strip()
    b = Bench(args.base_url, key, args.kind)
    results: dict[str, object] = {"kind": args.kind, "base_url": args.base_url, "phases": {}}
    phases = results["phases"]

    try:
        # warm sandbox for the steady-state phases
        sid, _ = b.create("bench-warm")
        st, boot_ms = b.wait_running(sid)
        print(f"warm sandbox {sid} booted in {boot_ms:.0f}ms")

        if "cold" not in skip:
            print(f"\n[1/7] cold create + first command ({args.samples} sequential)")
            phases["cold"] = phase_cold_and_first(b, args.samples)
        if "warm" not in skip:
            print(f"[2/7] warm command ×{args.warm}")
            phases["warm"] = {"command": phase_warm(b, sid, args.warm)}
        if "files" not in skip and args.kind != "macos":
            print("[3/7] filesystem I/O")
            phases["files"] = phase_files(b, sid)
        if "iteration" not in skip and args.kind != "macos":
            print("[4/7] iteration loop (6 edit→run cycles)")
            phases["iteration"] = {"cycle": phase_iteration(b, sid, 6)}
            print("[4b/7] same cycle as one batch round trip")
            phases["batch_iteration"] = {"cycle": phase_batch(b, sid, 6)}
        if "fanout" not in skip:
            print(f"[5/7] fan-out ×{args.fanout} (concurrent create → exec)")
            fan = phase_fanout(b, args.fanout)
            ids: list[str] = fan.pop("ids")  # type: ignore[assignment]
            phases["fanout"] = fan
        else:
            ids = []
        if "pause" not in skip:
            print("[6/7] stop → start (pause/resume equivalent)")
            phases["pause"] = phase_pause(b, sid)
        print("[7/7] destroy")
        phases["destroy"] = {"delete": []}
        for target in [sid] + ids:
            try:
                phases["destroy"]["delete"].append(b.delete(target))  # type: ignore[index]
            except Exception:
                pass
        b.made.clear()

        # ---- report ----------------------------------------------------------
        print(f"\n=== zoo sandbox benchmark — kind={args.kind} (p50 / p95, ms) ===")
        rows: list[tuple[str, str]] = []
        if "cold" in phases:
            c = phases["cold"]
            rows += [
                ("sandbox create (API)", fmt(c["create_api"])),
                ("… boot to running", fmt(c["boot_to_running"])),
                ("… create + first command (e2e)", fmt(c["create_plus_first_command"])),
            ]
        if "warm" in phases:
            rows.append((f"warm command ×{args.warm}", fmt(phases["warm"]["command"])))
        if "files" in phases:
            f = phases["files"]
            rows += [
                ("file put 100KB", fmt(f["put_100k"])),
                ("file get 100KB", fmt(f["get_100k"])),
                ("file put 1KB ×20 (total)", f"{sum(f['put_1k']):7.0f} /        —"),
            ]
        if "iteration" in phases:
            it = phases["iteration"]["cycle"]
            rows.append(("iteration loop (avg cycle)", f"{statistics.mean(it):7.0f} /        —"))
        if "batch_iteration" in phases:
            bt = phases["batch_iteration"]["cycle"]
            rows.append(("iteration as 1 batch call", f"{statistics.mean(bt):7.0f} /        —"))
        if "fanout" in phases:
            rows += [
                (f"fan-out ×{args.fanout} create (wall)", f"{phases['fanout']['fanout_created']:7.0f} /        —"),
                (f"fan-out ×{args.fanout} total", f"{phases['fanout']['fanout_total']:7.0f} /        —"),
            ]
        if "pause" in phases:
            rows += [("stop (pause)", fmt(phases["pause"]["stop"])), ("start (resume)", fmt(phases["pause"]["start"]))]
        if phases.get("destroy", {}).get("delete"):
            rows.append(("destroy", fmt(phases["destroy"]["delete"])))
        for name, val in rows:
            print(f"{name:36s} {val}")

        out = pathlib.Path(__file__).parent / f"results-{args.kind}-{time.strftime('%Y%m%d-%H%M%S')}.json"
        out.write_text(json.dumps(results, indent=2))
        print(f"\nsaved {out}")
    finally:
        b.cleanup()


if __name__ == "__main__":
    main()
