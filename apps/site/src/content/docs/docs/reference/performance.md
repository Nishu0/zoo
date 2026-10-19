---
title: Performance targets
description: METRICS.md targets and measurement protocol, not measured or accepted results.
---

:::caution
These are engineering targets, not published benchmark results. Runtime, chaos, recovery,
density and saved-state measurements are deferred. Static/build checks establish none of
the timing or reliability claims below.
:::

## Measurement protocol

TTI starts at `create()` and ends at the first successful in-sandbox call:
`execute_command("true")` for code, `screenshot()` for desktop/browser. Record warm and cold
runs separately, from a client in the same region as the deployment.

Use at least 100 runs per pattern: sequential, staggered every 200 ms, and bursts of 50.
Report p50/p95/p99 and success rate together. Failed or timed-out creates count against
success and the timing distribution at the 10-second ceiling; never drop failed samples.
Define the warm-pool depth and cold-drain procedure explicitly.

`scripts/perf.py` uses the checked-in Python SDK. It accepts `--url`, `--key`, `--kind`,
`--runs`, `--pattern sequential|staggered|burst`, `--burst` and `--json PATH`.
For Linux, `--cold` uses the existing admin `GET/PUT /v1/pool` routes to set that kind's
pool size to zero, waits for idle/booting counts to reach zero, and restores the previous
size afterward. Use a dedicated installation; another administrator changing that pool
during a measurement invalidates the experiment.

The script reports client end-to-end wall time, every attempt and failure, per-metric
percentiles and success rates, plus cleanup errors. Non-cold results are explicitly
**pool-enabled, not attested warm**: the current sandbox response does not identify a
successful warm claim. A burst of 50 also needs enough actual pool capacity; do not
describe a partly cold burst as a warm benchmark. Code skips GUI-only tool samples.
Native kinds require `--server-id`; the generic create failure ceiling remains 10 seconds,
so native cold-boot targets need their own longer-duration acceptance protocol.

Cleanup requests deletion of run-labeled sandboxes; it does not prove storage reclamation.
A timed-out create may finish later. After quiescing the experiment, inspect its exact
`zoo.perf` run label for late resources and outstanding deletion jobs.

## Fault injection

`scripts/chaos.py --runtime docker|kube` launches the performance client and injects
gateway/API restart, sandbox deletion and random guest-process termination faults.
It requires `--confirm-destructive` and an explicit Docker `--project`, or Kubernetes
`--namespace`, `--sandbox-namespace` and `--release`. It targets sandbox/guest faults only
at API sandboxes labeled with that experiment's run ID; control-plane faults affect other
clients on the explicitly selected installation. Guest termination is not a VM-pause test.

Its JSON includes fault timestamps/outcomes, performance samples and success rates,
timeouts and whether all four components were actually injected. Skipped faults or
missing coverage are not a successful chaos run. No performance or chaos experiment
has been executed as part of this source implementation.

## Startup targets

| Kind / mode | Pattern | p50 | p95 | p99 |
| --- | --- | --- | --- | --- |
| Code, warm | Sequential/staggered | ≤150 ms | ≤300 ms | ≤500 ms |
| Code, warm | Burst ×50 | ≤50 ms | ≤250 ms | ≤500 ms |
| Code, cold | Sequential | ≤1.5 s | ≤2.5 s | ≤4 s |
| Desktop/browser, warm | Sequential | ≤400 ms | ≤800 ms | ≤1.2 s |
| Desktop/browser, warm | Burst ×50 | ≤500 ms | ≤1 s | ≤2 s |
| Desktop/browser, cold | Sequential | ≤3 s | ≤5 s | ≤8 s |
| Stopped to running | Sequential | ≤1.5 s | ≤3 s | ≤5 s |
| Native VM slot | Boot/resume | ≤30 s | ≤45 s | ≤60 s |

A reserved saved-state slot is not an arbitrary clone. Publish platform/host/account,
identity constraints and cold-versus-resume mode with native results.

## Tool and interaction targets

Measure server-side round trips at 1280×720 where applicable, with response payload and
success recorded. Client timings include transport overhead and must be labeled accordingly.

| Operation | p50 | p99 |
| --- | --- | --- |
| Screenshot PNG | ≤30 ms | ≤100 ms |
| Click/mouse/key | ≤10 ms | ≤40 ms |
| Type 20 characters | ≤30 ms | ≤100 ms |
| Screen diff | ≤10 ms | ≤30 ms |
| Execute true | ≤15 ms | ≤50 ms |
| Read/write 1 MB | ≤30 ms | ≤100 ms |
| Accessibility tree | ≤150 ms | ≤500 ms |
| Browser live-view input to frame | ≤60 ms | ≤150 ms |
| Terminal key to echo | ≤30 ms | ≤100 ms |

Stable-screen waiting targets a return within 50 ms after the last change. Define how
change/stability is observed; a sleep is not the measurement.

## Reliability targets

- Warm create success ≥99.95%; cold create success ≥99.9%.
- Tool success ≥99.99%, excluding caller errors.
- Unexpected stops ≤1 per 10,000 sandbox-hours; readiness availability ≥99.95%.
- All guests/nodes reconnected after a gateway restart within 5 seconds.
- State-change delivery: p50 ≤100 ms, p99 ≤1 second.

Availability and rare-failure targets need long-duration observations, not a short sample
or a compiled fake-transport suite. Record experiment duration, faults and recovery outcomes.

## Density and experience targets

For a 32 GB / 8-core node: at least 16 idle desktops or 40 idle code sandboxes; idle desktop
memory ≤1.2 GB and idle code memory ≤400 MB including real VM overhead. Control-plane rest
memory targets ≤768 MB; gateway capacity targets 5,000 sandboxes/core and ≤4 MB per active
VNC session. Measure host used/resident memory, not just requested limits.

Experience goals: signup to first tool call ≤3 minutes, installer to dashboard ≤10 minutes,
two lines of SDK code for create/screenshot, and complete error hints plus REST/SDK/MCP
parity. These are targets, not current promises.

## Instrumentation and architectural constraints

Track sandbox-ready duration and create outcome/kind/warm labels, tool latency and outcome,
event delivery lag, gateway sessions/reconnects and screen encoding. Do not invent results
for absent counters or scripts.

The target architecture uses supported CLH/virtio-fs without DAX or VM templating. KSM does
not merge shared file-backed guest RAM, and freezing a VM does not guarantee reclamation.
VM pause needs a supported patched shim/controller; saved state uses unique reserved slots.
These corrections change the mechanism, not evidence that a numeric target was attained.

The repository's [METRICS.md](https://github.com/chann44/zoo/blob/main/METRICS.md) owns the
targets. Publish reproducible environment/version details and real measurements only after
the deferred validation is explicitly authorized.
