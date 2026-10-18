"""Exercise sourced Mac installer functions with disposable homes and root paths."""

import json
import os
import plistlib
import shlex
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEY = "ssh-ed25519 AAAATESTKEY"


@pytest.fixture
def installer(tmp_path):
    root = tmp_path / "root"
    binary = root / "usr/local/bin"
    binary.mkdir(parents=True)
    libexec = root / "usr/local/libexec/zoo-macos"
    libexec.mkdir(parents=True)
    home = tmp_path / "host home"
    home.mkdir()
    source = (ROOT / "install.sh").read_text().replace("/usr/local/", str(root / "usr/local") + "/")
    source = source.replace("/etc/", str(root / "etc") + "/")
    script = tmp_path / "install.sh"
    script.write_text(source)
    env = os.environ.copy()
    for name in ("ZOO_VERSION", "ZOO_HOME", "ZOO_MACOS_BASE", "ZOO_MACOS_USER"):
        env.pop(name, None)
    trace = tmp_path / "trace"

    def run(body, *, stubs="", check=True):
        text = f"source {shlex.quote(str(script))}\n" + stubs + "\n" + body
        return subprocess.run(["bash", "-c", text], env=env, capture_output=True, timeout=10, check=check)

    def enroll(*, cached_key=KEY):
        directory = home / ".zoo-node"
        directory.mkdir(exist_ok=True)
        config = {
            "api": "https://zoo.example",
            "node_id": "node",
            "server_id": "server",
            "endpoints": ["node.example:7443"],
        }
        (directory / "config.json").write_text(json.dumps(config))
        for name in ("node.key", "node.crt", "ca.crt"):
            (directory / name).write_text(f"retained-{name}")
        if cached_key is not None:
            (directory / "control-plane.pub").write_text(cached_key + "\n")
        node = directory / "bin/zoo-node"
        node.parent.mkdir(exist_ok=True)
        node.write_text("#!/bin/sh\nexit 0\n")
        node.chmod(0o755)
        return directory

    return SimpleNamespace(
        root=root, home=home, binary=binary, libexec=libexec, script=script, trace=trace, run=run, enroll=enroll
    )


@pytest.mark.parametrize("missing", [None, "config.json", "node.key", "node.crt", "ca.crt"])
def test_existing_registration_requires_config_and_all_credentials(installer, missing):
    directory = installer.enroll()
    if missing:
        (directory / missing).unlink()
    result = installer.run(f"macos_existing_node {shlex.quote(str(installer.home))}", check=False)
    assert (result.returncode == 0) is (missing is None)


@pytest.mark.parametrize("cached", [KEY, "malformed key", None])
def test_existing_identity_is_reused_and_missing_valid_public_key_is_fetched(installer, cached):
    installer.enroll(cached_key=cached)
    stubs = f"""curl() {{ printf '%s\\n' "$*" >> {shlex.quote(str(installer.trace))}; printf '%s\\n' {shlex.quote(KEY)}; }}"""
    result = installer.run(
        f'macos_node_identity {shlex.quote(str(installer.home))}\nprintf "%s\\n%s\\n" "$CONTROL_PLANE" "$NODE_KEY"',
        stubs=stubs,
    )
    assert result.stdout.decode().splitlines() == ["https://zoo.example", KEY]
    if cached == KEY:
        assert not installer.trace.exists()
    else:
        assert "https://zoo.example/v1/nodes/bootstrap/ssh-key" in installer.trace.read_text()


def test_existing_registration_cannot_be_silently_redirected_to_another_control_plane(installer):
    installer.enroll()
    result = installer.run(
        f"CONTROL_PLANE=https://other.example\nmacos_node_identity {shlex.quote(str(installer.home))}",
        check=False,
    )
    assert result.returncode != 0
    assert b"already enrolled" in result.stderr


def test_existing_node_resumes_with_a_used_token_and_keeps_its_identity(installer):
    directory = installer.enroll()
    original = {name: (directory / name).read_bytes() for name in ("config.json", "node.key", "node.crt", "ca.crt")}
    stubs = """chown() { :; }
id() { printf '501\\n'; }
as_user() { [ "$1" = launchctl ] || return 91; }
join_node() { echo 'must not reuse a consumed enrollment token' >&2; exit 92; }
"""
    installer.run(
        f"NODE_USER=test_user\nNODE_TOKEN=already-consumed-token\nNODE_KEY={shlex.quote(KEY)}\n"
        f"join_or_resume_macos_node {shlex.quote(str(installer.home))}",
        stubs=stubs,
    )
    assert {name: (directory / name).read_bytes() for name in original} == original
    with (installer.home / "Library/LaunchAgents/dev.zoo.node.plist").open("rb") as stream:
        service = plistlib.load(stream)
    assert service["ProgramArguments"] == [str(directory / "bin/zoo-node"), "run", "--dir", str(directory)]
    assert (directory / "control-plane.pub").read_text().strip() == KEY


@pytest.mark.parametrize("skip", ["no-base", "ready"])
def test_preparation_is_not_started_when_deferred_or_already_ready(installer, skip):
    if skip == "ready":
        marker = installer.home / ".zoovm/vms/zoo-macos-base/zoo-ready"
        marker.parent.mkdir(parents=True)
        marker.touch()
    installer.run(
        f"BASE={0 if skip == 'no-base' else 1}\nstart_macos_preparation {shlex.quote(str(installer.home))}",
        stubs="as_user() { echo 'preparation must not start' >&2; exit 93; }",
    )
    assert not (installer.home / ".zoovm/install.log").exists()


def test_preparation_background_process_receives_ssh_key_and_quoted_restore_image(installer, tmp_path):
    capture = tmp_path / "capture"
    capture.mkdir()
    coordinator = installer.libexec / "prepare-base.sh"
    coordinator.write_text(
        "#!/bin/bash\nset -eu\n"
        f"cat > {shlex.quote(str(capture / 'key'))}\n"
        f"printf '%s\\0' \"$@\" > {shlex.quote(str(capture / 'args'))}\n"
        f"touch {shlex.quote(str(capture / 'done'))}\n"
    )
    coordinator.chmod(0o755)
    ipsw = tmp_path / "restore image with spaces.ipsw"
    installer.run(
        f"NODE_KEY={shlex.quote(KEY)}\nIPSW={shlex.quote(str(ipsw))}\n"
        f"start_macos_preparation {shlex.quote(str(installer.home))}",
        stubs='as_user() { "$@"; }',
    )
    deadline = time.monotonic() + 5
    while not (capture / "done").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert (capture / "done").exists()
    assert (capture / "key").read_bytes() == (KEY + "\n").encode()
    assert (capture / "args").read_bytes().split(b"\0")[:-1] == [
        b"--name",
        b"zoo-macos-base",
        b"--guest-user",
        b"admin",
        b"--ipsw",
        str(ipsw).encode(),
    ]


@pytest.mark.parametrize("explicit,active", [(False, False), (False, True), (True, True)])
def test_native_runtime_is_preserved_by_default_or_while_vms_are_active(installer, explicit, active):
    runtime = installer.binary / "zoovm"
    runtime.write_text("#!/bin/sh\necho retained-build\n")
    runtime.chmod(0o755)
    installer.run(
        f"ZOO_VERSION=0.1.0\nNATIVE_VERSION_REQUESTED={int(explicit)}\ninstall_macos_runtime",
        stubs=f"""pgrep() {{ return {0 if active else 1}; }}
curl() {{ echo 'native download must not happen' >&2; exit 94; }}
""",
    )
    assert runtime.read_text() == "#!/bin/sh\necho retained-build\n"


def test_explicit_version_option_is_required_to_replace_an_existing_runtime(installer):
    result = installer.run('parse_args --version v0.2.3\nprintf "%s %s\\n" "$ZOO_VERSION" "$NATIVE_VERSION_REQUESTED"')
    assert result.stdout.strip() == b"0.2.3 1"


@pytest.mark.parametrize("mode", ["install", "check", "failed-preflight", "no-base"])
def test_one_command_enrolls_before_preparation_and_preflight_never_mutates_the_host(installer, mode):
    installer.enroll()
    trace = shlex.quote(str(installer.trace))
    home = shlex.quote(str(installer.home))
    stubs = f"""uname() {{ case "$1" in -s) echo Darwin ;; -m) echo arm64 ;; *) return 91 ;; esac; }}
id() {{ echo 0; }}
dscl() {{ printf 'NFSHomeDirectory: %s\\n' {home}; }}
sw_vers() {{ echo 15.6; }}
sysctl() {{ echo 34359738368; }}
need_disk() {{ {"fail 'not enough disk'" if mode == "failed-preflight" else ":"}; }}
need_memory() {{ :; }}
reach_control_plane() {{ :; }}
nc() {{ return 0; }}
launchctl() {{ :; }}
visudo() {{ :; }}
authorize() {{ echo authorize >> {trace}; }}
install_macos_runtime() {{ echo runtime >> {trace}; }}
install_macos_helper() {{ echo helper >> {trace}; }}
join_or_resume_macos_node() {{ echo enrollment >> {trace}; }}
"""
    if mode == "install":
        stubs += f"start_macos_preparation() {{ echo preparation >> {trace}; }}\n"
    arguments = "--node --user test_user"
    if mode == "check":
        arguments += " --check"
    if mode == "no-base":
        arguments += " --no-base"
    main = installer.script.read_text().split('if [[ "${BASH_SOURCE[0]:-$0}" != "$0" ]]; then return 0; fi', 1)[1]
    result = installer.run(f"set -- {arguments}\n" + main, stubs=stubs, check=False)
    if mode == "failed-preflight":
        assert result.returncode != 0
        assert b"pre-flight checks failed" in result.stderr
    else:
        assert result.returncode == 0, result.stderr.decode()
    if mode in ("check", "failed-preflight"):
        assert not installer.trace.exists()
        assert not (installer.root / "etc").exists()
    elif mode == "no-base":
        assert installer.trace.read_text().splitlines() == ["authorize", "runtime", "helper", "enrollment"]
        assert not (installer.home / ".zoovm/install.log").exists()
    else:
        assert installer.trace.read_text().splitlines() == [
            "authorize",
            "runtime",
            "helper",
            "enrollment",
            "preparation",
        ]


@pytest.mark.parametrize("valid_checksum", [False, True])
def test_explicit_inactive_runtime_upgrade_checks_checksum_before_atomic_replacement(
    installer, tmp_path, valid_checksum
):
    import hashlib
    import io
    import tarfile

    runtime = installer.binary / "zoovm"
    runtime.write_bytes(b"old retained runtime\n")
    runtime.chmod(0o755)
    archive = tmp_path / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        content = b"new verified runtime\n"
        item = tarfile.TarInfo("zoovm")
        item.size = len(content)
        bundle.addfile(item, io.BytesIO(content))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest() if valid_checksum else "0" * 64
    sums = tmp_path / "SHA256SUMS"
    sums.write_text(f"{digest}  ./zoovm-macos-arm64.tar.gz\n")
    staging = tmp_path / "staging"
    stubs = f"""pgrep() {{ return 1; }}
mktemp() {{ mkdir -p {shlex.quote(str(staging))}; printf '%s\\n' {shlex.quote(str(staging))}; }}
curl() {{
    local destination='' url=''
    while [ "$#" -gt 0 ]; do
        case "$1" in -o) destination=$2; shift 2 ;; https:*) url=$1; shift ;; *) shift ;; esac
    done
    case "$url" in
        */SHA256SUMS) cp {shlex.quote(str(sums))} "$destination" ;;
        */zoovm-macos-arm64.tar.gz) cp {shlex.quote(str(archive))} "$destination" ;;
        *) return 95 ;;
    esac
}}
install() {{
    printf '%s\\n' "${{@: -1}}" >> {shlex.quote(str(installer.trace))}
    while [ "$#" -gt 2 ]; do shift 2; done
    /usr/bin/install -m 755 "$1" "$2"
}}
codesign() {{ return 1; }}
"""
    result = installer.run(
        "ZOO_VERSION=0.2.3\nNATIVE_VERSION_REQUESTED=1\ninstall_macos_runtime", stubs=stubs, check=False
    )
    if valid_checksum:
        assert result.returncode == 0, result.stderr.decode()
        assert runtime.read_bytes() == b"new verified runtime\n"
        assert installer.trace.read_text().strip() == str(runtime) + ".new"
        assert not runtime.with_name("zoovm.new").exists()
    else:
        assert result.returncode != 0
        assert b"doesn't match" in result.stderr
        assert runtime.read_bytes() == b"old retained runtime\n"
        assert not installer.trace.exists()
        assert not runtime.with_name("zoovm.new").exists()
