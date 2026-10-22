"""The macOS stop/start saved-state decision matrix, without touching a real Mac.

stop: save-state (pause) when enabled and it succeeds; plain shutdown otherwise.
start: resume from the saved state, fall back to a cold boot of the same disk when
the resume fails, and clone from base only when no bundle exists.
"""

import os
import unittest
from unittest import mock

from apps.server import macos


def server():
    return mock.Mock(id="srv-1", docker_url="ssh://srv-1")


class ScriptedHost:
    """run()/check() stand-in: records commands, replies per prefix."""

    def __init__(self, codes: dict[str, int]):
        self.codes = codes
        self.commands: list[str] = []

    def run(self, _server_id, command, stdin=None, timeout=60):
        self.commands.append(command)
        code = next((c for prefix, c in self.codes.items() if command.startswith(prefix)), 0)
        return code, b"", b""

    def check(self, _server_id, command, stdin=None, timeout=60):
        code, out, _ = self.run(_server_id, command, stdin, timeout)
        if code != 0:
            raise RuntimeError(command)
        return ""


class StopTests(unittest.TestCase):
    def setUp(self):
        self.host = ScriptedHost({"zoovm save-state": 0})
        self.guest = mock.MagicMock()
        mock.patch.object(macos, "run", self.host.run).start()
        mock.patch.object(macos, "check", self.host.check).start()
        mock.patch.object(macos, "guest", self.guest).start()
        self.addCleanup(mock.patch.stopall)

    def stopped(self, *, suspend=True):
        with mock.patch.object(macos, "SUSPEND", suspend):
            macos.stop(macos.runtime_id("srv-1", "zoo-x"))

    def test_save_state_when_it_succeeds(self):
        self.stopped()
        self.assertTrue(any(c.startswith("zoovm save-state") for c in self.host.commands))
        self.guest.assert_not_called()  # no shutdown: the saved memory is the product
        self.assertFalse(any(c.startswith("zoovm stop") for c in self.host.commands))

    def test_shutdown_fallback_when_save_fails(self):
        self.host.codes["zoovm save-state"] = 1
        self.stopped()
        self.assertTrue(any("shutdown -h now" in str(c) for c in self.guest.call_args_list))
        self.assertTrue(any(c.startswith("zoovm stop") for c in self.host.commands))

    def test_no_save_attempt_when_suspend_disabled(self):
        self.stopped(suspend=False)
        self.assertFalse(any("save-state" in c for c in self.host.commands))
        self.assertTrue(any("shutdown -h now" in str(c) for c in self.guest.call_args_list))


class StartTests(unittest.TestCase):
    def setUp(self):
        self.host = ScriptedHost({})
        mock.patch.object(macos, "run", self.host.run).start()
        mock.patch.object(macos, "check", self.host.check).start()
        for name in ("connect", "ensure_base", "boot", "resume_vm", "wait_for_guest"):
            m = mock.MagicMock(return_value="")
            mock.patch.object(macos, name, m).start()
            setattr(self, name, m)
        mock.patch.object(macos, "base_version", return_value="27.0").start()
        for name in ("configure_guest", "install_guest"):
            m = mock.MagicMock()
            mock.patch.object(macos, name, m).start()
            setattr(self, name, m)
        self.hub = mock.MagicMock()
        mock.patch.object(macos, "hub", self.hub).start()
        self.addCleanup(mock.patch.stopall)

    def started(self, *, saved=1, exists=1, resume_ok=True, suspend=True):
        self.host.codes["zoovm saved-state"] = saved
        self.host.codes["zoovm get"] = exists
        if not resume_ok:
            self.resume_vm.side_effect = RuntimeError("resume boom")
        with mock.patch.object(macos, "SUSPEND", suspend):
            return macos.start("sbx-1", server(), {"ZOO_KEY": "v"}, admin=False)

    def test_resume_path(self):
        self.started(saved=0, exists=0)
        self.resume_vm.assert_called_once()
        self.boot.assert_not_called()
        self.wait_for_guest.assert_not_called()  # resumed memory: sshd is already up
        self.ensure_base.assert_not_called()
        self.configure_guest.assert_called_once()  # admin + env in one round trip
        self.install_guest.assert_called_once()
        self.assertTrue(self.install_guest.call_args.kwargs.get("resumed"))
        self.hub.bind.assert_called_once()

    def test_resume_failure_cold_boots_the_same_disk(self):
        self.started(saved=0, exists=0, resume_ok=False)
        self.boot.assert_called_once()
        self.assertTrue(any("rm -rf" in c and "/saved" in c for c in self.host.commands))
        self.wait_for_guest.assert_called_once()  # cold boot: poll for sshd
        self.assertFalse(self.install_guest.call_args.kwargs.get("resumed"))
        self.ensure_base.assert_not_called()  # the bundle exists; never re-clone over user data

    def test_cold_clone_when_no_bundle(self):
        self.started(saved=1, exists=1)
        self.resume_vm.assert_not_called()
        self.ensure_base.assert_called_once()
        self.assertTrue(any("zoovm clone" in c for c in self.host.commands))
        self.boot.assert_called_once()
        self.wait_for_guest.assert_called_once()
        self.configure_guest.assert_called_once()  # cold path also runs the merged admin+env op

    def test_saved_state_ignored_when_suspend_disabled(self):
        self.started(saved=0, exists=0, suspend=False)
        self.resume_vm.assert_not_called()
        self.wait_for_guest.assert_called_once()


if __name__ == "__main__":
    unittest.main()
