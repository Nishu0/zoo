"""Exercise privileged helper's mounted-tree refresh as an ordinary user.

The actual branch runs against a disposable directory. Ownership changes are
stubbed; disk attachment/mounting and the helper's privileged entry point never run.
"""

import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def seeded_guest(tmp_path):
    work = tmp_path / "work"
    mounted = work / "data"
    guest = mounted / "private/var/db/zoo"
    guest.mkdir(parents=True)
    launch = mounted / "Library/LaunchDaemons"
    launch.mkdir(parents=True)
    payload = tmp_path / "packaged"
    payload.mkdir()
    scripts = ("firstboot.sh", "configure-guest.sh", "guest-reidentify.sh")
    for script in scripts:
        (payload / script).write_text(f"new fixed {script}\n")
        (guest / script).write_text(f"old {script}\n")
    credentials = {"user": b"admin\n", "password": b"preserved-password\n", "key": b"first-key\nsecond-key\n"}
    for name, content in credentials.items():
        (guest / name).write_bytes(content)
    plist = launch / "com.zoo.firstboot.plist"
    plist.write_text("old launch daemon\n")
    # Run exactly the existing-seed mounted-volume branch. No privileged entry
    # checks or disk tools execute; install keeps mode handling but omits owners.
    source = (ROOT / "macos/base-prepare.sh").read_text()
    branch = source.split('mnt="$work/data"', 1)[1].split('[ ! -e "$db/.AppleSetupDone" ]', 1)[0]
    closing = "close_guest_disk() {" + source.split("close_guest_disk() {", 1)[1].split("trap cleanup EXIT", 1)[0]
    prefix = f"""set -euo pipefail
ROOT={shlex.quote(str(payload))}
work={shlex.quote(str(work))}
mnt="$work/data"
was_seeded=1
name=zoo-test
mounted=1
dev=/dev/disposable-test-disk
# Only the low-level disk primitives are stubbed; production close/finalize runs.
unmount_guest_volume() {{ :; }}
detach_guest_disk() {{ :; }}
fail() {{ printf '%s\\n' "$*" >&2; exit 1; }}
chown() {{ :; }}
install() {{
    local mode=755
    while [ "$#" -gt 2 ]; do
        case "$1" in -o|-g) shift 2 ;; -m) mode=$2; shift 2 ;; *) exit 91 ;; esac
    done
    /usr/bin/install -m "$mode" "$1" "$2"
}}
"""

    def run():
        return subprocess.run(
            ["bash", "-c", prefix + closing + "trap cleanup EXIT\n" + branch],
            capture_output=True,
            timeout=5,
            check=False,
        )

    return SimpleNamespace(
        mounted=mounted,
        guest=guest,
        payload=payload,
        plist=plist,
        scripts=scripts,
        credentials=credentials,
        run=run,
    )


@pytest.mark.parametrize("completed", [False, True])
def test_retry_refreshes_only_incomplete_guest_payload_and_preserves_credentials(seeded_guest, completed):
    guest = seeded_guest
    if completed:
        (guest.guest / "done").touch()
    result = guest.run()
    assert result.returncode == 0, result.stderr.decode()
    expected = b"ZOO_BASE_CONFIGURED=" if completed else b"ZOO_BASE_SEEDED="
    assert expected in result.stdout
    for name, content in guest.credentials.items():
        assert (guest.guest / name).read_bytes() == content
    for script in guest.scripts:
        assert (guest.guest / script).read_text() == (f"old {script}\n" if completed else f"new fixed {script}\n")
    if completed:
        assert guest.plist.read_text() == "old launch daemon\n"
    else:
        assert "/var/db/zoo/firstboot.sh" in guest.plist.read_text()
        assert (guest.guest / "firstboot.sh").stat().st_mode & 0o777 == 0o700
        assert guest.plist.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize("target", ["firstboot.sh", "configure-guest.sh", "guest-reidentify.sh", "plist"])
def test_retry_rejects_symlink_targets_before_writing_packaged_payload(seeded_guest, tmp_path, target):
    guest = seeded_guest
    outside = tmp_path / "outside"
    outside.write_text("must remain unchanged\n")
    selected = guest.plist if target == "plist" else guest.guest / target
    selected.unlink()
    selected.symlink_to(outside)
    result = guest.run()
    assert result.returncode != 0
    assert b"unexpected symlink" in result.stderr
    assert outside.read_text() == "must remain unchanged\n"
    for name, content in guest.credentials.items():
        assert (guest.guest / name).read_bytes() == content
    # All targets are checked before the first payload write, including late targets.
    for script in guest.scripts:
        if script != target:
            assert (guest.guest / script).read_text() == f"old {script}\n"


@pytest.mark.parametrize(
    "relative", ["private", "private/var", "private/var/db", "private/var/db/zoo", "Library", "Library/LaunchDaemons"]
)
def test_retry_rejects_symlinked_directory_paths_before_any_payload_write(seeded_guest, relative):
    guest = seeded_guest
    original = guest.mounted / relative
    moved = original.with_name(original.name + "-outside")
    original.rename(moved)
    original.symlink_to(moved, target_is_directory=True)
    result = guest.run()
    assert result.returncode != 0
    assert b"unexpected symlink" in result.stderr
    assert (guest.guest / "firstboot.sh").read_text() == "old firstboot.sh\n"
    assert guest.plist.read_text() == "old launch daemon\n"
