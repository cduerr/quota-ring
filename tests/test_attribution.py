import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quota_ring.attribution import _quota_samples, attribute_models
from quota_ring.codex_usage import TokenUsage, read_token_usage
from quota_ring.history import Observation

START = datetime(2026, 9, 20, tzinfo=timezone.utc)


def observation(minutes, used):
    return Observation(
        "codex", "Weekly", "reset", START + timedelta(minutes=minutes), used
    )


def usage(minutes, model, score, quota=None):
    return TokenUsage(
        f"{model}-{minutes}",
        START + timedelta(minutes=minutes),
        model,
        score,
        0,
        0,
        quota,
    )


class AttributionTests(unittest.TestCase):
    def test_isolated_steps_infer_relative_model_rates(self):
        observations = [
            observation(10, 1),
            observation(20, 2),
            observation(30, 3),
            observation(40, 4),
            observation(50, 6),
        ]
        usages = [
            usage(5, "sol", 100),
            usage(15, "sol", 100),
            usage(25, "astra", 50),
            usage(35, "astra", 50),
            usage(45, "sol", 100),
            usage(46, "astra", 50),
        ]
        result = attribute_models(observations, usages, START)
        self.assertEqual(result.method, "observed rates")
        self.assertEqual(result.inferred_models, ("astra", "sol"))
        self.assertAlmostEqual(result.points[-1].layers["sol"], 3)
        self.assertAlmostEqual(result.points[-1].layers["astra"], 3)
        self.assertAlmostEqual(result.points[-1].total, 6)

    def test_sparse_evidence_falls_back_to_weighted_token_share(self):
        observations = [observation(10, 1), observation(20, 2)]
        usages = [usage(5, "sol", 100), usage(15, "astra", 50)]
        result = attribute_models(observations, usages, START)
        self.assertEqual(result.method, "token share")
        self.assertEqual(result.inferred_models, ())
        self.assertEqual(result.points[-1].layers, {"sol": 1.0, "astra": 1.0})

    def test_paired_local_quota_snapshots_drive_rate_inference(self):
        observations = [observation(100, 2), observation(200, 4)]
        usages = [
            usage(5, "sol", 0, 0),
            usage(10, "sol", 100, 1),
            usage(15, "sol", 100, 2),
            usage(20, "astra", 0, 2),
            usage(25, "astra", 50, 3),
            usage(30, "astra", 50, 4),
        ]
        result = attribute_models(observations, usages, START)
        self.assertEqual(result.method, "observed rates")
        self.assertEqual(result.isolated_steps, 4)
        self.assertEqual(result.inferred_models, ("astra", "sol"))

    def test_multi_point_jump_is_not_charged_to_next_local_model(self):
        usages = [
            usage(5, "sol", 0, 0),
            usage(10, "sol", 10, 5),
            usage(15, "sol", 100, 6),
            usage(20, "sol", 100, 7),
        ]
        samples = _quota_samples(usages)
        self.assertEqual(len(samples["sol"]), 2)
        self.assertEqual(sum(change for change, _score in samples["sol"]), 2)

    def test_quota_step_without_local_tokens_is_kept_visible(self):
        result = attribute_models([observation(10, 2)], [], START)
        self.assertEqual(result.points[-1].layers, {"unattributed": 2.0})


class CodexLogTests(unittest.TestCase):
    def test_token_count_supplies_quota_without_double_counting(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            session_dir = home / "sessions" / "2026" / "09" / "20"
            session_dir.mkdir(parents=True)
            path = session_dir / "rollout.jsonl"
            records = [
                {"type": "turn_context", "payload": {"model": "gpt-test"}},
                {
                    "timestamp": "2026-09-20T00:05:00Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "rate_limits": {
                            "limit_id": "codex",
                            "primary": {"used_percent": 12.0},
                        },
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 100,
                                "cached_input_tokens": 20,
                                "output_tokens": 10,
                            }
                        },
                    },
                },
                {
                    "timestamp": "2026-09-20T00:05:00Z",
                    "type": "token_usage_record",
                    "payload": {
                        "usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 20,
                            "output_tokens": 10,
                        }
                    },
                },
            ]
            path.write_text("".join(json.dumps(record) + "\n" for record in records))
            events = read_token_usage(
                START, START + timedelta(hours=1), codex_home=home
            )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].model, "gpt-test")
        self.assertEqual(events[0].weighted_tokens, 132)
        self.assertEqual(events[0].quota_percent, 12)

    def test_resumed_thread_is_read_from_its_original_date_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            session_dir = home / "sessions" / "2026" / "09" / "17"
            session_dir.mkdir(parents=True)
            path = session_dir / "rollout.jsonl"
            records = [
                {
                    "timestamp": "2026-09-17T12:00:00Z",
                    "ordinal": 0,
                    "type": "turn_context",
                    "payload": {"model": "gpt-6-astra"},
                },
                {
                    "timestamp": "2026-09-20T00:05:00Z",
                    "ordinal": 1,
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 100,
                                "cached_input_tokens": 0,
                                "output_tokens": 0,
                            }
                        },
                    },
                },
            ]
            path.write_text("".join(json.dumps(record) + "\n" for record in records))
            events = read_token_usage(
                START, START + timedelta(hours=1), codex_home=home
            )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].model, "gpt-6-astra")
        self.assertTrue(events[0].source_key.endswith(":2"))


if __name__ == "__main__":
    unittest.main()
