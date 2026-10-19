"""Windows sandboxes: Hyper-V VMs on Windows servers, managed over SSH to the host with windows/zoovm.ps1.

The API uploads the helper scripts in windows/ to ~\\.zoovm on the host. Each guest runs TightVNC (screen, mouse and
keyboard, tunnelled through the host's SSH connection) and zoo-guest in the desktop session, which serves every other
tool. The guest has no SSH server: the host gives each VM its guest identity and zoo-guest updates through Hyper-V's
Guest Service Interface (zoovm push), and zoo-guest dials the public API's guest endpoint.
"""

import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import threading
import time

import paramiko
from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes

from server import egress, objects
from server.errors import ZooError
from server.guest import GuestError, guest_endpoint, guest_env, hub
from server.logger import logger
from server.ssh import alive, execute, open_client, output_of

PREFIX = "windows:"
BASE_VM = os.environ.get("ZOO_WINDOWS_BASE", "zoo-windows-base")
CPUS = int(os.environ.get("ZOO_WINDOWS_CPUS", "4"))
MEMORY_MB = int(os.environ.get("ZOO_WINDOWS_MEMORY_MB", "8192"))
MAX_VMS = int(os.environ.get("ZOO_WINDOWS_MAX_VMS", "4"))
USER = "zoo"
HOME = rf"C:\Users\{USER}"
ENV_DIR = rf"{HOME}\.zoo"
ENV_FILE = rf"{ENV_DIR}\env.ps1"
VNC_PORT = 5900
ROOT = os.path.dirname(os.path.dirname(__file__))
HELPER_DIR = os.path.join(ROOT, "windows")
GUEST_BINARY = os.environ.get(
    "ZOO_GUEST_WINDOWS_BINARY", os.path.join(ROOT, "guest", "dist", "zoo-guest-windows-amd64.exe")
)
EGRESS_BUILD = os.path.join(ROOT, "egress", "dist", "zoo-egress-windows-amd64.exe")
# zoo-guest in the VM: setup.ps1 installs it in the base VM, the host copies each VM its guest.env (zoovm push)
GUEST_DIR = r"C:\ProgramData\zoo"
GUEST_EXE = rf"{GUEST_DIR}\bin\zoo-guest.exe"
GUEST_ENV = rf"{GUEST_DIR}\guest.env"
# the VM build on the host, which base installs copy in and updates push from (relative to the user's profile)
HOST_VM_GUEST = r".zoovm\bin\zoo-guest-vm.exe"
HELPERS = ["zoovm.ps1", "setup.ps1"]
SAFE_RULE = re.compile(r"^[A-Za-z0-9.:/_-]+$")
# TightVNC stores its password DES-encrypted with this fixed key.
TIGHTVNC_KEY = bytes.fromhex("e84ad660c4721ae0")

# Reads a length-prefixed script from stdin and runs it; the rest of stdin stays readable through $ZooIn.
# Scripts travel over stdin because cmd.exe, which runs SSH commands on Windows, caps command lines at 8191 chars.
BOOTSTRAP = (
    "$i=[Console]::OpenStandardInput();$h=New-Object byte[] 8;$n=0;"
    "while($n -lt 8){$r=$i.Read($h,$n,8-$n);if($r -le 0){exit 2};$n+=$r};"
    "$l=[int][Text.Encoding]::ASCII.GetString($h);$s=New-Object byte[] $l;$n=0;"
    "while($n -lt $l){$r=$i.Read($s,$n,$l-$n);if($r -le 0){exit 2};$n+=$r};"
    "$ZooIn=$i;. ([ScriptBlock]::Create([Text.Encoding]::UTF8.GetString($s)))"
)
POWERSHELL_ARGV = [
    "powershell.exe",
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy",
    "Bypass",
    "-EncodedCommand",
    base64.b64encode(BOOTSTRAP.encode("utf-16-le")).decode(),
]
POWERSHELL = " ".join(POWERSHELL_ARGV)
PREAMBLE = (
    "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
    "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)\n"
    "function Read-ZooInput { $m=New-Object IO.MemoryStream; $ZooIn.CopyTo($m); ,$m.ToArray() }\n"
)

hosts: dict[str, paramiko.SSHClient] = {}
homes: dict[str, str] = {}
urls: dict[str, str] = {}
uploaded: set[str] = set()
vnc_set: set[str] = set()
lock = threading.Lock()


def q(value: str) -> str:
    """Quotes a value as a PowerShell single-quoted string."""
    return "'" + re.sub("['\u2018\u2019\u201a\u201b]", lambda m: m.group() * 2, str(value)) + "'"


def is_vm(runtime_id: str | None) -> bool:
    return bool(runtime_id) and runtime_id.startswith(PREFIX)


def vm_name(sandbox_id: str) -> str:
    return f"zoo-{sandbox_id}"


def runtime_id(server_id: str, sandbox_id: str) -> str:
    return f"{PREFIX}{server_id}:{vm_name(sandbox_id)}"


def parse(runtime_id: str) -> tuple[str, str]:
    server_id, name = runtime_id[len(PREFIX) :].split(":", 1)
    return server_id, name


def payload(script: str, data: bytes = b"", cwd: str | None = None) -> bytes:
    body = PREAMBLE
    if cwd:
        body += f"Set-Location -LiteralPath {q(cwd)}\n"
    body += "try {\n" + script + "\n} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }\n"
    encoded = body.encode()
    return f"{len(encoded):08d}".encode() + encoded + data


def connect(server_id: str, url: str) -> paramiko.SSHClient:
    with lock:
        urls[server_id] = url
        if alive(hosts.get(server_id)):
            return hosts[server_id]
        client = open_client(server_id, url)
        hosts[server_id] = client
        uploaded.discard(server_id)
    upload_helpers(server_id)
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
        uploaded.discard(server_id)
        client = hosts.pop(server_id, None)
    if client is not None:
        client.close()


def run(server_id: str, script: str, data: bytes = b"", timeout: float = 60):
    return execute(host(server_id), POWERSHELL, payload(script, data), timeout)


def check(server_id: str, script: str, data: bytes = b"", timeout: float = 60) -> str:
    return output_of(run(server_id, script, data, timeout))


def write_host_file(server_id: str, name: str, data: bytes):
    check(
        server_id,
        "$d = Join-Path $env:USERPROFILE '.zoovm'; New-Item -ItemType Directory -Force -Path $d | Out-Null\n"
        f"[IO.File]::WriteAllBytes((Join-Path $d {q(name)}), (Read-ZooInput))",
        data,
    )


def helper_files() -> dict[str, bytes]:
    files = {}
    for name in HELPERS:
        with open(os.path.join(HELPER_DIR, name), "rb") as f:
            files[name] = f.read()
    return files


def helpers_version(files: dict[str, bytes]) -> str:
    """This API's helper version: a hash over every helper, the same one a release's windows/*.ps1 give."""
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(f"{name} {hashlib.sha256(files[name]).hexdigest()}\n".encode())
    return digest.hexdigest()[:16]


# Prints `name hash` for each helper on the host, so only the ones that differ are uploaded.
HELPER_HASHES = r"""
$d = Join-Path $env:USERPROFILE '.zoovm'
foreach ($n in @(__NAMES__)) {
    $f = Join-Path $d $n
    if (Test-Path -LiteralPath $f) { "$n $((Get-FileHash -Algorithm SHA256 -LiteralPath $f).Hash)" }
}
"""


def host_helper_hashes(server_id: str) -> dict[str, str]:
    out = check(server_id, HELPER_HASHES.replace("__NAMES__", ps_list(HELPERS)))
    return {n: h.lower() for n, _, h in (line.strip().partition(" ") for line in out.splitlines()) if h}


def upload_helpers(server_id: str):
    """Puts this API's version of windows/*.ps1 on the host once per connection: uploads the ones whose hash differs,
    checks every hash afterwards, and records the version in ~\\.zoovm\\helpers.json."""
    if server_id in uploaded:
        return
    files = helper_files()
    wanted = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    on_host = host_helper_hashes(server_id)
    for name in [n for n, h in wanted.items() if on_host.get(n) != h]:
        write_host_file(server_id, name, files[name])
    found = host_helper_hashes(server_id)
    wrong = sorted(n for n, h in wanted.items() if found.get(n) != h)
    if wrong:
        raise RuntimeError(f"the helper scripts on the host don't match this API's after upload: {', '.join(wrong)}")
    write_host_file(
        server_id, "helpers.json", json.dumps({"version": helpers_version(files), "files": wanted}).encode()
    )
    uploaded.add(server_id)


def zoovm(server_id: str, *args, timeout: float = 120):
    host(server_id)
    command = " ".join(a if a.startswith("-") else q(a) for a in map(str, args))
    return run(
        server_id, f"& (Join-Path $env:USERPROFILE '.zoovm\\zoovm.ps1') {command}\nexit $LASTEXITCODE", timeout=timeout
    )


def zoovm_check(server_id: str, *args, timeout: float = 120) -> str:
    return output_of(zoovm(server_id, *args, timeout=timeout))


def probe(server_id: str, url: str) -> dict:
    try:
        connect(server_id, url)
        info = json.loads(
            check(
                server_id,
                "$os = Get-CimInstance Win32_OperatingSystem\n"
                "$cs = Get-CimInstance Win32_ComputerSystem\n"
                "$hyperv = [bool](Get-Command Get-VM -ErrorAction SilentlyContinue)\n"
                "if ($hyperv) { Get-VM | Out-Null }\n"
                'ConvertTo-Json -Compress @{ name = $env:COMPUTERNAME; os = "$($os.Caption) $($os.BuildNumber)";'
                " cpus = $cs.NumberOfLogicalProcessors; memory = $cs.TotalPhysicalMemory; hyperv = $hyperv }",
            )
        )
        running = len(running_vms(server_id)) if info["hyperv"] else 0
    except Exception as e:
        forget(server_id)
        return {"online": False, "error": str(e)[:500]}
    return {
        "online": True,
        "name": info["name"],
        "os": info["os"].replace("Microsoft ", ""),
        "cpus": int(info["cpus"]),
        "memory_total": int(info["memory"]),
        "docker_version": None,
        "microvm": info["hyperv"],
        "containers_running": running,
    }


def running_vms(server_id: str) -> set[str]:
    return {r["name"] for r in json.loads(zoovm_check(server_id, "list") or "[]") if r["state"] == "running"}


def exists(server_id: str, name: str) -> bool:
    return zoovm(server_id, "get", name)[0] == 0


def start(sandbox_id: str, server, env: dict[str, str]) -> str:
    """Clones the base VM's latest template on first boot, starts it, gives it its guest identity through Hyper-V,
    and waits until its zoo-guest connects."""
    require_guest_url()
    server_id, name = server.id, vm_name(sandbox_id)
    connect(server_id, server.docker_url)
    rid = runtime_id(server_id, sandbox_id)
    created = exists(server_id, name)
    if not created and zoovm(server_id, "sealed", BASE_VM)[0] != 0:
        raise RuntimeError(
            "the base VM isn't ready yet: finish its setup and stop it under Remote Servers, then start again"
        )
    if len(running_vms(server_id) - {name}) >= MAX_VMS:
        raise RuntimeError(f"this server already runs {MAX_VMS} Windows VMs (ZOO_WINDOWS_MAX_VMS)")
    if not created:
        started = time.monotonic()
        zoovm_check(server_id, "clone", BASE_VM, name, "-Cpu", str(CPUS), "-Memory", str(MEMORY_MB))
        logger.info(
            "Windows VM cloned",
            extra={
                "sandbox_id": sandbox_id,
                "base": base_of(rid),
                "clone_seconds": round(time.monotonic() - started, 2),
            },
        )
    if name not in running_vms(server_id):
        # an earlier boot's port ACLs may name the host's address on a switch that has since changed; enforce()
        # puts the current ones back once the guest answers
        check(server_id, CLEAR_ACLS.replace("__VM__", q(name)))
        zoovm_check(server_id, "start", name)
    hub.bind(rid, sandbox_id)
    push_identity(server_id, name, sandbox_id)
    wait_for_guest(sandbox_id)
    update_guest(rid, sandbox_id)
    set_vnc_password(rid)
    write_env(rid, env)
    return rid


def base_of(rid: str) -> str | None:
    """The version of the template this sandbox was cloned from, or None for a clone older than template versions."""
    server_id, name = parse(rid)
    return zoovm(server_id, "base", name)[1].decode(errors="replace").strip() or None


def guest_ip(rid: str) -> str:
    server_id, name = parse(rid)
    return zoovm_check(server_id, "ip", name).strip()


def tunnel(rid: str, port: int, address: str | None = None):
    server_id, _ = parse(rid)
    target = (address or guest_ip(rid), port)
    return host(server_id).get_transport().open_channel("direct-tcpip", target, ("127.0.0.1", 0), timeout=15)


def require_guest_url():
    guest_endpoint()


def connected(rid: str, service: str = "exec"):
    """This VM's zoo-guest, which every tool goes through. The guest has no other way in."""
    agent = hub.for_runtime(rid)
    if agent is None or not agent.has(service):
        raise ZooError(
            "guest_unavailable",
            "the sandbox's guest agent is not connected",
            f"restart the sandbox (its log is {GUEST_DIR}\\guest.log in the VM)",
        )
    return agent


def guest(rid: str, script: str, data: bytes = b"", timeout: float = 60):
    """Runs a PowerShell script in the guest through zoo-guest, in the desktop session with the user's elevated token."""
    return connected(rid).run(POWERSHELL_ARGV, stdin=payload(script, data, cwd=HOME), timeout=timeout)


def guest_check(rid: str, script: str, data: bytes = b"", timeout: float = 60) -> str:
    return output_of(guest(rid, script, data, timeout))


def guest_json(rid: str, script: str, timeout: float = 60):
    return json.loads(guest_check(rid, script, timeout=timeout))


def guest_raw(rid: str, argv: list[str], stdin: bytes | None = None, timeout: float = 60):
    """Runs a program in the guest without PowerShell, for tools like tar that stream binary data."""
    return connected(rid).run(argv, stdin=stdin or b"", timeout=timeout)


def desktop(rid: str, script: str, timeout: float = 30) -> str:
    """Runs a PowerShell script in the desktop session, where zoo-guest runs, so the windows it starts show up."""
    return guest_check(rid, script, timeout=timeout).strip()


def desktop_json(rid: str, script: str, timeout: float = 30):
    return json.loads(desktop(rid, script, timeout) or "null")


def window(rid: str, action: str, window_id: str = "", cmd: int = 0):
    """Lists (action "list") or acts on top-level windows: "show" with a ShowWindow cmd, "focus" or "close"."""
    result, _ = connected(rid, "windows").call(
        "window", {"action": action, "id": str(window_id), "cmd": cmd}, timeout=30
    )
    return result.get("windows")


def host_home(server_id: str) -> str:
    if server_id not in homes:
        homes[server_id] = check(server_id, "$env:USERPROFILE").strip()
    return homes[server_id]


def upload_vm_guest(server_id: str) -> str:
    """Puts this API's zoo-guest build for VMs on the host when it differs, and returns its path there. It's kept
    apart from the egress daemon's copy, which is running and locked."""
    if not os.path.exists(GUEST_BINARY):
        raise RuntimeError(f"zoo-guest for Windows isn't built: {GUEST_BINARY} (make guest-windows)")
    with open(GUEST_BINARY, "rb") as f:
        binary = f.read()
    installed = check(
        server_id,
        f"$exe = Join-Path $env:USERPROFILE {q(HOST_VM_GUEST)}\n"
        "if (Test-Path -LiteralPath $exe) { (Get-FileHash -Algorithm SHA256 -LiteralPath $exe).Hash }",
    )
    if installed.strip().lower() != hashlib.sha256(binary).hexdigest():
        write_host_file(server_id, HOST_VM_GUEST.removeprefix(".zoovm\\"), binary)
    return f"{host_home(server_id)}\\{HOST_VM_GUEST}"


def push(server_id: str, name: str, source: str, destination: str):
    zoovm_check(server_id, "push", name, "-Source", source, "-Destination", destination, timeout=300)


def push_identity(server_id: str, name: str, sandbox_id: str, timeout: float = 600):
    """Copies the VM its guest.env (the API's address and this sandbox's token) through Hyper-V's Guest Service
    Interface. It works once Windows in the VM has started its integration services, so it's retried until then."""
    env = "".join(f"{k}={v}\n" for k, v in guest_env(sandbox_id, remote=True).items()).encode()
    file = f"env-{name}.env"
    write_host_file(server_id, file, env)
    source = f"{host_home(server_id)}\\.zoovm\\{file}"
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                push(server_id, name, source, GUEST_ENV)
                return
            except RuntimeError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(3)
    finally:
        run(server_id, f"Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath {q(source)}")


def wait_for_guest(sandbox_id: str, timeout: float = 600):
    deadline = time.monotonic() + timeout
    while hub.for_sandbox(sandbox_id) is None:
        if time.monotonic() > deadline:
            raise RuntimeError(
                "the Windows guest agent didn't connect: check that the VM reaches ZOO_API_URL and read "
                f"{GUEST_DIR}\\guest.log in the VM. A base VM built before zoo-guest replaced SSH has no guest: "
                "reinstall it"
            )
        time.sleep(0.5)


def update_guest(rid: str, sandbox_id: str, timeout: float = 60):
    """Moves the VM's zoo-guest to this API's build when it differs: the host copies the new build next to the
    running one through Hyper-V, and the guest swaps it in and restarts on it (its update op)."""
    with open(GUEST_BINARY, "rb") as f:
        wanted = hashlib.sha256(f.read()).hexdigest()
    installed = guest_check(
        rid, f"(Get-FileHash -Algorithm SHA256 -LiteralPath {q(GUEST_EXE)}).Hash", timeout=30
    ).strip()
    if installed.lower() == wanted:
        return
    server_id, name = parse(rid)
    push(server_id, name, upload_vm_guest(server_id), GUEST_EXE + ".new")
    result, _ = connected(rid, "update").call("update", {}, timeout=30)
    replacement_pid = int(result["pid"])
    deadline = time.monotonic() + timeout
    while True:
        try:
            reported_pid = guest_check(
                rid, '(Get-CimInstance Win32_Process -Filter "ProcessId = $PID").ParentProcessId', timeout=10
            ).strip()
            if reported_pid == str(replacement_pid):
                return
        except (GuestError, ZooError):
            pass
        if time.monotonic() > deadline:
            raise RuntimeError("the Windows guest agent didn't come back after its update")
        time.sleep(0.25)


def vnc_password(rid: str) -> str:
    digest = hmac.new(os.environ.get("JWT_SECRET", "").encode(), rid.encode(), hashlib.sha256).digest()
    return base64.b32encode(digest).decode().lower()[:8]


def set_vnc_password(rid: str):
    """Gives the guest's TightVNC server this VM's own password; clones start with the base VM's."""
    encryptor = Cipher(TripleDES(TIGHTVNC_KEY * 3), modes.ECB()).encryptor()
    stored = encryptor.update(vnc_password(rid).encode().ljust(8, b"\0")) + encryptor.finalize()
    guest_check(
        rid,
        "$k = 'HKLM:\\SOFTWARE\\TightVNC\\Server'\n"
        f"Set-ItemProperty -Path $k -Name Password -Type Binary -Value ([byte[]]({','.join(str(b) for b in stored)}))\n"
        "Set-ItemProperty -Path $k -Name UseVncAuthentication -Type DWord -Value 1\n"
        "Restart-Service tvnserver",
    )
    vnc_set.add(rid)


def write_env(rid: str, env: dict[str, str]):
    lines = "".join(f"$env:{k} = {q(v)}\n" for k, v in env.items() if re.fullmatch(r"[A-Za-z_]\w*", k))
    guest_check(
        rid,
        f"New-Item -ItemType Directory -Force -Path {q(ENV_DIR)} | Out-Null\n"
        f"[IO.File]::WriteAllBytes({q(ENV_FILE)}, (Read-ZooInput))",
        lines.encode(),
    )


def close_guest(rid: str):
    vnc_set.discard(rid)


def stop(rid: str):
    server_id, name = parse(rid)
    close_guest(rid)
    zoovm_check(server_id, "stop", name, "-Timeout", "45", timeout=120)
    forget_policy(server_id, name)


def delete(sandbox_id: str, server):
    connect(server.id, server.docker_url)
    zoovm_check(server.id, "delete", vm_name(sandbox_id), timeout=180)
    forget_policy(server.id, vm_name(sandbox_id))


def forget_policy(server_id: str, name: str):
    run(
        server_id,
        f"Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path {EGRESS_DIR} {q(egress.file_name(name))})",
    )


SNAPSHOTS_DIR = "(Join-Path $env:USERPROFILE '.zoovm\\snapshots')"


def snapshot_path(snapshot_id: str) -> str:
    return f"(Join-Path {SNAPSHOTS_DIR} {q(snapshot_id + '.vhdx')})"


def disk_path(sandbox_id: str) -> str:
    return f"(Join-Path {VMS_DIR} {q(vm_name(sandbox_id) + '\\disk.vhdx')})"


def snapshot(sandbox_id: str, snapshot_id: str, server) -> int:
    """Copies the stopped VM's differencing disk on the same host; returns its size. Its parent template stays in
    use by the sandbox itself, so the copy stays valid for as long as the sandbox exists."""
    connect(server.id, server.docker_url)
    out = check(
        server.id,
        f"New-Item -ItemType Directory -Force -Path {SNAPSHOTS_DIR} | Out-Null\n"
        f"Copy-Item -LiteralPath {disk_path(sandbox_id)} -Destination {snapshot_path(snapshot_id)}\n"
        f"(Get-Item -LiteralPath {snapshot_path(snapshot_id)}).Length",
        timeout=1800,
    )
    return int(out.strip() or 0)


def restore_snapshot(sandbox_id: str, snapshot_id: str, server):
    connect(server.id, server.docker_url)
    check(
        server.id,
        f"if (-not (Test-Path -LiteralPath {snapshot_path(snapshot_id)})) {{ throw 'the snapshot is gone from the host' }}\n"
        f"Copy-Item -LiteralPath {snapshot_path(snapshot_id)} -Destination {disk_path(sandbox_id)} -Force",
        timeout=1800,
    )


def remove_snapshot(snapshot_id: str, server):
    connect(server.id, server.docker_url)
    run(server.id, f"Remove-Item -Force -ErrorAction SilentlyContinue {snapshot_path(snapshot_id)}")


MOVE_PART_BYTES = 1 << 30
VMS_DIR = "(Join-Path $env:USERPROFILE '.zoovm\\vms')"
TEMPLATES_DIR = "(Join-Path $env:USERPROFILE '.zoovm\\templates')"

# On the source host: uploads a file in parts of __PART__ bytes, one to each presigned URL.
UPLOAD_PARTS = r"""
$urls = @(__URLS__)
$tmp = Join-Path $env:TEMP ('zoo-move-' + [guid]::NewGuid() + '.part')
$in = [IO.File]::OpenRead(__FILE__)
$buffer = New-Object byte[] (8MB)
try {
    foreach ($url in $urls) {
        $out = [IO.File]::Create($tmp)
        try {
            $left = [int64]__PART__
            while ($left -gt 0) {
                $n = $in.Read($buffer, 0, [int][Math]::Min($buffer.Length, $left))
                if ($n -le 0) { break }
                $out.Write($buffer, 0, $n)
                $left -= $n
            }
        } finally { $out.Dispose() }
        & curl.exe -fsS --retry 3 -T $tmp $url
        if ($LASTEXITCODE -ne 0) { throw "upload failed (curl exit $LASTEXITCODE)" }
    }
} finally {
    $in.Dispose()
    Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $tmp
}
"""

# On the target host: downloads the parts and joins them; the file only appears once it's complete.
DOWNLOAD_PARTS = r"""
$file = __FILE__
$urls = @(__URLS__)
New-Item -ItemType Directory -Force -Path (Split-Path $file) | Out-Null
$tmp = Join-Path $env:TEMP ('zoo-move-' + [guid]::NewGuid() + '.part')
$out = [IO.File]::Create("$file.partial")
try {
    foreach ($url in $urls) {
        & curl.exe -fsS --retry 3 -o $tmp $url
        if ($LASTEXITCODE -ne 0) { throw "download failed (curl exit $LASTEXITCODE)" }
        $in = [IO.File]::OpenRead($tmp)
        try { $in.CopyTo($out) } finally { $in.Dispose() }
    }
    $out.Dispose()
    Move-Item -Force -LiteralPath "$file.partial" -Destination $file
} finally {
    $out.Dispose()
    Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $tmp, "$file.partial"
}
"""

# On the target host: the moved-in templates it already has, from an earlier move off the same server.
HAS_TEMPLATES = r"""
foreach ($n in @(__NAMES__)) { if (Test-Path -LiteralPath (Join-Path __DIR__ $n)) { $n } }
"""


def moved_name(source_id: str, template: str) -> str:
    """A template's name on the server it moves to: kept apart from that server's own base VM templates."""
    return template if template.startswith("moved-") else f"moved-{source_id[:8]}-{template}"


def transfer(sandbox_id: str, index: int, source_id: str, path: str, size: int, target_id: str, dest: str):
    """Copies one file from the source host to dest (a PowerShell path) on the target through object storage."""
    parts = max(1, -(-size // MOVE_PART_BYTES))
    keys = [f"moves/{sandbox_id}/{index}.{p:04d}" for p in range(parts)]
    upload = (
        UPLOAD_PARTS.replace("__URLS__", ps_list([objects.presign("PUT", k) for k in keys]))
        .replace("__FILE__", q(path))
        .replace("__PART__", str(MOVE_PART_BYTES))
    )
    try:
        check(source_id, upload, timeout=6 * 3600)
        download = DOWNLOAD_PARTS.replace("__URLS__", ps_list([objects.presign("GET", k) for k in keys])).replace(
            "__FILE__", dest
        )
        check(target_id, download, timeout=6 * 3600)
    finally:
        for key in keys:
            with contextlib.suppress(Exception):
                objects.delete(key)


def move(sandbox_id: str, source, target):
    """Moves a stopped VM to another Windows server through object storage (server/objects.py). Its disk is a
    differencing disk, so the templates under it go too, unless the target has them from an earlier move; the target
    keeps them apart from its own base VM's (`moved-` names) and relinks the chain. The API only hands out
    presigned URLs."""
    name = vm_name(sandbox_id)
    connect(source.id, source.docker_url)
    connect(target.id, target.docker_url)
    if name in running_vms(source.id):
        raise RuntimeError("stop the sandbox before moving it")
    chain = json.loads(zoovm_check(source.id, "chain", name))
    chain = chain if isinstance(chain, list) else [chain]
    disk, templates = chain[0], chain[1:]
    names = [moved_name(source.id, t["name"]) for t in templates]
    base = base_of(runtime_id(source.id, sandbox_id))
    try:
        zoovm_check(target.id, "delete", name, timeout=180)
        have = set(
            check(
                target.id, HAS_TEMPLATES.replace("__NAMES__", ps_list(names)).replace("__DIR__", TEMPLATES_DIR)
            ).split()
        )
        files = [(disk, f"(Join-Path {VMS_DIR} {q(name + '\\disk.vhdx')})")] + [
            (t, f"(Join-Path {TEMPLATES_DIR} {q(n)})") for t, n in zip(templates, names, strict=True) if n not in have
        ]
        for index, (item, dest) in enumerate(files):
            transfer(sandbox_id, index, source.id, item["path"], int(item["size"]), target.id, dest)
        if base:
            check(target.id, f"Set-Content -Path (Join-Path {VMS_DIR} {q(name + '\\zoo-base')}) -Value {q(base)}")
        zoovm_check(target.id, "adopt", name, "-Parents", ",".join(names), "-Cpu", str(CPUS), "-Memory", str(MEMORY_MB))
    except Exception:
        with contextlib.suppress(Exception):
            zoovm(target.id, "delete", name, timeout=180)
        raise
    delete(sandbox_id, source)


def is_running(rid: str) -> bool:
    server_id, name = parse(rid)
    return name in running_vms(server_id)


def windows_path(path: str) -> str:
    if '"' in path:
        raise ValueError("paths can't contain double quotes")
    return path.replace("/", "\\").rstrip("\\")


def tar_output(rid: str, argv: list[str], timeout: float) -> bytes:
    code, out, err = guest_raw(rid, argv, timeout=timeout)
    # bsdtar exits 1 when it skipped files Windows keeps locked, like the registry hive; the archive is still good.
    if code != 0 and not (code == 1 and out):
        raise RuntimeError(err.decode(errors="replace").strip() or f"exit code {code}")
    return out


# each profile app's process name, as Get-Process takes it
APP_PROCESSES = {"chrome": "chrome", "edge": "msedge", "firefox": "firefox", "vscode": "Code"}


def app_running(rid: str, app: str) -> bool:
    script = (
        f"if (Get-Process -Name {q(APP_PROCESSES[app])} -ErrorAction SilentlyContinue) "
        "{ 'running' } else { 'stopped' }"
    )
    return guest_check(rid, script).strip() == "running"


def export_dir(rid: str, path: str) -> bytes:
    parent, base = os.path.split(windows_path(path).replace("\\", "/"))
    return tar_output(rid, ["tar.exe", "-cf", "-", "-C", windows_path(parent), base], 600)


def import_dir(rid: str, parent: str, data: bytes):
    parent = windows_path(parent)
    guest_check(rid, f"New-Item -ItemType Directory -Force -Path {q(parent)} | Out-Null")
    output_of(guest_raw(rid, ["tar.exe", "-xf", "-", "-C", parent], data, 600))


def export_home(rid: str):
    excludes = [f"--exclude={USER}/{p}" for p in ["AppData/Local", "NTUSER.DAT*", "ntuser.dat*", "ntuser.ini"]]
    yield tar_output(rid, ["tar.exe", "-cf", "-", *excludes, "-C", "C:\\Users", USER], 3600)


def import_home(rid: str, data: bytes):
    output_of(guest_raw(rid, ["tar.exe", "-xf", "-", "-C", "C:\\Users"], data, 3600))


EGRESS_DIR = "(Join-Path $env:USERPROFILE '.zoovm\\egress')"
EGRESS_TASK = "zoo-egress"

CLEAR_ACLS = "Get-VMNetworkAdapterExtendedAcl -VMName __VM__ | Remove-VMNetworkAdapterExtendedAcl"

# On the host: writes the VM's policy for the egress daemon and puts Hyper-V port ACLs on the VM's adapter. A
# filtered VM may only reach the daemon's proxy, the API and DHCP (and DNS when allowed), plus ip rules that allow,
# and answer the host's SSH and VNC; everything else it sends is dropped at the switch, outside the VM.
# Prints the host's address on the VM's switch, where the proxy listens.
HOST_POLICY = r"""
$vm = __VM__
$dir = __DIR__
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$keep = @(@((Get-VM | Where-Object State -eq 'Running').Name) + $vm | ForEach-Object { "$_.json" })
Get-ChildItem -Path $dir -Filter *.json | Where-Object { $keep -notcontains $_.Name } | Remove-Item -Force
$tmp = Join-Path $dir "$vm.json.tmp"
[IO.File]::WriteAllBytes($tmp, (Read-ZooInput))
Move-Item -Force -Path $tmp -Destination (Join-Path $dir "$vm.json")
$switch = (Get-VMNetworkAdapter -VMName $vm | Select-Object -First 1).SwitchName
$hostIp = (Get-NetIPAddress -InterfaceAlias "vEthernet ($switch)" -AddressFamily IPv4 | Select-Object -First 1).IPAddress
if (-not $hostIp) { throw "no host address on switch '$switch'" }
Get-VMNetworkAdapterExtendedAcl -VMName $vm | Remove-VMNetworkAdapterExtendedAcl
$script:weight = @{ Deny = 1000; Allow = 10; System = 2000 }
function Acl($kind, $extra) {
    $action = $(if ($kind -eq 'Deny') { 'Deny' } else { 'Allow' })
    $script:weight[$kind] += 1
    Add-VMNetworkAdapterExtendedAcl -VMName $vm -Direction Outbound -Action $action -Weight $script:weight[$kind] @extra |
        Out-Null
}
if (__FILTERED__) {
    Add-VMNetworkAdapterExtendedAcl -VMName $vm -Direction Outbound -Action Deny -Weight 1 | Out-Null
    Acl System @{ Protocol = 'UDP'; RemotePort = '67' }
    Acl System @{ RemoteIPAddress = $hostIp; Protocol = 'TCP'; RemotePort = '__PROXY_PORT__' }
    Acl System @{ RemoteIPAddress = $hostIp; Protocol = 'TCP'; LocalPort = '22' }
    Acl System @{ RemoteIPAddress = $hostIp; Protocol = 'TCP'; LocalPort = '__VNC_PORT__' }
    if (__DNS__) {
        Acl System @{ RemoteIPAddress = $hostIp; Protocol = 'UDP'; RemotePort = '53' }
        Acl System @{ RemoteIPAddress = $hostIp; Protocol = 'TCP'; RemotePort = '53' }
    }
    foreach ($e in @(__ALWAYS__)) {
        $ip, $port = $e.Split('|')
        Acl System @{ RemoteIPAddress = $ip; Protocol = 'TCP'; RemotePort = $port }
    }
    foreach ($c in @(__ALLOW__)) { Acl Allow @{ RemoteIPAddress = $c } }
}
foreach ($c in @(__DENY__)) { Acl Deny @{ RemoteIPAddress = $c } }
$hostIp
"""

ENSURE_EGRESS = r"""
$bin = Join-Path $env:USERPROFILE '.zoovm\bin'
$exe = Join-Path $bin 'zoo-egress.exe'
$dir = __DIR__
New-Item -ItemType Directory -Force -Path $bin, $dir | Out-Null
$data = Read-ZooInput
if ($data.Length -gt 0) {
    Stop-ScheduledTask -TaskName __TASK__ -ErrorAction SilentlyContinue
    Get-Process -Name zoo-egress -ErrorAction SilentlyContinue | Where-Object Path -eq $exe | Stop-Process -Force
    [IO.File]::WriteAllBytes($exe, $data)
}
if (-not (Get-NetFirewallRule -DisplayName 'zoo egress' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName 'zoo egress' -Direction Inbound -Action Allow -Protocol TCP `
        -LocalPort __PROXY_PORT__ -Program $exe | Out-Null
}
if (-not (Get-ScheduledTask -TaskName __TASK__ -ErrorAction SilentlyContinue)) {
    $action = New-ScheduledTaskAction -Execute $exe `
        -Argument ('-dir "{0}" -log "{1}"' -f $dir, (Join-Path $env:USERPROFILE '.zoovm\egress.log'))
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName __TASK__ -Action $action -Trigger $trigger -Principal $principal `
        -Settings $settings -Force | Out-Null
}
if ((Get-ScheduledTask -TaskName __TASK__).State -ne 'Running') { Start-ScheduledTask -TaskName __TASK__ }
"""

# In the guest: points WinINet, WinHTTP and command-line tools at the host's proxy, the only way out of a filtered VM.
GUEST_PROXY = r"""
$proxy = __PROXY__
$inet = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
if ($proxy) {
    Set-ItemProperty -Path $inet -Name ProxyEnable -Value 1 -Type DWord
    Set-ItemProperty -Path $inet -Name ProxyServer -Value $proxy
    Set-ItemProperty -Path $inet -Name ProxyOverride -Value '<local>'
    netsh winhttp set proxy proxy-server="$proxy" bypass-list="<local>" | Out-Null
    [Environment]::SetEnvironmentVariable('HTTP_PROXY', "http://$proxy", 'Machine')
    [Environment]::SetEnvironmentVariable('HTTPS_PROXY', "http://$proxy", 'Machine')
    [Environment]::SetEnvironmentVariable('NO_PROXY', 'localhost,127.0.0.1', 'Machine')
} else {
    Set-ItemProperty -Path $inet -Name ProxyEnable -Value 0 -Type DWord
    netsh winhttp reset proxy | Out-Null
    foreach ($n in 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY') { [Environment]::SetEnvironmentVariable($n, $null, 'Machine') }
}
"""


def ps_list(values: list[str]) -> str:
    """The inside of @(...): empty stays empty, where $null would be one item."""
    return ", ".join(q(v) for v in values)


def ensure_egress(server_id: str):
    """Runs the host's standalone egress daemon as a scheduled task, copying this API's build over when it
    changed."""
    with open(EGRESS_BUILD, "rb") as f:
        binary = f.read()
    installed = check(
        server_id,
        "$exe = Join-Path $env:USERPROFILE '.zoovm\\bin\\zoo-egress.exe'\n"
        "if (Test-Path -LiteralPath $exe) { (Get-FileHash -Algorithm SHA256 -LiteralPath $exe).Hash }",
    )
    if installed.strip().lower() == hashlib.sha256(binary).hexdigest():
        binary = b""
    script = (
        ENSURE_EGRESS.replace("__DIR__", EGRESS_DIR)
        .replace("__TASK__", q(EGRESS_TASK))
        .replace("__PROXY_PORT__", str(egress.PROXY_PORT))
    )
    check(server_id, script, binary, timeout=120)


def apply_network(rid: str, default_action: str, allow_dns: bool, rules: list[tuple[str, str, str]]):
    """Enforced on the host: the egress daemon's proxy decides names, and Hyper-V port ACLs keep a filtered VM from
    going around it. Inside the VM, the proxy settings and Windows Firewall are a second layer."""
    for _, value, _ in rules:
        if not SAFE_RULE.match(value):
            raise ValueError(f"invalid network rule value {value!r}")
    server_id, name = parse(rid)
    ensure_egress(server_id)
    filtered = egress.proxied(default_action, rules)
    endpoints = egress.always(guest_endpoint())
    data = egress.policy(rid, [guest_ip(rid)], default_action, allow_dns, rules, endpoints)
    addresses = [(t, v, e) for t, v, e in rules if t != "domain"]
    script = (
        HOST_POLICY.replace("__VM__", q(name))
        .replace("__DIR__", EGRESS_DIR)
        .replace("__FILTERED__", "$true" if filtered else "$false")
        .replace("__DNS__", "$true" if allow_dns else "$false")
        .replace("__PROXY_PORT__", str(egress.PROXY_PORT))
        .replace("__VNC_PORT__", str(VNC_PORT))
        .replace("__ALWAYS__", ps_list([f"{e['ip']}|{int(e['port'])}" for e in endpoints]))
        .replace("__ALLOW__", ps_list([v for _, v, e in addresses if e == "allow"]))
        .replace("__DENY__", ps_list([v for _, v, e in addresses if e != "allow"]))
    )
    host_ip = check(server_id, script, data).strip().splitlines()[-1]
    proxy = f"{host_ip}:{egress.PROXY_PORT}" if filtered else ""
    lines = [
        GUEST_PROXY.replace("__PROXY__", q(proxy)),
        "Remove-NetFirewallRule -Group 'zoo-policy' -ErrorAction SilentlyContinue",
        (
            "Set-NetFirewallProfile -All -Enabled True "
            f"-DefaultOutboundAction {'Block' if default_action == 'deny' or filtered else 'Allow'}"
        ),
        "function Rule($name, $action, $address, $protocol, $port) {",
        "  $a = @{ Group = 'zoo-policy'; DisplayName = \"zoo $name\"; Direction = 'Outbound'; Action = $action }",
        "  if ($address) { $a.RemoteAddress = $address }",
        "  if ($protocol) { $a.Protocol = $protocol; $a.RemotePort = $port }",
        "  New-NetFirewallRule @a | Out-Null",
        "}",
        "Rule dhcp Allow $null UDP 67",
        f"Rule dns-udp {'Allow' if allow_dns else 'Block'} $null UDP 53",
        f"Rule dns-tcp {'Allow' if allow_dns else 'Block'} $null TCP 53",
        *guest_rule(),
    ]
    if filtered:
        lines.append(f"Rule proxy Allow {q(host_ip)} TCP {egress.PROXY_PORT}")
    # names are the host proxy's to decide; here only addresses. A block rule wins over an allow rule.
    for _, value, effect in addresses:
        lines.append(f"Rule {q(value)} {'Allow' if effect == 'allow' else 'Block'} {q(value)}")
    guest_check(rid, "\n".join(lines))


def guest_rule() -> list[str]:
    """Lets zoo-guest reach the API whatever the policy says. A block rule for the same address still wins."""
    endpoint = guest_endpoint()
    if endpoint is None or not SAFE_RULE.match(endpoint[0]):
        return []
    host, port = endpoint
    return [
        f"$ips = @(try {{ [Net.Dns]::GetHostAddresses({q(host)}) | ForEach-Object IPAddressToString }} catch {{}})",
        f"if ($ips) {{ Rule zoo-guest Allow $ips TCP {port} }}",
    ]


# Store (packaged) apps start through their package, not an .exe Image File Execution Options can catch, so they're
# denied with AppLocker packaged-app rules. Enforcing them needs an edition with AppLocker (Enterprise or Education).
APPLOCKER = r"""
$rules = foreach ($family in @(__DENY__)) {
    $pkg = Get-AppxPackage | Where-Object PackageFamilyName -eq $family | Select-Object -First 1
    if (-not $pkg) { continue }
    Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*\WindowsApps\$($pkg.PackageFullName)\*" } |
        Stop-Process -Force
    ('<FilePublisherRule Id="{0}" Name="zoo deny {1}" Description="" UserOrGroupSid="S-1-1-0" Action="Deny">' +
        '<Conditions><FilePublisherCondition PublisherName="{2}" ProductName="{1}" BinaryName="*">' +
        '<BinaryVersionRange LowSection="0.0.0.0" HighSection="*" /></FilePublisherCondition></Conditions>' +
        '</FilePublisherRule>') -f [guid]::NewGuid(), [Security.SecurityElement]::Escape($pkg.Name),
        [Security.SecurityElement]::Escape($pkg.Publisher)
}
if ($rules) {
    # enforcing a collection blocks whatever it doesn't allow, so every other packaged app is allowed first
    $collection = '<RuleCollection Type="Appx" EnforcementMode="Enabled">' +
        '<FilePublisherRule Id="a9e18c21-ff8f-43cf-b9fc-db40eed693ba" Name="All signed packaged apps" ' +
        'Description="" UserOrGroupSid="S-1-1-0" Action="Allow"><Conditions>' +
        '<FilePublisherCondition PublisherName="*" ProductName="*" BinaryName="*">' +
        '<BinaryVersionRange LowSection="0.0.0.0" HighSection="*" /></FilePublisherCondition></Conditions>' +
        '</FilePublisherRule>' + ($rules -join '') + '</RuleCollection>'
} else {
    $collection = '<RuleCollection Type="Appx" EnforcementMode="NotConfigured" />'
}
$path = Join-Path $env:TEMP 'zoo-applocker.xml'
Set-Content -Path $path -Value ('<AppLockerPolicy Version="1">' + $collection + '</AppLockerPolicy>') -Encoding UTF8
Set-AppLockerPolicy -XmlPolicy $path
if ($rules) {
    sc.exe config AppIDSvc start= auto | Out-Null
    Start-Service AppIDSvc
}
"""
PACKAGED = re.compile(r"^([\w.-]+_[a-z0-9]+)![\w.-]+$", re.IGNORECASE)


def apply_apps(rid: str, effects: dict[str, str]):
    """Blocks an app's .exe from starting with an Image File Execution Options debugger that doesn't exist, and a
    Store app with an AppLocker rule for its package."""
    lines = ["$ifeo = 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options'"]
    packaged = {}
    for binary, effect in effects.items():
        if match := PACKAGED.match(binary):
            packaged[match.group(1)] = effect
            continue
        if not re.fullmatch(r"[\w .+-]+\.exe", binary, re.IGNORECASE):
            continue
        key = f"(Join-Path $ifeo {q(binary)})"
        if effect == "allow":
            lines.append(f"Remove-ItemProperty -Path {key} -Name Debugger -ErrorAction SilentlyContinue")
        else:
            lines.append(f"New-Item -Path {key} -Force | Out-Null")
            lines.append(f"Set-ItemProperty -Path {key} -Name Debugger -Value 'C:\\zoo\\blocked-by-policy.exe'")
    if packaged:
        lines.append(APPLOCKER.replace("__DENY__", ps_list([f for f, e in packaged.items() if e != "allow"])))
    if len(lines) > 1:
        guest_check(rid, "\n".join(lines))


INSTALL_LOG = "install.log"
PHASES = ("downloading Windows", "preparing disk", "using ", "applying Windows image", "first boot", "installed")


def base_id(server_id: str) -> str:
    return f"{PREFIX}{server_id}:{BASE_VM}"


def ready_marker() -> str:
    return f"(Join-Path $env:USERPROFILE '.zoovm\\vms\\{BASE_VM}\\zoo-ready')"


def base_sandbox_id(server_id: str) -> str:
    """The base VM's guest identity: it isn't a sandbox, but its guest connects the same way."""
    return f"windows-base-{server_id}"


def connect_base(server_id: str) -> bool:
    """Whether the running base VM's guest is connected; when it isn't, copies it its identity (once, no waiting),
    so it connects shortly after Windows finishes starting."""
    rid, sandbox_id = base_id(server_id), base_sandbox_id(server_id)
    hub.bind(rid, sandbox_id)
    if hub.for_sandbox(sandbox_id) is not None:
        return True
    with contextlib.suppress(Exception):
        push_identity(server_id, BASE_VM, sandbox_id, timeout=0)
    return False


def base_ready(server_id: str, running: bool) -> bool:
    if run(server_id, f"if (-not (Test-Path {ready_marker()})) {{ exit 1 }}")[0] == 0:
        return True
    if not running or not connect_base(server_id):
        return False
    try:
        ok = guest(base_id(server_id), "if (-not (Test-Path C:\\zoo\\ready)) { exit 1 }", timeout=10)[0] == 0
    except Exception:
        return False
    if ok:
        check(server_id, f"Set-Content -Path {ready_marker()} -Value ready")
    return ok


def base_status(server_id: str) -> dict:
    """State of the server's base VM: missing, installing, failed, stopped or running."""
    installing = zoovm(server_id, "installing", BASE_VM)[0] == 0
    log = run(
        server_id,
        f"$f = Join-Path $env:USERPROFILE '.zoovm\\{INSTALL_LOG}'\n"
        "if (Test-Path $f) { $t = [IO.File]::ReadAllText($f); $t.Substring([Math]::Max(0, $t.Length - 4000)) }",
    )[1].decode(errors="replace")
    lines = [line.strip() for line in re.split(r"[\r\n]+", log) if line.strip()]
    last = lines[-1] if lines else ""
    if installing:
        phase = next((line for line in reversed(lines) if line.startswith(PHASES)), "starting")
        found = re.findall(r"(\d+(?:\.\d+)?)%", last)
        return {"state": "installing", "progress": float(found[-1]) if found else None, "message": phase}
    if not exists(server_id, BASE_VM):
        failed = "zoovm:" in log
        return {
            "state": "failed" if failed else "missing",
            "progress": None,
            "message": last[-300:] if failed else None,
        }
    running = BASE_VM in running_vms(server_id)
    ready = base_ready(server_id, running)
    message = None if ready or not running else "Windows is finishing setup (C:\\zoo\\setup.log in the VM)"
    return {"state": "running" if running else "stopped", "progress": None, "message": message, "ready": ready}


def base_install(server_id: str, iso: str, edition: str | None = None):
    """Builds the base VM from an ISO in the background, with this API's zoo-guest built in."""
    require_guest_url()
    upload_vm_guest(server_id)
    args = ["spawn", BASE_VM, "-Iso", iso] + (["-Edition", edition] if edition else [])
    zoovm_check(server_id, *args)


def base_start(server_id: str) -> str:
    """Starts the base VM. Its guest gets its identity from base_status, which the dashboard polls."""
    zoovm_check(server_id, "start", BASE_VM)
    return ""


def os_label(rid: str) -> str:
    """`windows-<build>.<revision>` of a running VM, which names the templates sealed from it."""
    with contextlib.suppress(Exception):
        build = guest_check(
            rid,
            "$v = Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion'\n"
            '"$($v.CurrentBuildNumber).$($v.UBR)"',
            timeout=15,
        ).strip()
        if re.fullmatch(r"\d+\.\d+", build):
            return f"windows-{build}"
    return "windows"


def base_stop(server_id: str):
    """Shuts the base VM down and, once its setup is done, seals its disk as the template new sandboxes clone. The
    template's version is `windows-<build>.<revision>-<UTC minute>`, and every sandbox cloned from it records it."""
    running = BASE_VM in running_vms(server_id)
    label = os_label(base_id(server_id)) if running and connect_base(server_id) else "windows"
    close_guest(base_id(server_id))
    zoovm_check(server_id, "stop", BASE_VM, "-Timeout", "120", timeout=180)
    if base_ready(server_id, running=False):
        zoovm_check(server_id, "seal", BASE_VM, "-Label", label, timeout=300)
