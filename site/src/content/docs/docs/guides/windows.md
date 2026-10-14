---
title: Windows sandboxes
description: Hyper-V hosts, required guest tools and complete saved-state slot identity.
---

## Requirements

Use a Hyper-V-capable Windows edition and enabled hardware virtualization, zoo-node and
a prepared Windows base with zoo-guest. Run the generated host setup in an elevated
PowerShell under the account that owns the VMs. Supply your own appropriately licensed
installation media; edition availability is not a licensing grant.

The dashboard's **Add server → Windows** command carries the API URL, one-time token and
API-provided key. `-Iso` can select local or downloaded installation media and `-Edition`
selects the installed edition. Allow the required reboot/setup completion before allocating
sandboxes. Configure a usable VM network/switch on the host.

## Required connectivity

The native daemon dials the gateway's mTLS node endpoint on 7443. VM guests must also reach
the derived API/guest connection endpoint. DNS and certificate trust must work inside the
VM, not just in the administrator's browser.

All shell/files/terminal/window/app/accessibility operations use the required guest. Screen
pixels/input use the gateway's VNC session. A missing guest returns an explicit error;
there is no alternative guest management channel for tool execution.

## Saved-state contract: complete, uniquely reserved VM slots

Prepare each saved-state slot as its own Hyper-V VM with a unique identity and matching
configuration, disks and saved state. Resume that slot for one allocation at a time and
perform the guest identity handoff before treating it as ready.

Copying `vmrs`/`vmgs` files next to a new VM or different differencing disk is not a valid
saved-state clone recipe. Hardware/configuration state, disk ancestry and VM identity must
remain consistent under the platform's supported operations. Do not attach arbitrary disks
to a saved state or reuse one captured VM identity concurrently.

Base/slot changes require drained allocations and freshly prepared slot state. Treat
saved memory as sensitive. Disk snapshots or transfers do not automatically carry portable
saved memory. Real slot restore, reidentification and recovery validation is deferred.

## Capacity and data

`ZOO_WINDOWS_MAX_VMS` limits concurrent VMs per host. The API uses the host's actual
capacity and allocated slots; waiting for capacity does not change the identity rules.

Persistent user data, template ancestry, snapshots and a database backup must be recovered
with their matching dependencies. The VM boot targets are goals, not timings established
by the current implementation.
