---
title: macOS sandboxes
description: Apple Silicon VM hosts, required guest tools and reserved saved-state slot constraints.
---

## Requirements

Use an Apple Silicon host running macOS 14+, zoo-node, zoovm and a prepared macOS guest.
The guest must run zoo-guest. Desktop viewing and VNC input work independently of
Accessibility permission; window inspection and Accessibility tools require Apple's
guest-side grant. The host/guest OS pair must be compatible with the restore image and VM helper.
Keep sufficient disk/RAM for the base and the configured slots.

Zoo caps a Mac at two running macOS VMs, counting a running base. Administrators remain
responsible for applicable platform licensing.

## Prepare the host

Use the dashboard's **Add server → Mac** command with its one-time token, control-plane
URL and API-provided key. The native daemon enrolls and dials the gateway over mTLS.
`install.sh --node` can prepare the base; `--ipsw PATH` selects a restore image and
`--no-base` defers preparation until the first macOS sandbox or **Prepare macOS** action.

Zoo prepares the base template and guest tools automatically. **Remote Servers** shows
preparation progress and the current stage. You can create a macOS sandbox while the
host is preparing; its provisioning job waits for the template to become ready.
If preparation fails, its message explains the failed stage and **Retry preparation**
restarts preparation. An existing unprepared base can use **Prepare macOS** to continue.

The normal flow does not require opening the base screen, entering a guest password, or
running a setup script manually. **Advanced template controls** let administrators open
the prepared template to customize apps; stop it afterward to use those changes for new
sandboxes. Host/guest endpoints must resolve and trust the deployment's API certificate;
there is no configurable guest-URL alternate path.

## Existing hosts and preparation retries

Use the same Zoo node setup command to enroll a new Mac, refresh its installed helpers,
or resume interrupted preparation. The command installs the host integration, enrolls the
node before downloading/restoring macOS, and starts preparation in the background. The
Mac appears under **Remote Servers** while preparation runs; the terminal need not stay open.

For a new Mac, copy the complete one-time command from **Servers → Add server → Mac**.
For a Mac already enrolled with your deployment, run this single command under the
VM-owning account (replace the URL with your deployment):

```bash
curl -fsSL http://your-zoo-server:8000/v1/nodes/install/install.sh | \
  sudo bash -s -- --node --control-plane http://your-zoo-server:8000
```

An enrolled Mac keeps its existing registration and certificates; rerunning setup does
not create a second server or spend another join token. The installer refreshes its
restricted root-owned helper and resumes the existing node service. Base preparation
resumes automatically; there are no separate guest-account or repair commands.

`--check` performs preflight checks without installing or preparing a VM. `--no-base`
defers background preparation until a sandbox is requested. `--ipsw PATH` selects a
readable local Apple restore image when a base must be installed; an existing installed
base is reused. The native runtime stays in place by default; add `--version VERSION`
to request a verified release upgrade. An upgrade is deferred while native VMs or an
installation are active, so a running VM's runtime is preserved.

Preparation downloads and restores macOS only when the base is absent. Retrying an
installed base reuses its disk and bootstrap state; successful guest account creation is
not repeated. Preparation logs are in `~/.zoovm/install.log` on the host. Status and the
runner PID are in `~/.zoovm/preparation/zoo-macos-base/` (using the configured base name).
A stopped or interrupted runner reports a failure; retry preparation to continue. An API
restart can resume guest verification without restoring macOS again.

## Native templates and resource use

The base is a native macOS guest prepared with Zoo's guest tools. It does not run the Linux
XFCE desktop or a Docker image. Zoo caches this prepared base on the Mac and creates
independent sandbox disks using APFS copy-on-write clones. The base can remain stopped
between allocations. Each sandbox still boots its own native VM and uses its own memory;
disk cloning does not establish a measured startup time or eliminate VM resource costs.

The default template contains Apple's macOS and Zoo's guest tools; it does not add
Xcode or Homebrew. The native VM tool caches the downloaded Apple restore image in
`~/.zoovm/cache/restore/`, outside the base bundle, so deleting the base does not erase
that download. Cache writers are serialized and images are checked against Apple's
restore metadata before reuse. This cache requires the updated native zoovm release;
updating only the API container does not replace an existing host binary.

Use `--ipsw PATH` during host installation to select a specific Apple restore image.
The image must support that Mac's hardware; an older macOS version is not necessarily
compatible with a newer Mac. Third-party prepared images such as
[Cirrus Labs' vanilla templates](https://github.com/cirruslabs/macos-image-templates)
avoid developer-tool bundles, but Zoo does not currently import their OCI/Tart format.

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
