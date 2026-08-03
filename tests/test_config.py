import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from quota_ring.config import Config


class ConfigTests(unittest.TestCase):
    def test_config_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = Config(
                codex_command="/opt/codex",
                poll_seconds=120,
                low_poll_seconds=60,
                path=path,
            )
            config.save()
            self.assertEqual(
                json.loads(path.read_text())["codex_command"], "/opt/codex"
            )
            self.assertEqual(Config.load(path).poll_seconds, 120)
            self.assertEqual(Config.load(path).low_poll_seconds, 60)
            self.assertTrue(Config.load(path).kimi_enabled)

    def test_low_usage_refresh_defaults_to_90_seconds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            self.assertEqual(Config.load(path).low_poll_seconds, 90)

    def test_legacy_config_is_loaded_but_saves_to_quota_ring_path(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            legacy = home / ".config" / "codex-usage-indicator" / "config.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text('{"poll_seconds": 120}\n')
            with patch.dict(os.environ, {}, clear=True), patch(
                "pathlib.Path.home", return_value=home
            ):
                config = Config.load()
            self.assertEqual(config.poll_seconds, 120)
            self.assertEqual(
                config.path, home / ".config" / "quota-ring" / "config.json"
            )
