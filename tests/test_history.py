import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quota_ring.history import HistoryStore
from quota_ring.models import DashboardStatus, ProviderStatus, UsageWindow

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
SERIES = ("codex", "Weekly")


def at(**offset):
    return NOW + timedelta(**offset)


def percents(observations):
    return [observation.used_percent for observation in observations]


def status(used, resets_at=1000, name="Weekly", unavailable=None):
    if unavailable:
        return DashboardStatus(
            (ProviderStatus("codex", "Codex", unavailable_reason=unavailable),)
        )
    window = UsageWindow(
        name, used, resets_at=resets_at, duration_minutes=10080
    )
    return DashboardStatus(
        (ProviderStatus("codex", "Codex", windows=(window,)),)
    )


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.history = HistoryStore(memory=True)
        self.addCleanup(self.history.close)

    def test_only_changes_are_stored(self):
        self.assertEqual(self.history.record(status(10), NOW), 1)
        self.assertEqual(self.history.record(status(10), at(minutes=5)), 0)
        self.assertEqual(self.history.record(status(10), at(minutes=10)), 0)
        self.assertEqual(self.history.record(status(11), at(minutes=15)), 1)
        self.assertEqual(percents(self.history.current_series(*SERIES)), [10, 11])

    def test_heartbeat_advances_even_when_nothing_changed(self):
        self.history.record(status(10), NOW)
        later = at(minutes=30)
        self.history.record(status(10), later)
        # Without this a flat stretch is indistinguishable from downtime.
        self.assertEqual(self.history.last_seen(*SERIES), later.astimezone())

    def test_a_new_reset_time_starts_a_new_instance(self):
        self.history.record(status(90, resets_at=1000), NOW)
        self.history.record(status(2, resets_at=2000), at(hours=1))
        self.assertEqual(self.history.current_instance_key(*SERIES), "2000")
        # The old instance keeps its own readings.
        self.assertEqual(percents(self.history.series(*SERIES, "1000")), [90])
        self.assertEqual(percents(self.history.current_series(*SERIES)), [2])

    def test_a_drop_starts_a_new_instance_without_a_reset_time(self):
        # Claude reports no reset timestamp, so a fall in spend is the only
        # evidence that the window rolled over.
        self.history.record(status(90, resets_at=None), NOW)
        first = self.history.current_instance_key(*SERIES)
        self.history.record(status(91, resets_at=None), at(minutes=5))
        self.assertEqual(self.history.current_instance_key(*SERIES), first)
        self.history.record(status(3, resets_at=None), at(minutes=10))
        self.assertNotEqual(self.history.current_instance_key(*SERIES), first)

    def test_unavailable_providers_are_not_recorded(self):
        unavailable = status(0, unavailable="Not logged in")
        self.assertEqual(self.history.record(unavailable, NOW), 0)
        self.assertIsNone(self.history.last_seen(*SERIES))


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.history = HistoryStore(memory=True)
        self.addCleanup(self.history.close)

    def test_recent_instances_report_peak_spend_oldest_first(self):
        runs = ((1000, (10, 40)), (2000, (5, 95)), (3000, (20, 30)))
        for index, (reset, peaks) in enumerate(runs):
            for offset, used in enumerate(peaks):
                self.history.record(
                    status(used, resets_at=reset), at(hours=index * 10 + offset)
                )
        instances = self.history.recent_instances(*SERIES)
        self.assertEqual(
            [(one.instance_key, one.peak_used_percent) for one in instances],
            [("1000", 40), ("2000", 95), ("3000", 30)],
        )

    def test_recent_instances_honour_the_limit(self):
        for reset in range(1000, 1010):
            self.history.record(status(5, resets_at=reset), NOW)
        instances = self.history.recent_instances(*SERIES, limit=3)
        self.assertEqual(len(instances), 3)

    def test_series_of_an_unknown_window_is_empty(self):
        self.assertEqual(self.history.current_series("kimi", "Weekly"), [])
        self.assertIsNone(self.history.last_seen("kimi", "Weekly"))


class RetentionTests(unittest.TestCase):
    def test_prune_drops_only_readings_past_the_retention_window(self):
        history = HistoryStore(memory=True, retention_days=30)
        self.addCleanup(history.close)
        history.record(status(5, resets_at=1000), at(days=-60))
        history.record(status(6, resets_at=2000), at(days=-10))
        self.assertEqual(history.prune(NOW), 1)
        self.assertEqual(
            [one.instance_key for one in history.recent_instances(*SERIES)],
            ["2000"],
        )

    def test_clear_removes_everything(self):
        history = HistoryStore(memory=True)
        self.addCleanup(history.close)
        history.record(status(5), NOW)
        history.clear()
        self.assertIsNone(history.last_seen(*SERIES))
        self.assertEqual(history.current_series(*SERIES), [])


class FileTests(unittest.TestCase):
    def test_history_file_is_private_and_survives_reopening(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "history.db"
            history = HistoryStore(path=path)
            history.record(status(42), NOW)
            history.close()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

            reopened = HistoryStore(path=path)
            self.addCleanup(reopened.close)
            self.assertEqual(percents(reopened.current_series(*SERIES)), [42])


if __name__ == "__main__":
    unittest.main()
