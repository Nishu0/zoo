import contextlib
import hashlib
import ipaddress
import json
import os
import re
import shlex
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlparse, urlsplit, urlunsplit

import paramiko

from apps.server import egress, objects
from apps.server.errors import ZooError
from apps.server.guest import guest_endpoint, guest_env, hub
from apps.server.jobs import Wait
from apps.server.logger import logger
from apps.server.ssh import alive, execute, open_client, output_of, private_key
from apps.server.ssh import public_key as host_public_key
from apps.server.vnc import VNC, password_of

PREFIX = "macos:"
BASE_VM = os.environ.get("ZOO_MACOS_BASE", "zoo-macos-base")
CPUS = int(os.environ.get("ZOO_MACOS_CPUS", "4"))
MEMORY_MB = int(os.environ.get("ZOO_MACOS_MEMORY_MB", "8192"))
USER = os.environ.get("ZOO_MACOS_USER", "admin")
HOME = f"/Users/{USER}"
ENV_FILE = f"{HOME}/.zoo/env"
MAX_VMS = 2
HOST_PATH = 'export PATH="/usr/local/bin:/opt/homebrew/bin:$HOME/.local/bin:$PATH"; '
SAFE_RULE = re.compile(r"^[A-Za-z0-9.:/_-]+$")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
GUEST_BINARY = os.environ.get("ZOO_GUEST_DARWIN_BINARY", os.path.join(ROOT, "go", "guest", "dist", "zoo-guest-darwin-arm64"))
GUEST_LABEL = "com.zoo.guest"
GUEST_PLIST = f"{HOME}/Library/LaunchAgents/{GUEST_LABEL}.plist"

EGRESS_DIR = "$HOME/.zoovm/egress"
EGRESS_BINARY = "$HOME/.zoovm/bin/zoo-egress"
EGRESS_BUILD = os.path.join(ROOT, "go", "egress", "dist", "zoo-egress-darwin-arm64")

hosts: dict[str, paramiko.SSHClient] = {}
guests: dict[str, paramiko.SSHClient] = {}
roots: dict[str, paramiko.SSHClient] = {}
urls: dict[str, str] = {}
lock = threading.Lock()
# one boot at a time per Mac, so two can't both see a free slot and start a third VM
starting: dict[str, threading.Lock] = {}


def is_vm(runtime_id: str | None) -> bool:
    return bool(runtime_id) and runtime_id.startswith(PREFIX)


def vm_name(sandbox_id: str) -> str:
    return f"zoo-{sandbox_id}"


def runtime_id(server_id: str, sandbox_id: str) -> str:
    return f"{PREFIX}{server_id}:{vm_name(sandbox_id)}"


def parse(runtime_id: str) -> tuple[str, str]:
    server_id, name = runtime_id[len(PREFIX) :].split(":", 1)
    return server_id, name


def connect(server_id: str, url: str) -> paramiko.SSHClient:
    with lock:
        urls[server_id] = url
        if alive(hosts.get(server_id)):
            return hosts[server_id]
        client = open_client(server_id, url)
        hosts[server_id] = client
        return client


def url(server_id: str) -> str:
    if server_id not in urls:
        from db.connection import db_manager

        with db_manager.session() as db:
            server = db.get_server(id=server_id)
        if server is None:
            raise RuntimeError(f"server {server_id} not found")
        urls[server_id] = server.docker_url
    return urls[server_id]


def host(server_id: str) -> paramiko.SSHClient:
    return connect(server_id, url(server_id))


def forget(server_id: str):
    with lock:
        urls.pop(server_id, None)
        client = hosts.pop(server_id, None)
    if client is not None:
        client.close()


def run(server_id: str, command: str, stdin: bytes | None = None, timeout: float = 60):
    return execute(host(server_id), HOST_PATH + command, stdin, timeout)


def open_socket(server_id: str, address: tuple[str, int]):
    return host(server_id).get_transport().open_channel("direct-tcpip", address, ("127.0.0.1", 0), timeout=15)


def check(server_id: str, command: str, stdin: bytes | None = None, timeout: float = 60) -> str:
    return output_of(run(server_id, command, stdin, timeout))


def probe(server_id: str, url: str) -> dict:
    try:
        connect(server_id, url)
        check(server_id, "command -v zoovm")
        os_version = check(server_id, "sw_vers -productVersion").strip()
        if int(os_version.split(".")[0]) < 14 or check(server_id, "uname -m").strip() != "arm64":
            raise ZooError("not_supported", "macOS hosts require Apple Silicon and macOS 14 or later.")
        cpus, memory = check(server_id, "sysctl -n hw.ncpu hw.memsize").split()
        running = len(running_vms(server_id))
    except Exception as e:
        forget(server_id)
        return {"online": False, "error": str(e)[:500]}
    return {
        "online": True,
        "name": urlparse(url).hostname,
        "os": f"macOS {os_version}",
        "cpus": int(cpus),
        "memory_total": int(memory),
        "docker_version": None,
        "microvm": True,
        "containers_running": running,
    }


def running_vms(server_id: str) -> set[str]:
    return {r["name"] for r in json.loads(check(server_id, "zoovm list") or "[]") if r["state"] == "running"}


def start(sandbox_id: str, server, env: dict[str, str], admin: bool = False) -> str:
    """Clones the base VM on first boot, runs it headless with a VNC server, and waits for SSH in the guest. Waits
    in the job queue (Wait) while the Mac already runs Apple's limit of macOS VMs."""
    server_id, name = server.id, vm_name(sandbox_id)
    connect(server_id, server.docker_url)
    cloned = run(server_id, f"zoovm get {name}")[0] == 0
    if not cloned:
        ensure_base(server_id)
    rid = runtime_id(server_id, sandbox_id)
    with lock:
        mac = starting.setdefault(server_id, threading.Lock())
    with mac:
        running = running_vms(server_id)
        if name not in running and len(running) >= MAX_VMS:
            raise Wait(
                f"Mac full: it already runs {MAX_VMS} macOS VMs, the most Apple allows; this one starts when one stops"
            )
        if not cloned:
            version = base_version(server_id)
            started = time.monotonic()
            check(
                server_id,
                f"zoovm clone {BASE_VM} {name} && zoovm set {name} --cpu {CPUS} --memory {MEMORY_MB} "
                f"&& printf '%s\\n' {shlex.quote(version)} > {vm_dir(name)}/zoo-base",
            )
            logger.info(
                "macOS VM cloned",
                extra={
                    "sandbox_id": sandbox_id,
                    "base": version,
                    "clone_seconds": round(time.monotonic() - started, 2),
                },
            )
        # a retried boot adopts the VM an earlier attempt started
        if name not in running:
            boot(server_id, name)
    wait_for_guest(rid)
    set_admin(rid, admin)
    write_env(rid, env, ssh=True)
    hub.bind(rid, sandbox_id)
    install_guest(rid, sandbox_id)
    return rid


def vm_dir(name: str) -> str:
    return f"~/.zoovm/vms/{name}"


def base_of(rid: str) -> str | None:
    """The version of the base VM this sandbox was cloned from, or None for a clone older than base versions."""
    server_id, name = parse(rid)
    return run(server_id, f"cat {vm_dir(name)}/zoo-base 2>/dev/null")[1].decode(errors="replace").strip() or None


def boot(server_id: str, name: str) -> str:
    check(server_id, f"zoovm stop {name}", timeout=60)
    log = f"{vm_dir(name)}/run.log"
    check(server_id, f"nohup zoovm run {name} > {log} 2>&1 < /dev/null &")
    return wait_for_vnc(server_id, name, log)


def wait_for_vnc(server_id: str, name: str, log: str, timeout: int = 60) -> str:
    started = time.monotonic()
    while time.monotonic() < started + timeout:
        code, out, _ = run(server_id, f"zoovm vnc {name}")
        if code == 0:
            return out.decode().strip()
        if time.monotonic() > started + 5 and name not in running_vms(server_id):
            raise RuntimeError(run(server_id, f"cat {log}")[1].decode(errors="replace").strip()[-500:] or "VM exited")
        time.sleep(0.5)
    raise RuntimeError("macOS VM did not start its VNC server in time")


def guest_client(rid: str) -> paramiko.SSHClient:
    client = guests.get(rid)
    if alive(client):
        return client
    server_id, name = parse(rid)
    ip = check(server_id, f"zoovm ip {name}").strip()
    tunnel = open_socket(server_id, (ip, 22))
    client = paramiko.SSHClient()
    # The guest sits on the Mac's private NAT network, reachable only from the Mac itself
    # (directly or through its SSH connection). Each clone gets fresh host keys, so there is nothing to pin.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            ip,
            username=USER,
            sock=tunnel,
            pkey=private_key(),
            allow_agent=False,
            look_for_keys=False,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
        )
    except BaseException:
        client.close()
        tunnel.close()
        raise
    client.get_transport().set_keepalive(30)
    guests[rid] = client
    return client


def root_client(rid: str) -> paramiko.SSHClient:
    """The root session installed by guest-setup.sh for bootstrap and privileged policy changes."""
    if rid in roots and alive(roots[rid]):
        return roots[rid]
    server_id, name = parse(rid)
    ip = check(server_id, f"zoovm ip {name}").strip()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            ip,
            username="root",
            sock=open_socket(server_id, (ip, 22)),
            pkey=private_key(),
            allow_agent=False,
            look_for_keys=False,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
        )
    except paramiko.AuthenticationException:
        client.close()
        raise ZooError("guest_unavailable", "the macOS guest has no root bootstrap key", "Run the base VM setup.")
    transport = client.get_transport()
    if transport is not None:
        transport.set_keepalive(30)
    roots[rid] = client
    return client


def set_admin(rid: str, admin: bool):
    """The guest user is an administrator with passwordless sudo only in an admin sandbox; otherwise an agent can't
    undo the VM's own pf rules or app policy."""
    # macOS's SSH SACL initially permits administrators through a nested group.
    # Preserve explicit bootstrap access before removing administrator rights.
    ssh_access = (
        "set -e\ndseditgroup -o read com.apple.access_ssh >/dev/null 2>&1 || "
        "dseditgroup -o create com.apple.access_ssh\n"
        "dseditgroup -o edit -a root -t user com.apple.access_ssh\n"
        f"dseditgroup -o edit -a {shlex.quote(USER)} -t user com.apple.access_ssh\n"
    )
    if admin:
        script = (
            f"dseditgroup -o checkmember -m {USER} admin >/dev/null || dseditgroup -o edit -a {USER} -t user admin\n"
            f"echo '{USER} ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/zoo && chmod 440 /etc/sudoers.d/zoo"
        )
    else:
        script = (
            f"rm -f /etc/sudoers.d/zoo\ndseditgroup -o edit -d {USER} -t user admin 2>/dev/null || true\n"
            f"! dseditgroup -o checkmember -m {USER} admin >/dev/null"
        )
    guest_check(rid, ssh_access + script, root=True)


def wait_for_guest(rid: str, timeout: int = 300):
    deadline = time.monotonic() + timeout
    error = None
    while time.monotonic() < deadline:
        try:
            if guest(rid, "true", timeout=15, ssh=True)[0] == 0:
                return
        except Exception as e:
            error = e
            guests.pop(rid, None)
        time.sleep(2)
    raise RuntimeError(f"could not reach the macOS guest over SSH: {error}")


def guest(
    rid: str, script: str, stdin: bytes | None = None, timeout: float = 60, root: bool = False, ssh: bool = False
):
    """Runs a script in the VM as the guest user through zoo-guest. Root scripts and ssh=True go over SSH: setting
    the VM up before its guest runs, and what only root can do."""
    command = f"cd {HOME} && {script}"
    if not root and not ssh:
        return connected(rid).run(["/bin/sh", "-c", command], stdin=stdin or b"", timeout=timeout)
    if root:
        return execute(root_client(rid), command, stdin, timeout)
    return execute(guest_client(rid), command, stdin, timeout)


def connected(rid: str, service: str = "exec"):
    """This VM's zoo-guest, which every tool goes through."""
    agent = hub.for_runtime(rid)
    if agent is None or not agent.has(service):
        raise ZooError("guest_unavailable", "the sandbox's guest agent is not connected")
    return agent


def guest_check(
    rid: str, script: str, stdin: bytes | None = None, timeout: float = 60, root: bool = False, ssh: bool = False
) -> str:
    return output_of(guest(rid, script, stdin, timeout, root, ssh))


def guest_bytes(rid: str, script: str, timeout: float) -> bytes:
    code, out, err = guest(rid, script, timeout=timeout)
    if code != 0:
        raise RuntimeError(err.decode(errors="replace").strip() or f"exit code {code}")
    return out


def write_env(rid: str, env: dict[str, str], ssh: bool = False):
    """ssh=True at boot, before the guest is installed."""
    lines = "".join(f"export {k}={shlex.quote(v)}\n" for k, v in env.items() if re.fullmatch(r"[A-Za-z_]\w*", k))
    guest_check(rid, f"mkdir -p {HOME}/.zoo && umask 077 && cat > {ENV_FILE}", stdin=lines.encode(), ssh=ssh)


def native_guest_host(rid: str, hostname: str) -> str:
    """A local API lives on the VM's NAT host, rather than the VM's loopback.

    Read the actual route over authenticated SSH; neither Wi-Fi addresses nor a
    hardcoded vmnet subnet are stable across Macs. Docker publishes the ingress
    on the Mac, including this virtual interface.
    """
    try:
        loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        loopback = hostname.lower().rstrip(".") == "localhost"
    if not loopback:
        return hostname
    gateway = guest_check(
        rid, "/sbin/route -n get default | /usr/bin/awk '$1 == \"gateway:\" {print $2}'", ssh=True
    ).strip()
    try:
        address = ipaddress.IPv4Address(gateway)
        if not address.is_private or address.is_loopback or address.is_unspecified or address.is_multicast:
            raise ValueError("not a NAT gateway")
    except ValueError as error:
        raise ZooError(
            "guest_unavailable", "the macOS guest's NAT gateway is unavailable", "Check the VM's virtual network."
        ) from error
    return str(address)


def native_guest_endpoint(rid: str) -> tuple[str, int] | None:
    endpoint = guest_endpoint()
    return (native_guest_host(rid, endpoint[0]), endpoint[1]) if endpoint else None


def install_guest(rid: str, sandbox_id: str, timeout: float = 15):
    """Copies zoo-guest into the VM when it changed, writes its config, and (re)starts it as a LaunchAgent in the
    auto-logged-in user's session."""
    env = guest_env(sandbox_id, remote=True)
    if not env or not os.path.exists(GUEST_BINARY):
        raise ZooError(
            "guest_unavailable",
            "the macOS guest build or connection address is missing",
            "Build zoo-guest and configure the gateway address.",
        )
    target = urlsplit(env["ZOO_GUEST_URL"])
    hostname = native_guest_host(rid, target.hostname or "")
    if hostname != target.hostname:
        if target.scheme == "wss":
            raise ZooError("guest_unavailable", "local HTTPS needs a hostname reachable from the macOS guest")
        authority = f"{hostname}:{target.port}" if target.port else hostname
        env["ZOO_GUEST_URL"] = urlunsplit(target._replace(netloc=authority))
    with open(GUEST_BINARY, "rb") as f:
        binary = f.read()
    path = f"{HOME}/.zoo/bin/zoo-guest"
    installed = guest(rid, f"shasum -a 256 {path} 2>/dev/null", ssh=True)[1].split()[:1]
    if installed != [hashlib.sha256(binary).hexdigest().encode()]:
        guest_check(
            rid,
            f"mkdir -p {HOME}/.zoo/bin && cat > {path}.new && chmod 755 {path}.new && mv {path}.new {path}",
            stdin=binary,
            timeout=120,
            ssh=True,
        )
    # zsh starts the guest as its child rather than exec'ing it ("; exit" keeps it from being the last command), so
    # zsh is the responsible process for macOS privacy checks. The Accessibility grant on /bin/zsh survives guest
    # updates, which a grant on the ad-hoc signed guest binary would not.
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{GUEST_LABEL}</string>
<key>ProgramArguments</key><array><string>/bin/zsh</string><string>-c</string><string>{path} -env {HOME}/.zoo/guest.env; exit $?</string></array>
<key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>ProcessType</key><string>Interactive</string>
<key>StandardErrorPath</key><string>{HOME}/.zoo/guest.log</string>
</dict></plist>
"""
    guest_check(
        rid,
        f"umask 077 && cat > {HOME}/.zoo/guest.env && mkdir -p {HOME}/Library/LaunchAgents "
        f"&& cat > {GUEST_PLIST} <<'ZOO_PLIST'\n{plist}ZOO_PLIST\n",
        stdin="".join(f"{k}={v}\n" for k, v in env.items()).encode(),
        ssh=True,
    )
    # bootout and bootstrap again, not kickstart, so a changed plist takes effect; only root can from SSH
    guest_check(
        rid,
        f"uid=$(id -u {USER}); launchctl bootout gui/$uid/{GUEST_LABEL} 2>/dev/null; "
        f"for i in 1 2 3 4 5 6 7 8 9 10; do launchctl bootstrap gui/$uid {GUEST_PLIST} 2>/dev/null "
        "&& break; sleep 0.5; done",
        root=True,
    )
    deadline = time.monotonic() + timeout
    while hub.for_sandbox(sandbox_id) is None and time.monotonic() < deadline:
        time.sleep(0.25)
    if hub.for_sandbox(sandbox_id) is None:
        raise ZooError(
            "guest_unavailable", "the macOS guest did not connect", "Check the guest log and gateway address."
        )


def stop(rid: str):
    server_id, name = parse(rid)
    # the guest may already be down or unreachable; stopping the VM below covers both
    with contextlib.suppress(Exception):
        guest(rid, "shutdown -h now", timeout=10, root=True)
    for client in (guests.pop(rid, None), roots.pop(rid, None)):
        if client is not None:
            client.close()
    run(server_id, f"zoovm stop {name} --timeout 45", timeout=60)
    run(server_id, f'rm -f "{EGRESS_DIR}/{egress.file_name(name)}"')


def delete(sandbox_id: str, server):
    name = vm_name(sandbox_id)
    connect(server.id, server.docker_url)
    run(server.id, f"zoovm stop {name}", timeout=60)
    run(server.id, f'rm -f "{EGRESS_DIR}/{egress.file_name(name)}"')
    check(server.id, f"zoovm delete {name}")


SNAPSHOTS = "~/.zoovm/snapshots"


def snapshot(sandbox_id: str, snapshot_id: str, server) -> int:
    """Clones the stopped VM's bundle (disk and all) on the same Mac. APFS clones are instant and share blocks until
    either copy changes, so the size returned is what the snapshot would take on its own."""
    connect(server.id, server.docker_url)
    path = f"{SNAPSHOTS}/{shlex.quote(snapshot_id)}"
    out = check(
        server.id,
        f"mkdir -p {SNAPSHOTS} && cp -cR {vm_dir(vm_name(sandbox_id))} {path} && du -sk {path} | cut -f1",
        timeout=600,
    )
    return int(out.strip() or 0) * 1024


def restore_snapshot(sandbox_id: str, snapshot_id: str, server):
    connect(server.id, server.docker_url)
    vm, path = vm_dir(vm_name(sandbox_id)), f"{SNAPSHOTS}/{shlex.quote(snapshot_id)}"
    check(
        server.id,
        f"test -d {path} || {{ echo 'the snapshot is gone from the Mac' >&2; exit 1; }}; "
        f"rm -rf {vm}.restoring && cp -cR {path} {vm}.restoring && rm -rf {vm} && mv {vm}.restoring {vm}",
        timeout=600,
    )


def remove_snapshot(snapshot_id: str, server):
    connect(server.id, server.docker_url)
    run(server.id, f"rm -rf {SNAPSHOTS}/{shlex.quote(snapshot_id)}")


MOVE_PART_MB = 1024


def move(sandbox_id: str, source, target):
    """Moves a stopped VM to another Mac through object storage (server/objects.py): the source Mac packs its bundle
    into 1 GB parts (tar keeps the disk image sparse) and uploads them, the target downloads and unpacks them, then
    the source copy goes. The API only hands out presigned URLs."""
    name = vm_name(sandbox_id)
    connect(source.id, source.docker_url)
    connect(target.id, target.docker_url)
    if name in running_vms(source.id):
        raise RuntimeError("stop the sandbox before moving it")
    staging = f"~/.zoovm/move-{name}"
    parts = check(
        source.id,
        f"rm -rf {staging} && mkdir -p {staging} && set -o pipefail && tar -czf - -C ~/.zoovm/vms {name} "
        f"| split -b {MOVE_PART_MB}m - {staging}/part. && ls {staging}",
        timeout=3 * 3600,
    ).split()
    keys = [f"moves/{sandbox_id}/{part}" for part in parts]
    try:
        check(
            source.id,
            " && ".join(
                f"curl -fsS --retry 3 -T {staging}/{part} {shlex.quote(objects.presign('PUT', key))}"
                for part, key in zip(parts, keys, strict=True)
            ),
            timeout=6 * 3600,
        )
        downloads = " && ".join(f"curl -fsS --retry 3 {shlex.quote(objects.presign('GET', key))}" for key in keys)
        check(
            target.id,
            f"zoovm delete {name} && mkdir -p ~/.zoovm/vms && set -o pipefail "
            f"&& ({downloads}) | tar -xzf - -C ~/.zoovm/vms && zoovm get {name}",
            timeout=6 * 3600,
        )
    finally:
        run(source.id, f"rm -rf {staging}")
        for key in keys:
            with contextlib.suppress(Exception):
                objects.delete(key)
    delete(sandbox_id, source)


def is_running(rid: str) -> bool:
    server_id, name = parse(rid)
    return name in running_vms(server_id)


def vnc_channel(rid: str):
    """Connects to the VM's VNC server on 127.0.0.1 of the Mac, directly or through the host's SSH connection."""
    server_id, name = parse(rid)
    url = check(server_id, f"zoovm vnc {name}").strip()
    target = urlparse(url)
    channel = open_socket(server_id, (target.hostname, target.port))
    channel.settimeout(30)
    return channel, password_of(url)


@contextmanager
def vnc(rid: str):
    channel, password = vnc_channel(rid)
    try:
        yield VNC(channel, password)
    finally:
        channel.close()


# a pattern per profile app for `pgrep -f`; the bracket keeps pgrep from matching the shell that runs it
APP_PROCESSES = {
    "safari": "[S]afari.app/Contents/MacOS/Safari",
    "chrome": "[G]oogle Chrome.app/Contents/MacOS/Google Chrome",
    "edge": "[M]icrosoft Edge.app/Contents/MacOS/Microsoft Edge",
    "firefox": "[F]irefox.app/Contents/MacOS/firefox",
    "vscode": "[V]isual Studio Code.app/Contents/MacOS/",
}


def app_running(rid: str, app: str) -> bool:
    code, _, err = guest(rid, f"pgrep -f {shlex.quote(APP_PROCESSES[app])}")
    if code not in (0, 1):
        raise RuntimeError(err.decode(errors="replace").strip() or f"exit code {code}")
    return code == 0


def export_dir(rid: str, path: str) -> bytes:
    parent, base = os.path.split(path.rstrip("/"))
    return guest_bytes(rid, f"tar -cf - -C {shlex.quote(parent)} {shlex.quote(base)}", timeout=600)


def import_dir(rid: str, parent: str, data: bytes):
    guest_check(rid, f"mkdir -p {shlex.quote(parent)} && tar -xf - -C {shlex.quote(parent)}", stdin=data, timeout=600)


def export_home(rid: str):
    yield guest_bytes(rid, f"tar -cf - --exclude {USER}/Library/Caches -C /Users {USER}", timeout=3600)


def import_home(rid: str, data: bytes):
    guest_check(rid, "tar -xf - -C /Users", stdin=data, timeout=3600)


def apply_network(rid: str, default_action: str, allow_dns: bool, rules: list[tuple[str, str, str]]):
    """The Mac's egress daemon enforces the policy with pf on the host, out of the VM's reach; the VM's own pf rules
    stay as a second layer."""
    server_id, name = parse(rid)
    ensure_egress(server_id)
    ip = check(server_id, f"zoovm ip {name}").strip()
    endpoint = native_guest_endpoint(rid)
    data = egress.policy(rid, [ip], default_action, allow_dns, rules, egress.always(endpoint))
    live = " ".join(egress.file_name(n) for n in running_vms(server_id) | {name})
    path = egress.file_name(name)
    # drop the policies of VMs that are gone, so a VM given their address doesn't inherit one
    check(
        server_id,
        f'cd "{EGRESS_DIR}" && for f in *.json; do case " {live} " in *" $f "*) ;; *) rm -f "$f";; esac; done; '
        f"cat > {path}.tmp && mv {path}.tmp {path}",
        stdin=data,
    )
    lines = [
        "set skip on lo0",
        # Zoo reaches the guest over SSH; keep state so replies pass a default-deny policy.
        "pass in quick proto tcp to any port 22 keep state",
        *guest_rule(rid, endpoint=endpoint),
        "pass out quick proto udp to any port 67 keep state",
        f"{'pass' if allow_dns else 'block return'} out quick proto {{ tcp udp }} to any port 53",
    ]
    # names are the host's to decide; here only addresses, deny rules first as on the host
    for _, value, effect in sorted((r for r in rules if r[0] != "domain"), key=lambda r: r[2] == "allow"):
        if not SAFE_RULE.match(value):
            raise ValueError(f"invalid network rule value {value!r}")
        lines.append(f"{'pass' if effect == 'allow' else 'block return'} out quick to {value}")
    if egress.proxied(default_action, rules):
        lines.append(f"pass out quick proto tcp to any port {{ 80 443 {egress.PROXY_PORT} }} keep state")
    lines.append("block return out all" if default_action == "deny" else "pass out all")
    guest_check(
        rid,
        "cat > /etc/zoo-pf.conf && pfctl -q -f /etc/zoo-pf.conf && (pfctl -q -e 2>/dev/null || true)",
        stdin=("\n".join(lines) + "\n").encode(),
        root=True,
    )


def guest_rule(rid: str | None = None, *, endpoint: tuple[str, int] | None = None) -> list[str]:
    """Lets zoo-guest reach the API whatever the policy says."""
    endpoint = endpoint or (native_guest_endpoint(rid) if rid else guest_endpoint())
    if endpoint is None or not SAFE_RULE.match(endpoint[0]):
        return []
    return [f"pass out quick proto tcp to {endpoint[0]} port {endpoint[1]} keep state"]


def ensure_egress(server_id: str):
    """Runs the Mac's standalone egress daemon, copying this API's build over when it changed. Its pf rules
    stay loaded when it stops, so filtered VMs fail closed rather than open. The bracket in the process pattern
    keeps pkill and pgrep from matching the shell that runs them."""
    if run(server_id, "sudo -n /sbin/pfctl -s info >/dev/null")[0] != 0:
        raise RuntimeError(
            "network policy on macOS needs passwordless sudo for /sbin/pfctl on the Mac (see macos/README.md)"
        )
    with open(EGRESS_BUILD, "rb") as f:
        binary = f.read()
    digest = hashlib.sha256(binary).hexdigest()
    installed = run(server_id, f'shasum -a 256 "{EGRESS_BINARY}" 2>/dev/null')[1].split()[:1]
    if installed != [digest.encode()]:
        check(
            server_id,
            f'mkdir -p "$(dirname "{EGRESS_BINARY}")" && cat > "{EGRESS_BINARY}.new" && chmod 755 "{EGRESS_BINARY}.new" '
            f'&& mv "{EGRESS_BINARY}.new" "{EGRESS_BINARY}" && (pkill -f "zoo-egres[s].*-dir" || true)',
            stdin=binary,
            timeout=120,
        )
    check(
        server_id,
        f'mkdir -p "{EGRESS_DIR}" && chmod 700 "{EGRESS_DIR}" && (pgrep -f "zoo-egres[s].*-dir" >/dev/null || '
        f'nohup "{EGRESS_BINARY}" -dir "{EGRESS_DIR}" -log "$HOME/.zoovm/egress.log" >/dev/null 2>&1 < /dev/null &)',
    )


def apply_apps(rid: str, effects: dict[str, str]):
    lines = []
    for app, effect in effects.items():
        binaries = shlex.quote(f"/Applications/{app}.app/Contents/MacOS")
        owner, mode = (f"{USER}:admin", "755") if effect == "allow" else ("root:wheel", "700")
        lines.append(f"[ -d {binaries} ] && chown -R {owner} {binaries} && chmod -R {mode} {binaries} || true")
    if lines:
        guest_check(rid, "\n".join(lines), root=True)


INSTALL_LOG = "~/.zoovm/install.log"
SETUP_SCRIPT = os.path.join(ROOT, "macos", "guest-setup.sh")


def base_id(server_id: str) -> str:
    return f"{PREFIX}{server_id}:{BASE_VM}"


def ready_marker() -> str:
    """Marks the base VM ready to clone, and holds its version (base_version)."""
    return f"{vm_dir(BASE_VM)}/zoo-ready"


def controller_key_marker() -> str:
    return f"{vm_dir(BASE_VM)}/zoo-controller-key"


def recover_base_key(rid: str):
    """Recover an enrolled Zoo base after its control plane replaced its SSH identity."""
    server_id, name = parse(rid)
    if rid != base_id(server_id):
        raise ValueError("controller key recovery is restricted to the base VM")
    password_path = f"{vm_dir(name)}/password"
    code, encoded_password, _ = run(
        server_id,
        f"test -f {vm_dir(name)}/zoo-seeded && test ! -L {password_path} "
        f'&& test -f {password_path} && test "$(stat -f %Lp {password_path})" = 600 '
        f'&& test "$(stat -f %u {password_path})" = "$(id -u)" && cat {password_path}',
    )
    if code != 0:
        raise RuntimeError("the base has no private Zoo bootstrap password")
    password = encoded_password.decode("ascii", errors="replace").strip()
    if re.fullmatch(r"[0-9a-f]{36}", password) is None:
        raise RuntimeError("the base has no valid Zoo bootstrap password")
    old_key = run(server_id, f"cat {controller_key_marker()} 2>/dev/null")[1].decode().strip()
    if old_key:
        old_key = validate_controller_key(old_key)
    new_key = validate_controller_key(public_key())
    ip = check(server_id, f"zoovm ip {name}").strip()
    tunnel = open_socket(server_id, (ip, 22))
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            ip,
            username=USER,
            password=password,
            sock=tunnel,
            allow_agent=False,
            look_for_keys=False,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
        )
        # No password enters a command, log, guest file, or root password login.
        proof = f'test -f /var/db/zoo/done && test "$(cat /var/db/zoo/user)" = {shlex.quote(USER)}'
        verification = f'test "$(id -un)" = {shlex.quote(USER)} && sudo -n /bin/sh -c {shlex.quote(proof)}'
        output_of(execute(client, verification, None, 10))
        script = r"""set -eu
IFS= read -r new_key
IFS= read -r old_key
user=$1
test -f /var/db/zoo/done
test "$(cat /var/db/zoo/user)" = "$user"
for directory in /var/root/.ssh "/Users/$user/.ssh"; do
    test ! -L "$directory"
    mkdir -p "$directory"
    chmod 700 "$directory"
    target="$directory/authorized_keys"
    test ! -L "$target"
    temporary=$(mktemp "$directory/.authorized_keys.XXXXXX")
    trap 'rm -f "$temporary"' EXIT
    if test -f "$target"; then
        while IFS= read -r line || test -n "$line"; do
            test "$line" = "$new_key" && continue
            if test -n "$old_key" && test "$line" = "$old_key"; then continue; fi
            printf '%s\n' "$line"
        done < "$target" > "$temporary"
    fi
    printf '%s\n' "$new_key" >> "$temporary"
    chmod 600 "$temporary"
    case "$directory" in
        /var/root/*) chown root:wheel "$directory" "$temporary" ;;
        *) chown "$user:staff" "$directory" "$temporary" ;;
    esac
    mv -f "$temporary" "$target"
    trap - EXIT
done
"""
        command = f"sudo -n /bin/sh -c {shlex.quote(script)} zoo-key-recovery {shlex.quote(USER)}"
        output_of(execute(client, command, f"{new_key}\n{old_key}\n".encode(), 15))
    finally:
        client.close()
        tunnel.close()
        for cache in (guests, roots):
            stale = cache.pop(rid, None)
            if stale is not None:
                stale.close()


def validate_controller_key(key: str) -> str:
    from apps.server.ssh import validate_host_key

    return validate_host_key(key)


def mark_base(server_id: str, *, verified: bool = False) -> str:
    """Gives the base VM a new version, `<macOS version>-<build>-<UTC minute>`: when it's first found ready, and each
    time it's stopped after a change, so sandboxes cloned before and after differ."""
    os_version, build = "macos", "unknown"
    code, out, _ = run(server_id, f"zoovm version {BASE_VM}")
    if code == 0:
        with contextlib.suppress(ValueError):
            info = json.loads(out)
            os_version, build = info.get("os") or os_version, info.get("build") or build
    version = f"{os_version}-{build}-{time.strftime('%Y%m%d%H%M', time.gmtime())}"
    if verified:
        check(server_id, f"printf '%s\\n' {shlex.quote(public_key())} > {controller_key_marker()}")
    check(server_id, f"printf '%s\\n' {shlex.quote(version)} > {ready_marker()}")
    return version


def base_version(server_id: str) -> str:
    """The base VM's current version; a base marked ready before versions gets one now."""
    version = run(server_id, f"cat {ready_marker()} 2>/dev/null")[1].decode(errors="replace").strip()
    return version or mark_base(server_id)


def desktop_ready(server_id: str) -> bool:
    user = shlex.quote(USER)
    command = (
        f'uid=$(id -u {user}); test "$(stat -f %Su /dev/console)" = {user} '
        '&& pgrep -u "$uid" -x Finder >/dev/null && pgrep -u "$uid" -x Dock >/dev/null '
        '&& ! pgrep -u "$uid" -x "Setup Assistant|SetupAssistant" >/dev/null'
    )
    return guest(base_id(server_id), command, timeout=5, ssh=True)[0] == 0


def base_ready(server_id: str) -> bool:
    if (
        run(
            server_id,
            f"test -f {ready_marker()} && test -f {controller_key_marker()} "
            f'&& test "$(cat {controller_key_marker()})" = {shlex.quote(public_key())}',
        )[0]
        == 0
    ):
        return True
    if BASE_VM not in running_vms(server_id):
        return False
    if run(server_id, f"nc -z -G 2 $(zoovm ip {BASE_VM}) 22", timeout=10)[0] != 0:
        return False
    try:
        try:
            ok = desktop_ready(server_id)
        except paramiko.AuthenticationException:
            recover_base_key(base_id(server_id))
            ok = desktop_ready(server_id)
        if ok:
            sandbox_id = f"base-{server_id}"
            hub.bind(base_id(server_id), sandbox_id)
            install_guest(base_id(server_id), sandbox_id)
            ok = connected(base_id(server_id), "exec").run(["true"], timeout=5)[0] == 0
    except Exception:
        stale = guests.pop(base_id(server_id), None)
        if stale is not None:
            stale.close()
        return False
    if ok:
        mark_base(server_id, verified=True)
    return ok


PREPARE_HELPER = "/usr/local/libexec/zoo-macos/prepare-base.sh"
PREPARATION = f'"$HOME"/.zoovm/preparation/{shlex.quote(BASE_VM)}'
_preparation_locks: dict[str, threading.Lock] = {}


@contextmanager
def preparation_guard(server_id: str):
    """Keep the Mac's verification lock until this SSH channel closes, across API/worker processes."""
    transport = host(server_id).get_transport()
    if transport is None:
        raise RuntimeError("The Mac disconnected during preparation")
    channel = transport.open_session(timeout=10)
    try:
        channel.settimeout(10)
        channel.exec_command(
            f"mkdir -p {PREPARATION} && /usr/bin/lockf -k -t 0 {PREPARATION}/verify.guard "
            "/bin/sh -c 'printf \"locked\\n\"; cat >/dev/null'"
        )
        yield channel.makefile("rb").readline() == b"locked\n"
    finally:
        channel.close()


def preparation_status(server_id: str) -> tuple[str, str] | None:
    code, output, _ = run(
        server_id,
        f"test -f {PREPARATION}/state || exit 1; phase=$(cat {PREPARATION}/state); "
        f'case "$phase" in ready|verifying|failed) ;; *) '
        f"pid=$(cat {PREPARATION}/pid 2>/dev/null); "
        'case "$pid" in ""|*[!0-9]*) phase=interrupted ;; *) kill -0 "$pid" 2>/dev/null || phase=interrupted ;; esac ;; esac; '
        f'if [ "$phase" = verifying ]; then modified=$(stat -f %m {PREPARATION}/state 2>/dev/null || true); '
        'case "$modified" in ""|*[!0-9]*) ;; *) [ $(($(date +%s) - modified)) -le 900 ] || phase=verification_failed ;; esac; fi; '
        'if [ "$phase" = interrupted ]; then printf "failed\\nMac preparation stopped; retry preparation under Servers\\n"; '
        'elif [ "$phase" = verification_failed ]; then printf "failed\\nGuest verification timed out; retry preparation under Servers and check the Mac install log\\n"; '
        f'else printf "%s\\n" "$phase"; cat {PREPARATION}/error 2>/dev/null || true; fi',
    )
    if code != 0:
        return None
    lines = output.decode(errors="replace").splitlines()
    return (lines[0], "\n".join(lines[1:])[-500:]) if lines else None


def ensure_base(server_id: str):
    """Agent creates wait for one shared preparation instead of entering Setup Assistant."""
    status = base_status(server_id)
    if status.get("ready"):
        return
    if status["state"] == "failed":
        raise RuntimeError(status.get("message") or "macOS preparation failed; retry preparation under Servers")
    if status["state"] != "installing":
        base_install(server_id)
    stage = status.get("message") or "Preparing the macOS base and guest tools"
    raise Wait(f"{stage}; the sandbox will start automatically", window=3600)


def base_status(server_id: str) -> dict:
    """State of the server's base VM: missing, installing, failed, stopped or running."""
    preparation = preparation_status(server_id)
    if preparation is not None:
        phase, error = preparation
        if phase == "failed":
            return {
                "state": "failed",
                "message": error or "macOS preparation failed; retry preparation",
                "ready": False,
            }
        if phase not in ("ready", "verifying"):
            return {
                "state": "installing",
                "message": {
                    "downloading": "Downloading macOS (once per host)",
                    "installing": "Installing macOS",
                    "configuring": "Creating the guest account and enabling remote access",
                    "booting": "Starting the prepared guest",
                }.get(phase, "Preparing macOS"),
                "ready": False,
            }
        if phase == "verifying":
            with lock:
                finishing = _preparation_locks.setdefault(server_id, threading.Lock())
            if finishing.acquire(blocking=False):
                try:
                    with preparation_guard(server_id) as acquired:
                        # A different API/worker may have completed while this request waited for SSH.
                        current = preparation_status(server_id) if acquired else None
                        if current and current[0] == "verifying" and base_ready(server_id):
                            if not desktop_ready(server_id):
                                return {
                                    "state": "installing",
                                    "message": "Waiting for the guest desktop to finish setup",
                                    "ready": False,
                                }
                            base_stop(server_id)
                            check(server_id, f"printf 'ready\\n' > {PREPARATION}/state")
                            return {"state": "stopped", "ready": True, "message": "Guest ready for agent sandboxes"}
                finally:
                    finishing.release()
            return {"state": "installing", "message": "Verifying guest connection and commands", "ready": False}
    installing = run(server_id, f"pgrep -f '[z]oovm install {BASE_VM}'")[0] == 0
    log = run(server_id, f"tail -c 4000 {INSTALL_LOG} 2>/dev/null")[1].decode(errors="replace")
    last = re.split(r"[\r\n]+", log.strip())[-1] if log.strip() else ""
    if installing:
        found = re.findall(r"(\d+(?:\.\d+)?)%", last)
        phase = "installing macOS" if last.startswith("installing") else "downloading macOS"
        return {"state": "installing", "progress": float(found[-1]) if found else None, "message": phase}
    if run(server_id, f"zoovm get {BASE_VM}")[0] != 0:
        failed = "zoovm:" in log
        return {
            "state": "failed" if failed else "missing",
            "progress": None,
            "message": last[-300:] if failed else None,
        }
    state = "running" if BASE_VM in running_vms(server_id) else "stopped"
    return {"state": state, "progress": None, "message": None, "ready": base_ready(server_id)}


def base_install(server_id: str):
    if not vnc_safe_name(BASE_VM) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", USER):
        raise ZooError("bad_request", "invalid macOS base VM or guest account name")
    if run(server_id, f"test -x {PREPARE_HELPER}")[0] != 0:
        raise ZooError(
            "unavailable",
            "The Mac needs the automatic preparation helper",
            "Run the Mac host setup command once to install it.",
        )
    key = public_key()
    check(
        server_id,
        f"mkdir -p ~/.zoovm && printf '%s\\n' {shlex.quote(key)} | "
        f"nohup {PREPARE_HELPER} --name {shlex.quote(BASE_VM)} --guest-user {shlex.quote(USER)} "
        f">> {INSTALL_LOG} 2>&1 &",
    )


def vnc_safe_name(name: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", name) is not None


def base_start(server_id: str) -> str:
    return boot(server_id, BASE_VM)


def base_stop(server_id: str):
    stop(base_id(server_id))
    if BASE_VM in running_vms(server_id):
        raise RuntimeError("The base VM is still stopping; retry preparation after it stops")
    if run(server_id, f"test -f {ready_marker()}")[0] == 0:
        mark_base(server_id)


def public_key() -> str:
    return host_public_key()


def base_setup(server_id: str):
    """Opens Terminal in the base VM and types the guest setup script, which then asks for the password."""
    with open(SETUP_SCRIPT) as f:
        # Drop the shebang: zsh would try to history-expand its "!" while it's typed.
        script = "".join(line for line in f if not line.startswith("#!"))
    command = (
        f"cat > /tmp/zoo-setup.sh <<'ZOO_SETUP'\n{script}ZOO_SETUP\nsh /tmp/zoo-setup.sh {shlex.quote(public_key())}\n"
    )
    with vnc(base_id(server_id)) as v:
        v.combo(["cmd", "space"])
        time.sleep(1)
        v.type("Terminal", 0.03)
        time.sleep(0.5)
        v.combo(["Return"])
        time.sleep(3)
        v.type(command, 0.015)
