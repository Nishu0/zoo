"""Check the legacy loginwindow byte encoding with the actual system awk."""

import secrets
import subprocess
import unittest
from pathlib import Path


class AutologinCredentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (Path(__file__).parents[1] / "configure-guest.sh").read_text()
        start = source.index("printf '%s\\000'")
        end = source.index('\' > "$temporary"', start)
        cls.pipeline = source[start : end + 1]

    def test_generated_and_unicode_password_bytes_round_trip(self):
        key = bytes([125, 137, 82, 35, 210, 188, 221, 234, 163, 185, 31])
        for password in ["admin", secrets.token_hex(18), "tést-password", "x" * 11, "x" * 12]:
            with self.subTest(length=len(password)):
                result = subprocess.run(
                    ["/bin/sh", "-c", "IFS= read -r PASSWORD\n" + self.pipeline],
                    input=(password + "\n").encode(),
                    capture_output=True,
                    check=True,
                )
                encoded = result.stdout
                self.assertEqual(len(encoded) % 12, 0)
                decoded = bytes(value ^ key[index % len(key)] for index, value in enumerate(encoded))
                self.assertEqual(decoded.split(b"\0", 1)[0], password.encode())
                self.assertTrue(decoded.endswith(b"\0"))


if __name__ == "__main__":
    unittest.main()
