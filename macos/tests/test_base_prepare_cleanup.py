"""Execute production cleanup functions with failed disk operations, without root."""

import subprocess
import tempfile
import unittest
from pathlib import Path


class BasePreparationCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (Path(__file__).parents[1] / "base-prepare.sh").read_text()
        cls.functions = source[source.index("close_guest_disk() {") : source.index("trap cleanup EXIT")]

    def run_cleanup(self, *, unmount=0, detach=0, body="exit 0", keep_payload=True):
        directory = tempfile.TemporaryDirectory(prefix="zoo-cleanup-test-")
        self.addCleanup(directory.cleanup)
        work = Path(directory.name) / "mount-work"
        (work / "data").mkdir(parents=True)
        payload = work / "data" / "guest-data"
        if keep_payload:
            payload.write_text("guest files must survive cleanup failures")
        script = (
            "set -euo pipefail\nwork=$1\ndev=/dev/test-guest\nmounted=1\nname=zoo-test\n"
            + self.functions
            + f"\nunmount_guest_volume() {{ return {unmount}; }}\n"
            + f"detach_guest_disk() {{ return {detach}; }}\n"
            + "trap cleanup EXIT\n"
            + body
        )
        result = subprocess.run(
            ["/bin/bash", "-c", script, "test", str(work)], capture_output=True, text=True, check=False
        )
        return result, work, payload

    def test_detach_failure_blocks_success_and_preserves_guest_files(self):
        result, _, payload = self.run_cleanup(detach=1, body="finish_seed ZOO_BASE_SEEDED")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("ZOO_BASE_SEEDED=", result.stdout)
        self.assertIn("Do not boot", result.stderr)
        self.assertTrue(payload.is_file())

    def test_unmount_and_detach_failure_preserve_original_error(self):
        result, _, payload = self.run_cleanup(unmount=1, detach=1, body="exit 7")
        self.assertEqual(result.returncode, 7)
        self.assertTrue(payload.is_file())

    def test_successful_body_fails_if_exit_cleanup_cannot_detach(self):
        result, _, payload = self.run_cleanup(detach=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(payload.is_file())

    def test_successful_detach_can_finish_after_unmount_failure(self):
        result, work, _ = self.run_cleanup(unmount=1, body="finish_seed ZOO_BASE_SEEDED", keep_payload=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ZOO_BASE_SEEDED=zoo-test", result.stdout)
        self.assertFalse(work.exists())

    def test_successful_cleanup_preserves_original_handler_error(self):
        result, work, _ = self.run_cleanup(body="exit 9", keep_payload=False)
        self.assertEqual(result.returncode, 9)
        self.assertFalse(work.exists())

    def test_nonempty_mount_directory_is_never_deleted_recursively(self):
        result, _, payload = self.run_cleanup()
        self.assertEqual(result.returncode, 0)
        self.assertTrue(payload.is_file())


if __name__ == "__main__":
    unittest.main()
