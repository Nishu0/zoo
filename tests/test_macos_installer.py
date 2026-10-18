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
macos_enrollment_status() { return 0; }
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
        marker.with_name("zoo-controller-key").write_text(KEY + "\n")
    installer.run(
        f"NODE_KEY={shlex.quote(KEY)}\nBASE={0 if skip == 'no-base' else 1}\nstart_macos_preparation {shlex.quote(str(installer.home))}",
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


@pytest.fixture
def enrollment_probe(installer):
    import base64
    import datetime
    import hashlib
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    directory = installer.enroll()
    key = ec.generate_private_key(ec.SECP256R1())
    ca_key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.UTC)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test authority")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "node")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    (directory / "node.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (directory / "node.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (directory / "ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    state = SimpleNamespace(
        code=200, response={"status": "current", "node_id": "node", "server_id": "server"}, requests=[], errors=[]
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            pass

        def do_POST(self):
            try:
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                state.requests.append(payload)
                assert self.path == "/v1/nodes/enrollment/status"
                assert "token" not in payload
                assert payload["certificate"] == (directory / "node.crt").read_text()
                message = f"zoo-node-enrollment-v1\n{payload['timestamp']}\n{payload['nonce']}\n".encode()
                key.public_key().verify(
                    base64.b64decode(payload["signature"], validate=True), message, ec.ECDSA(hashes.SHA256())
                )
                self.send_response(state.code)
                if state.code == 302:
                    self.send_header("Location", "/unexpected-redirect")
                self.end_headers()
                self.wfile.write(json.dumps(state.response).encode())
            except Exception as error:
                state.errors.append(error)
                self.send_error(500)

        def do_GET(self):
            state.errors.append(AssertionError("probe followed a redirect"))
            self.send_error(500)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    config = json.loads((directory / "config.json").read_text())
    config["api"] = url
    (directory / "config.json").write_text(json.dumps(config))
    fingerprint = hashlib.sha256(ca.public_bytes(serialization.Encoding.DER)).hexdigest()

    def token(ca_fingerprint=fingerprint):
        value = {"u": url, "s": "already-consumed-token-secret", "f": ca_fingerprint}
        return "zn1." + base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    def run(raw_token=""):
        result = installer.run(
            f"CONTROL_PLANE={shlex.quote(url)}\nNODE_TOKEN={shlex.quote(raw_token)}\n"
            f"macos_enrollment_status {shlex.quote(str(installer.home))}",
            check=False,
        )
        assert not state.errors
        return result

    try:
        yield SimpleNamespace(state=state, run=run, token=token, server=server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize(
    "verdict,code",
    [
        ("current", 0),
        ("registration_missing", 1),
        ("authority_changed", 1),
        ("expired", 1),
        ("registration_replaced", 1),
        ("unknown_error", 2),
    ],
)
def test_installer_real_signed_probe_distinguishes_current_stale_and_unknown_results(enrollment_probe, verdict, code):
    probe = enrollment_probe
    if verdict != "current":
        probe.state.response = {"status": "reenroll", "reason": verdict}
    assert probe.run().returncode == code
    assert len(probe.state.requests) == 1


def test_same_ca_consumed_token_reuses_signed_registration_without_redeeming_token(enrollment_probe):
    assert enrollment_probe.run(enrollment_probe.token()).returncode == 0
    assert len(enrollment_probe.state.requests) == 1


def test_new_token_pinned_to_another_ca_classifies_stale_credentials_before_network(enrollment_probe):
    assert enrollment_probe.run(enrollment_probe.token("0" * 64)).returncode == 1
    assert not enrollment_probe.state.requests


@pytest.mark.parametrize("status", [302, 401, 404, 500])
def test_redirect_or_http_failures_never_authorize_reenrollment(enrollment_probe, status):
    enrollment_probe.state.code = status
    assert enrollment_probe.run().returncode == 2


def test_current_status_with_wrong_identity_fails_closed(enrollment_probe):
    enrollment_probe.state.response = {"status": "current", "node_id": "other", "server_id": "server"}
    assert enrollment_probe.run().returncode == 2


def test_unreachable_control_plane_is_not_a_stale_registration(enrollment_probe):
    enrollment_probe.server.shutdown()
    enrollment_probe.server.server_close()
    assert enrollment_probe.run().returncode == 2


@pytest.mark.parametrize(
    "mode", ["success", "join-failed", "incomplete", "restart-failed", "stop-stuck", "promote-failed", "backup-failed"]
)
def test_stale_registration_repair_stages_credentials_and_rolls_back_failures(installer, tmp_path, mode):
    directory = installer.enroll()
    preserved = {
        name: (directory / name).read_bytes()
        for name in ("config.json", "node.key", "node.crt", "ca.crt", "control-plane.pub")
    }
    disk = installer.home / ".zoovm/vms/zoo-existing/disk.img"
    disk.parent.mkdir(parents=True)
    disk.write_bytes(b"must preserve the existing VM disk")
    active = tmp_path / "service.active"
    active.touch()
    binary = tmp_path / "downloaded-node"
    binary.write_text(
        """#!/bin/bash
set -eu
umask 077
[ "$1" = join ] || exit 90
shift
token=$1
shift
printf 'untrusted remote echo: %s\\n' "$token"
[ "$token" != spent-token ] || exit 91
while [ "$#" -gt 0 ]; do
    case "$1" in --dir) directory=$2; shift 2 ;; --ssh-user) shift 2 ;; *) exit 92 ;; esac
done
printf '{"api":"https://zoo.example","node_id":"new-node","server_id":"new-server","endpoints":["node.example:7443"]}' > "$directory/config.json"
"""
        + (
            ""
            if mode == "incomplete"
            else """for file in node.key node.crt ca.crt; do printf 'new-%s' "$file" > "$directory/$file"; done
"""
        )
    )
    binary.chmod(0o755)
    failure = mode in ("restart-failed", "stop-stuck")
    stubs = f"""chown() {{ :; }}
id() {{ echo 501; }}
macos_enrollment_status() {{ return 1; }}
curl() {{
    local destination=''
    while [ "$#" -gt 0 ]; do case "$1" in -o) destination=$2; shift 2 ;; *) shift ;; esac; done
    cp {shlex.quote(str(binary))} "$destination"
}}
as_user() {{
    if [ "$1" != launchctl ]; then "$@"; return; fi
    printf '%s\\n' "$*" >> {shlex.quote(str(installer.trace))}
    case "$2" in
        bootout) {":" if mode == "stop-stuck" else "rm -f " + shlex.quote(str(active))}; return 0 ;;
        print) [ -f {shlex.quote(str(active))} ] ;;
        bootstrap)
            if [ {int(failure)} = 1 ] && grep -q new-node {shlex.quote(str(directory / "config.json"))}; then return 1; fi
            touch {shlex.quote(str(active))}; return 0 ;;
        *) return 94 ;;
    esac
}}
mv() {{
    if [ {shlex.quote(mode)} = backup-failed ] && [ "$1" = {shlex.quote(str(directory))} ]; then return 1; fi
    if [ {shlex.quote(mode)} = promote-failed ] && [ "$2" = {shlex.quote(str(directory))} ]; then
        case "$1" in *.previous) ;; *) return 1 ;; esac
    fi
    command mv "$@"
}}
"""
    result = installer.run(
        f"NODE_USER=test_user\nNODE_KEY={shlex.quote(KEY)}\nCONTROL_PLANE=https://zoo.example\n"
        f"NODE_TOKEN={'spent-token' if mode == 'join-failed' else 'fresh-token'}\n"
        f"join_or_resume_macos_node {shlex.quote(str(installer.home))}",
        stubs=stubs,
        check=False,
    )
    assert b"fresh-token" not in result.stdout + result.stderr
    assert b"spent-token" not in result.stdout + result.stderr
    assert disk.read_bytes() == b"must preserve the existing VM disk"
    assert active.exists(), "an intact old service must be restored when repair fails: " + result.stderr.decode()
    assert not list(installer.home.glob(".zoo-node-enroll.*")), result.stderr.decode()
    if mode == "success":
        assert result.returncode == 0, result.stderr.decode()
        assert json.loads((directory / "config.json").read_text())["node_id"] == "new-node"
        assert (directory / "node.key").read_text() == "new-node.key"
        assert (directory / "node.key").stat().st_mode & 0o777 == 0o600
        assert directory.stat().st_mode & 0o777 == 0o700
        service_events = installer.trace.read_text().splitlines()
        assert any("bootout gui/501/dev.zoo.node" in event for event in service_events)
        assert any("bootstrap gui/501" in event for event in service_events)
    else:
        assert result.returncode != 0
        assert {name: (directory / name).read_bytes() for name in preserved} == preserved
        if mode in ("join-failed", "incomplete"):
            assert not installer.trace.exists(), "staging must finish before the old service is stopped"


@pytest.mark.parametrize("verdict", [1, 2])
def test_unverified_registration_never_reports_success_or_overwrites_credentials(installer, verdict):
    directory = installer.enroll()
    previous = {name: path.read_bytes() for path in directory.iterdir() if (name := path.name) != "bin"}
    result = installer.run(
        f'NODE_USER=test_user\nNODE_KEY={shlex.quote(KEY)}\nNODE_TOKEN=""\n'
        f"join_or_resume_macos_node {shlex.quote(str(installer.home))}",
        stubs=f"""macos_enrollment_status() {{ return {verdict}; }}
reenroll_macos_node() {{ echo 'must not replace credentials without verified stale status and a fresh token' >&2; exit 95; }}
resume_macos_node_service() {{ echo 'must not claim a stale or unreachable registration is resumed' >&2; exit 96; }}
""",
        check=False,
    )
    assert result.returncode != 0
    assert {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()} == previous


def test_resuming_a_service_keeps_an_existing_correct_system_symlink_without_replacing_it(installer):
    directory = installer.enroll()
    link = installer.binary / "zoo-node"
    link.symlink_to(directory / "bin/zoo-node")
    before = link.lstat().st_ino
    installer.run(
        f"NODE_USER=test_user\nresume_macos_node_service {shlex.quote(str(installer.home))}",
        stubs="""chown() { :; }
id() { echo 501; }
as_user() { [ "$1" = launchctl ]; }
ln() { echo 'must not replace an already correct root-managed symlink' >&2; exit 97; }
""",
    )
    assert link.lstat().st_ino == before
    assert link.readlink() == directory / "bin/zoo-node"


@pytest.mark.parametrize("old_key", [None, "ssh-ed25519 OLDKEY"])
def test_an_old_ready_template_is_prepared_again_when_its_controller_identity_does_not_match(installer, old_key):
    template = installer.home / ".zoovm/vms/zoo-macos-base"
    template.mkdir(parents=True)
    (template / "zoo-ready").touch()
    if old_key:
        (template / "zoo-controller-key").write_text(old_key + "\n")
    installer.run(
        f"NODE_KEY={shlex.quote(KEY)}\nstart_macos_preparation {shlex.quote(str(installer.home))}",
        stubs=f"as_user() {{ cat > {shlex.quote(str(installer.trace))}; }}",
    )
    assert installer.trace.read_text() == KEY + "\n"
