---
title: Remote servers
description: Add Linux, Mac, and Windows hosts to your Zoo installation.
---

## Supported hosts

One Zoo control plane can manage hosts running different operating systems. Available
sandbox kinds depend on the runtimes installed on each host:

| Host | Sandbox kinds | Requirements |
| --- | --- | --- |
| Linux | Linux desktop, Firefox browser, code | Linux Docker engine and zoo-node |
| macOS | macOS; Linux desktop, Firefox browser, code | Apple Silicon, macOS 14+, zoovm and a prepared base VM for macOS; a Linux Docker engine such as Docker Desktop for Linux kinds |
| Windows | Windows; Linux desktop, Firefox browser, code | Hyper-V and a prepared base VM for Windows; Docker Desktop in Linux container mode for Linux kinds |

Intel Macs can provide Linux container capacity. Native macOS sandboxes require Apple
Silicon. Linux hosts cannot run native macOS or Windows sandboxes. The dashboard advertises
Linux capacity after the node reports a working Linux Docker engine; installing zoo-node
alone does not supply that engine. Browser sandboxes use the Firefox-only Linux image.

## Enroll from the dashboard

Open **Servers → Add server**, choose the host's OS, name it, and copy the generated command.
It contains a short-lived, single-use join token, the public control-plane URL, and the
API-provided SSH key. Run it on the intended host with the required privileges.
Enrollment downloads the installer and node binary from the running API image, so their
supported platforms match that deployment.

Linux and Mac installation use `install.sh --node --token ... --control-plane ... --key ...`.
Linux runs zoo-node as a systemd service. Windows uses the generated elevated PowerShell
command. If zoo-node is already installed, use the displayed join command.

The account selected during enrollment must have access to the Docker Linux engine for
Linux workloads, and to the native VM runtime for native workloads. On Mac and Windows,
keep Docker Desktop running with the selected account's engine available over SSH.

## Connectivity and identity

zoo-node connects outbound to the gateway's mTLS endpoint on port 7443. The public node
hostname may differ from the API hostname; configure `ZOO_NODE_HOST` for that address.
The public API URL must be reachable by the enrolled host and its sandbox guests.
`localhost` on the control-plane machine is not a usable public address for remote hosts.

Docker operations use `docker system dial-stdio` over the enrolled host's pinned SSH
identity and existing node tunnel. No Docker TCP port or inbound host SSH port needs to be
published. The local SSH service and Docker CLI must remain available on the host.

Node CA material and host identity are stored in the database. Gateway peers use private
DNS membership and TLS RPC/relay listeners. Ordinary HTTP ingress does not forward node
gRPC. Update node binaries from the matching Zoo release and retain enrollment identity.

## Placement and isolation

Choose a compatible remote host when creating a sandbox, or use automatic placement.
Linux workloads use that host's Linux Docker engine and retain their home volume there.
Remote Docker homes do not automatically follow a sandbox to another machine.

Docker isolation follows `ZOO_ISOLATION`: `auto` uses Kata when the selected engine has it,
otherwise ordinary Linux containers; `kata` requires Kata and fails if it is absent;
`runc` selects ordinary containers. Docker Desktop provides a Linux VM for its engine,
but ordinary containers share that VM's kernel. Use Kata-capable Linux hosts and strict
Kata mode when a separate guest kernel per sandbox is required.

Native Mac capacity remains capped at two running macOS VMs per host, including a running
base VM. Windows uses its configured native VM cap. Linux containers do not consume those
native VM slots. See the native-platform guides for base VM setup and saved-state limits.
