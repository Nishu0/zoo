"""Verify the shipped Setup Assistant policy with macOS's plist writer."""

import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(Path("/usr/libexec/PlistBuddy").exists(), "requires macOS PlistBuddy")
class SetupPolicyTests(unittest.TestCase):
    def test_system_policy_skips_apple_account_and_storage(self):
        source = (Path(__file__).parents[1] / "configure-guest.sh").read_text()
        helper_start = source.index("set_preference() {")
        helper_end = source.index("\n}\n", helper_start) + 3
        policy_start = source.index("managed_setup=")
        policy_end = source.index("# Explicitly establish", policy_start)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "setup.plist"
            script = source[helper_start:helper_end] + source[policy_start:policy_end]
            script = script.replace(
                "managed_setup=/Library/Preferences/com.apple.SetupAssistant.managed.plist",
                'managed_setup="$1"',
            )
            subprocess.run(["/bin/sh", "-eu", "-c", script, "test", str(output)], check=True)
            with output.open("rb") as file:
                policy = plistlib.load(file)
            self.assertEqual(policy["SkipSetupItems"], ["AppleID", "iCloudStorage"])
            self.assertIs(policy["SkipCloudSetup"], True)
            self.assertIs(policy["SkipiCloudStorageSetup"], True)


if __name__ == "__main__":
    unittest.main()
