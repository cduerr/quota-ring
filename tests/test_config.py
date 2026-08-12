import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_low_usage_refresh_defaults_to_90_seconds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            self.assertEqual(Config.load(path).low_poll_seconds, 90)

    def test_ring_order_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            Config(ring_order=("claude", "codex", "kimi"), path=path).save()
            self.assertEqual(Config.load(path).ring_order, ("claude", "codex", "kimi"))

    def test_ring_order_defaults_when_absent_or_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.assertEqual(Config.load(path).ring_order, ("codex", "kimi", "claude"))
            path.write_text('{"ring_order": "codex"}\n')
            self.assertEqual(Config.load(path).ring_order, ("codex", "kimi", "claude"))

    def test_ring_order_filters_unknown_and_appends_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"ring_order": ["kimi", "bogus", "kimi"]}\n')
            self.assertEqual(Config.load(path).ring_order, ("kimi", "codex", "claude"))

    def test_legacy_config_is_loaded_but_saves_to_quota_ring_path(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            legacy = home / ".config" / "codex-usage-indicator" / "config.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text('{"poll_seconds": 120}\n')
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("pathlib.Path.home", return_value=home),
            ):
                config = Config.load()
            self.assertEqual(config.poll_seconds, 120)
            self.assertEqual(
                config.path, home / ".config" / "quota-ring" / "config.json"
            )
