---
title: macOS sandboxes
description: Apple Silicon VM hosts, required guest tools and reserved saved-state slot constraints.
---

## Requirements

Use an Apple Silicon host running macOS 14+, zoo-node, zoovm and a prepared macOS guest.
The guest must run zoo-guest and have the required Accessibility grant for window/input
tools. The host/guest OS pair must be compatible with the restore image and VM helper.
Keep sufficient disk/RAM for the base and the configured slots.

Zoo caps a Mac at two running macOS VMs, counting a running base. Administrators remain
responsible for applicable platform licensing.

## Prepare the host

Use the dashboard's **Add server → Mac** command with its one-time token, control-plane
URL and API-provided key. The native daemon enrolls and dials the gateway over mTLS.
`install.sh --node` can prepare the base; `--ipsw PATH` selects a restore image and
`--no-base` leaves base preparation to the administrator.

Finish any guest setup/Accessibility permission that cannot be safely automated. Verify
the guest is connected before using tools. Host/guest endpoints must resolve and trust
the deployment's API certificate; there is no configurable guest-URL alternate path.

## Saved-state contract: unique reserved slots

Saved-state reuse is restricted to a pre-reserved VM slot with its own hardware identity,
disk and saved state. The state must originate from that slot on the same compatible
Mac/account and configuration. Restore it as that slot, then hand off sandbox identity
through the required guest. Never run two allocations from the same slot simultaneously.

Do not copy one saved state into arbitrary clones with new machine identifiers, attach a
different disk to it, or treat it as portable memory for another host/account. A saved
state captures credentials and memory; handle it as sensitive data and keep the disk/state
pair consistent. Incompatible or unavailable state is an explicit failure, not permission
to use an undocumented alternate create path.

Base/slot updates must happen while allocations are drained and must capture state from
the actual prepared slot. APFS disk clones alone do not establish that a saved-memory clone
is valid. Actual slot preparation, resume/reidentification and cleanup need native runtime
validation before this path is declared accepted.

## Tools and lifecycle

Shell, files, terminals, windows, apps, accessibility and secrets use zoo-guest. The native
gateway owns the active VNC session for screenshots, input and viewing. Missing required
guest capabilities fail explicitly.

Snapshots, disk transfer and saved-state restore have different identity constraints.
Do not promise saved memory will move with a disk. Real lifecycle recovery and the boot
timing targets remain unmeasured.
