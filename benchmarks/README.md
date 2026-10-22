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

## zoo vs the published numbers

One section, two runs, everything at a glance:

- `zoo code`: `results-code-20261022-202723.json` — 10 cold samples, 30 warm, fan-out ×5
- `zoo macos`: `results-macos-20261022-203202.json` — 3 cold samples, saved-state resume

Both 2026-10-22, Apple M4 / 24 GB, loopback, Docker Desktop. Cells are p50;
"—" = not published by that provider, or not measured for that kind (the macOS
bench skips files / iteration / fan-out).

| Metric (p50) | E2B | Modal | Daytona | zoo code (loopback) | zoo macos, full VM (loopback) |
|---|---|---|---|---|---|
| sandbox create (API) | ~517 ms | ~336 ms | ~71–90 ms | 83 ms | 67 ms |
| … boot to running | — | — | — | 23 ms | ~69 s |
| create + first command | ~807 ms | ~1,611 ms | ~197 ms | 125 ms | ~70 s |
| warm command | ~83 ms | ~103 ms | ~67 ms | **14 ms** | 38 ms |
| file put / get 100KB | — | — | — | 20 / 28 ms | — |
| iteration cycle | ~3,800 ms | ~12,600 ms | ~6,300 ms | 28 ms | — |
| … same cycle as 1 batch call | — | — | — | 28 ms | — |
| fan-out total | ~3,000 ms (×10) | ~10,400 ms (×10) | ~7,000 ms (×10) | 172 ms (×5) | — |
| stop + resume | ~1,100 ms | ~1,600 ms | ~23,000 ms | 658 ms (311 / 347 ms) | ~35 s (25.1 s / 9.7 s) |
| destroy | — | — | ~59 ms (cleanup) | 39 ms | 20 ms |

zoo code p95 for the same run: create 124 ms, create + first command 675 ms,
warm 40 ms, destroy 88 ms. The boot p95 (553 ms) is two cold image-extract
outliers, not the common path. zoo macos p95: boot 82.7 s, warm 48 ms.

## macOS VMs

Full-VM isolation class — not comparable to container providers (think Fly
Machines macOS / MacStadium), only interesting vs itself. Baseline vs
saved-state resume (2026-10-22, 8 GB VM):

| Metric (p50) | baseline | saved-state resume |
|---|---|---|
| sandbox create → running (cold) | 39.2 s | 68.7 s * |
| warm command | 43 ms | 38 ms |
| stop | 1.7 s | 25.1 s (pause + serialize 8 GB) |
| **start (after stop)** | **23.1 s** | **9.7 s** |
| destroy | 30 ms | 20 ms |

\* cold create never touches the suspend path — today's runs spanned 38–69 s
purely on machine load; suspend only changes stop/start.

A manual stop→start cycle on an idle VM measured 5 s / 7 s; the bench VM carried
more dirty pages. Stop latency scales with RAM — `ZOO_MACOS_MEMORY_MB=4096`
halves both save and restore time. Opt out entirely with `ZOO_MACOS_SUSPEND=0`.

Honest-reading notes: loopback removes the WAN+TLS hop remote providers'
numbers include (their one-shot HTTPS context was ~200–500 ms); the iteration
loop here is minimal (1 file write + 1 exec) vs nickaggarwal's full
lint/test/typecheck suite; fan-out is ×5 vs the published ×10. The warm-command
number (5–14 ms across today's runs) is the SSH/guest-agent round-trip floor —
the real differentiator for agent latency once deployed close to the caller.

## Batch exec (`POST /v1/sandboxes/{id}/exec/batch`)

Runs up to 25 commands in order over ONE HTTP round trip (one auth check, one
TLS handshake), `stop_on_error` halts on the first failure. On loopback it is
*not* faster than sequential calls (RTT ≈ 0; measured 28 ms vs 28 ms for a
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
- macOS kind is a different isolation class — see the macOS section above.
