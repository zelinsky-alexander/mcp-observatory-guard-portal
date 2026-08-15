from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


class StorageV2SidecarScriptTests(unittest.TestCase):
    def test_installer_has_valid_bash_syntax(self) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "install_storage_v2_sidecar_portal.sh"
        )
        result = subprocess.run(
            ["bash", "-n", str(script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
