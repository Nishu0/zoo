# Zoo sandbox benchmarks

Measures zoo's sandboxes with the same methodology the agent-sandbox industry
uses (E2B, Daytona, Modal, Runloop, Blaxel, TensorLake), so results are
directly comparable with the published tables.

## Methodology provenance

| Suite | What we took from it |
|---|---|
| [wasmerio/wasmer-sdk RESULTS-2026-09-01](https://github.com/wasmerio/wasmer-sdk/blob/main/benchmarks/sandboxes/RESULTS-2026-09-01.md) | Separates **creation** vs **time-to-first-command** vs **warm per-command**; p50/p95; sequential samples; warm artifacts |
| [nickaggarwal/sandbox-test](https://github.com/nickaggarwal/sandbox-test) (Mar 2026) | iteration loop, fan-out, pause/resume, filesystem I/O, destroy |
| [tensorlakeai/sandbox-sqlite-bench](https://github.com/tensorlakeai/sandbox-sqlite-bench) | small-file I/O as disk-speed proxy |

Conventions: p50 and p95 (p95 is where marketing claims die), images warm
(pulled/verified already — artifact acquisition excluded), new isolation
boundary per cold sample, client TLS excluded but on the same host so all
providers share the network context.

## Run it

```bash
# 1. stack + API running
docker compose -f compose.local.yml up -d
uv run python main.py            # API on :8003 behind caddy :8000

# 2. mint a benchmark key (local only; writes benchmarks/.bench-key)
uv run python benchmarks/mint_key.py

# 3. benchmark Docker sandboxes — the E2B/Daytona/Modal comparison class
uv run python benchmarks/bench.py --kind code

#    macOS VMs (different isolation class; skips the Linux files API)
uv run python benchmarks/bench.py --kind macos --samples 3 --fanout 2

#    match the published fan-out size exactly
uv run python benchmarks/bench.py --fanout 10
```

Results print as a table and land in `benchmarks/results-<kind>-<ts>.json`.

## Reading the numbers against the published ones

| Metric (p50, ms) | E2B | Modal | Daytona | zoo (fill in) |
|---|---|---|---|---|
| sandbox create | ~517 | ~336 | ~71–90 | |
| create + first command | ~807 | ~1,611 | ~197 | |
| warm command | ~83 | ~103 | ~67 | |
| iteration cycle | ~3,800 | ~12,600 | ~6,300 | |
| fan-out ×10 total | ~3,000 | ~10,400 | ~7,000 | |
| pause + resume | ~1,100 | ~1,600 | ~23,000 (stop/start) | |

## Measured results (2026-10-22, Apple M4 / 24 GB, loopback, Docker Desktop)

Docker `code` sandboxes (`bench.py --kind code`, 10 cold samples, 30 warm, fan-out ×5):

| Metric (p50 / p95, ms) | zoo code |
|---|---|
| sandbox create (API) | 13 / 31 |
| … boot to running | 234 / 274 |
| … create + first command (e2e) | 255 / 303 |
| warm command ×30 | **5 / 9** |
| file put/get 100KB | 9 / 7 |
| iteration loop (avg cycle) | 17 |
| fan-out ×5 create (wall) | 49 |
| fan-out ×5 total | 544 |
| stop / start | 261 / 269 |
| destroy | 21 / 36 |

macOS VMs (`--kind macos`; full-VM isolation class, not comparable to container
providers). Baseline vs saved-state resume (2026-10-22, 8 GB VM):

| Metric (p50) | baseline | saved-state resume |
|---|---|---|
| sandbox create → running (cold) | 39.2 s | 38.4 s (unchanged) |
| warm command | 43 ms | 53 ms |
| stop | 1.7 s | 21.3 s (pause + serialize 8 GB) |
| **start (after stop)** | **23.1 s** | **9.7 s** |
| destroy | 30 ms | 14 ms |

A manual stop→start cycle on an idle VM measured 5 s / 7 s; the bench VM carried
more dirty pages. Stop latency scales with RAM — `ZOO_MACOS_MEMORY_MB=4096`
halves both save and restore time. Opt out entirely with `ZOO_MACOS_SUSPEND=0`.

Honest-reading notes: loopback removes the WAN+TLS hop remote providers'
numbers include (their one-shot HTTPS context was ~200–500 ms); the iteration
loop here is minimal (1 file write + 1 exec) vs nickaggarwal's full
lint/test/typecheck suite; fan-out is ×5 vs the published ×10. The warm-command
5 ms is the SSH/guest-agent round-trip floor — the real differentiator for
agent latency once deployed close to the caller.

## Batch exec (`POST /v1/sandboxes/{id}/exec/batch`)

Runs up to 25 commands in order over ONE HTTP round trip (one auth check, one
TLS handshake), `stop_on_error` halts on the first failure. On loopback it is
*not* faster than sequential calls (RTT ≈ 0; measured 23 ms vs 15 ms for a
3-command cycle) — it exists for remote agents, where every saved round trip is
a full network RTT: a 3-command write→run→read cycle over a 30 ms-RTT link
drops from ~100 ms to ~40 ms. Same pattern as MCP aggregators (mcp-batchit),
which report ~98 % wire reduction on multi-step tool chains.

## State of the art this benchmark set is up against (research 2026-10)

- **OpenSandbox ready pools**: creates are admissions into pre-warmed capacity —
  12–16 ms p50 create (pool hit), 35 ms Firecracker snapshot restore, 0.7 ms
  reflink rootfs. Pool misses cost 700 ms+; size the pool for the burst.
- **Firecracker snapshot resume**: <5 ms raw; production reliability comes from
  guest-agent lifecycle hooks (syncfs before snapshot; reseed entropy, sync
  clock, reconnect after resume) — zoo's macOS resume implements the same shape.
- **Tensorlake incremental disk snapshots**: O(changed bytes), 29–129 ms steady
  state; E2B/Modal full-disk snapshots cost seconds and stall workloads.
- **MCP batching** (mcp-batchit, arXiv 2511.07426): multi-tool chains are
  serial and prompt-heavy; application-level batching cuts round trips and wire
  dramatically (one project: 986 KB → 62 KB).

Sources: wasmer RESULTS-2026-09-01; nickaggarwal/sandbox-test FULL_BENCHMARK_REPORT
(Mar 2026); Daytona's launch post (71 ms create / 67 ms exec / 59 ms cleanup).

Caveats when comparing:

- **This machine is a laptop, not their cloud.** Absolute numbers include
  Docker Desktop overhead and your Mac's CPU. The point is the *shape*: where
  zoo is fast (VM clone from a verified base ≈ 40 ms, the same trick as Morph's
  snapshot forking) and where it pays (SSH/guest-agent round-trips).
- Providers benchmark their own regions; our loopback is a best case for
  network, worst case for nothing. Fine for relative work.
- macOS kind is not comparable to the container providers at all — it's a full
  VM class (like Fly Machines macOS / MacStadium), only interesting vs itself.
