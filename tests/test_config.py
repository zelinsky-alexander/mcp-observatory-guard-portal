from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mcp_portal.config import AnalysisConfig, Config, ConfigurationError


class ConfigTests(unittest.TestCase):
    def test_public_readonly_rejects_local_feature_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            with self.assertRaisesRegex(ConfigurationError, "conflicts"):
                Config(
                    database_path=root / "catalog.sqlite",
                    mode="public-readonly",
                    analysis=AnalysisConfig(
                        root / "jobs.sqlite",
                        root / "observatory",
                        root / "rules.json",
                        root / "evidence",
                    ),
                )

    def test_public_readonly_environment_fails_closed_on_feature_flag(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            database = Path(raw_root) / "catalog.sqlite"
            database.touch()
            environment = {
                "MCP_PORTAL_DATABASE": str(database),
                "MCP_PORTAL_MODE": "public-readonly",
                "MCP_PORTAL_ENABLE_REVIEW": "1",
            }
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(ConfigurationError, "conflicts"):
                    Config.from_env()

    def test_non_loopback_binding_requires_public_readonly_mode(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            database = Path(raw_root) / "catalog.sqlite"
            database.touch()
            environment = {
                "MCP_PORTAL_DATABASE": str(database),
                "MCP_PORTAL_HOST": "0.0.0.0",
            }
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(
                    ConfigurationError, "requires MCP_PORTAL_MODE=public-readonly"
                ):
                    Config.from_env()

            environment["MCP_PORTAL_MODE"] = "public-readonly"
            with patch.dict(os.environ, environment, clear=True):
                config = Config.from_env()
            self.assertTrue(config.public_readonly)
            self.assertEqual(config.host, "0.0.0.0")


if __name__ == "__main__":
    unittest.main()
