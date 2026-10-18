"""Write first-login preferences using real PlistBuddy and disposable plist files.

Only preference blocks execute. Account, ownership, SSH, autologin and system
configuration operations are excluded; user ownership calls are harmless stubs.
"""

import plistlib
import re
import subprocess
from pathlib import Path

import pytest

SOURCE = (Path(__file__).resolve().parents[1] / "macos/configure-guest.sh").read_text()
PLIST_BUDDY = Path("/usr/libexec/PlistBuddy")
PREFERENCE_WRITER = "set_preference() {" + SOURCE.split("set_preference() {", 1)[1].split("\nsetup=", 1)[0]


def run_preferences(script, *arguments):
    return subprocess.run(
        ["/bin/sh", "-c", "set -eu\n" + PREFERENCE_WRITER + "\n" + script, "test", *map(str, arguments)],
        capture_output=True,
        check=True,
        timeout=5,
    )


@pytest.mark.skipif(not PLIST_BUDDY.exists(), reason="native macOS PlistBuddy is required")
@pytest.mark.parametrize(
    "kind,value,expected", [("bool", "true", True), ("integer", "0", 0), ("string", "26.2", "26.2")]
)
def test_prelogin_writer_creates_typed_preferences_without_a_gui_session(tmp_path, kind, value, expected):
    preferences = tmp_path / "home with spaces" / "Library" / "Preferences"
    preferences.mkdir(parents=True)
    path = preferences / "com.apple.SetupAssistant.plist"
    run_preferences('set_preference "$1" TestPreference "$2" "$3"', path, kind, value)
    with path.open("rb") as file:
        actual = plistlib.load(file)["TestPreference"]
    assert type(actual) is type(expected)
    assert actual == expected


@pytest.mark.skipif(not PLIST_BUDDY.exists(), reason="native macOS PlistBuddy is required")
def test_setup_assistant_preferences_are_repairable_typed_and_preserve_unrelated_values(tmp_path):
    home = tmp_path / "home with spaces"
    preferences = home / "Library/Preferences"
    preferences.mkdir(parents=True)
    path = preferences / "com.apple.SetupAssistant.plist"
    with path.open("wb") as file:
        plistlib.dump(
            {"UnrelatedPreference": "keep", "DidSeePrivacy": "wrong-type", "LastSeenBuddyBuildVersion": 1}, file
        )
    block = "setup=" + SOURCE.split("\nsetup=", 1)[1].split("# Explicitly establish", 1)[0]
    script = (
        """preferences=$1
HOME_DIR=$2
ME=test_guest
chown() { printf '%s\\n' "$*" >> "$HOME_DIR/ownership.calls"; }
defaults() { echo 'per-user defaults must not run before GUI login' >&2; exit 90; }
sw_vers() { case "$1" in -productVersion) echo 26.2 ;; -buildVersion) echo 25C56 ;; *) exit 91 ;; esac; }
"""
        + block
    )
    run_preferences(script, preferences, home)
    run_preferences(script, preferences, home)  # Retrying setup repairs types without resetting the domain.
    assert (home / ".skipbuddy").is_file()
    assert f"test_guest:staff {home / '.skipbuddy'}" in (home / "ownership.calls").read_text().splitlines()
    with path.open("rb") as file:
        actual = plistlib.load(file)
    assert actual.pop("UnrelatedPreference") == "keep"
    for key in (
        "LastPreLoginTasksPerformedVersion",
        "LastSeenAgeRangeSelectionProductVersion",
        "LastSeenCloudProductVersion",
        "LastSeenDiagnosticsProductVersion",
        "LastSeenSiriProductVersion",
        "LastSeenSyncProductVersion",
        "LastSeenIntelligenceProductVersion",
    ):
        assert actual.pop(key) == "26.2"
    for key in ("LastPreLoginTasksPerformedBuild", "LastSeenBuddyBuildVersion"):
        assert actual.pop(key) == "25C56"
    assert type(actual["MiniBuddyLaunchReason"]) is int
    assert actual.pop("MiniBuddyLaunchReason") == 0
    assert actual.pop("MiniBuddyShouldLaunchToResumeSetup") is False
    assert actual.pop("MiniBuddyLaunchedPostMigration") is False
    assert {
        "DidSeePrivacy",
        "DidSeeCloudSetup",
        "DidSeeIntelligence",
        "SkipFirstLoginOptimization",
        "DidSeeAppStore",
        "DidSeeSetupSequence",
        "DidSeeSyncSetup",
        "DidSeeSyncSetup2",
        "DidSeeiCloudLoginForStorageServices",
        "SkipExpressSettingsUpdating",
    } <= actual.keys()
    assert all(type(value) is bool and value for value in actual.values())


@pytest.mark.skipif(not PLIST_BUDDY.exists(), reason="native macOS PlistBuddy is required")
def test_screensaver_preferences_use_integer_zero_in_user_byhost_and_system_plists(tmp_path):
    preferences = tmp_path / "user preferences"
    by_host = preferences / "ByHost"
    by_host.mkdir(parents=True)
    user = preferences / "com.apple.screensaver.plist"
    first = by_host / "com.apple.screensaver.ABCD.plist"
    second = by_host / "com.apple.screensaver.EFGH.plist"
    unrelated = by_host / "com.example.unrelated.plist"
    system = tmp_path / "system screensaver.plist"
    for path in (user, first, second, unrelated):
        with path.open("wb") as file:
            plistlib.dump({"idleTime": "wrong-type", "Keep": "value"}, file)
    block = "screensaver=" + SOURCE.split("\nscreensaver=", 1)[1].split("\nsudo ", 1)[0]
    block = block.replace("/Library/Preferences/com.apple.screensaver.plist", '"$system_screensaver"')
    assert "/Library/Preferences/" not in block  # Never execute writes to the real host preference directory.
    script = "preferences=$1\nsystem_screensaver=$2\nME=test_guest\nchown() { :; }\n" + block
    run_preferences(script, preferences, system)
    for path in (user, first, second):
        with path.open("rb") as file:
            actual = plistlib.load(file)
        assert type(actual["idleTime"]) is int and actual["idleTime"] == 0
        assert actual["Keep"] == "value"
    with system.open("rb") as file:
        actual = plistlib.load(file)
    assert type(actual["loginWindowIdleTime"]) is int and actual["loginWindowIdleTime"] == 0
    with unrelated.open("rb") as file:
        assert plistlib.load(file)["idleTime"] == "wrong-type"


def test_unattended_guest_configuration_does_not_use_gui_dependent_defaults_writes():
    assert not re.search(r"\bdefaults\s+(?:write|-currentHost)\b", SOURCE)
