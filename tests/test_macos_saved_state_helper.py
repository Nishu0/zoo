from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = (ROOT / "macos/zoovm/main.swift").read_text()
SETUP = (ROOT / "macos/guest-setup.sh").read_text()


def section(start: str, end: str) -> str:
    return HELPER.split(start, 1)[1].split(end, 1)[0]


def test_saved_slots_validate_origin_account_and_configuration():
    validation = section("func validateSavedState(", "func lease(")
    for guard in (
        "state.host == (try originatingHost())",
        "state.uid == getuid()",
        "state.hostVersion == ProcessInfo.processInfo.operatingSystemVersionString",
        "state.topology == savedTopology",
        '"config.json", state.config',
        '"machine.bin", state.machine',
        '"hardware.bin", state.hardware',
    ):
        assert guard in validation


def test_saved_state_is_captured_with_disk_and_auxiliary_storage_while_paused():
    saving = section("func saveState()", "func serveVNC()")
    assert saving.index("validateSaveRestoreSupport()") < saving.index("try await vm.pause()")
    assert saving.index("try await vm.pause()") < saving.index("try await vm.saveMachineStateTo")
    assert saving.index("try await vm.saveMachineStateTo") < saving.index("try handle.synchronize()")
    assert saving.index("try handle.synchronize()") < saving.index("try privateClone")
    assert saving.index("try privateClone") < saving.index("try await vm.stop()")
    assert 'for part in ["disk.img", "aux.img"]' in saving
    assert "RENAME_SWAP" in saving


def test_clone_state_keeps_identity_and_claims_one_exclusive_slot():
    cloning = section("func cloneState(", "func controlSocket(")
    assert 'BundleLock("bundle:\\(source)")' in cloning
    assert "requireUnleased(source)" in cloning
    assert 'mkdir(file(source, ".lease").path, 0o700)' in cloning
    assert "try privateClone" in cloning
    assert "VZMacMachineIdentifier()" not in cloning
    assert "randomLocallyAdministered" not in cloning
    assert "copyItem" not in HELPER
    for part in ("machine.bin", "aux.img", "disk.img", "config.json"):
        assert part in section("let savedParts =", "struct SavedState:")


def test_restore_is_paused_then_resumes_without_network_or_cold_fallback():
    restore = section("if restoring {", "} else {")
    assert "try lease(name)" in restore
    assert "validateSaveRestoreSupport()" in restore
    assert "try await vm.restoreMachineStateFrom" in restore
    assert "vm.state == .paused" in restore
    assert restore.index("device.attachment = nil") < restore.index("try await vm.resume()")
    assert "vm.start()" not in restore
    assert 'removeItem(at: file(name, "state.vzvmsave"))' in restore


def test_slot_and_identity_guards_cover_runners_and_lease_release():
    runner = section("init(name: String)", "func start(restoring:")
    for lock in ("bundle:", "machine:", "mac:"):
        assert f'BundleLock("{lock}' in runner
    release = section("func releaseSlot(", "let args =")
    assert "!FileManager.default.fileExists(atPath: bundle(claim.owner).path)" in release
    assert 'BundleLock("bundle:\\(claim.owner)")' in release


def test_vm_listing_excludes_the_private_lock_directory():
    listing = section("func list()", "func delete(")
    assert "names.sorted().filter(validName).filter(exists)" in listing


def test_live_guest_reidentification_restarts_the_launch_agent_after_atomic_write():
    script = SETUP.split("<<'ZOO_REIDENTIFY'", 1)[1].split("\nZOO_REIDENTIFY", 1)[0]
    assert 'cat > "$temporary"' in script
    assert script.index("launchctl bootout") < script.index('mv -f "$temporary"')
    assert script.index('mv -f "$temporary"') < script.index("launchctl bootstrap")
    assert script.index("launchctl bootstrap") < script.index("ZOO_REIDENTIFIED=")
    assert "ZOO_GUEST_URL|ZOO_GUEST_TOKEN|ZOO_SANDBOX_ID" in script
    assert "identity.ready" in script
