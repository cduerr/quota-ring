import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from quota_ring.activity import QuietHours, learn_quiet_hours
from quota_ring.forecast import forecast_window
from quota_ring.history import HistoryStore
from quota_ring.models import DashboardStatus, ProviderStatus, UsageWindow

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


@unittest.skipUnless(hasattr(time, "tzset"), "requires local timezone support")
class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.zone = patch.dict(os.environ, {"TZ": "UTC"})
        self.zone.start()
        time.tzset()
        self.addCleanup(self.restore_zone)

    def restore_zone(self):
        self.zone.stop()
        time.tzset()

    def activity(self, count=10, quiet=range(3, 8)):
        return [
            (NOW - timedelta(days=day)).replace(hour=hour)
            for day in range(1, count + 1)
            for hour in range(24)
            if hour not in quiet
        ]

    def weekly(self, elapsed, used=2, quiet=None, duration=7 * 24 * 60):
        window = UsageWindow(
            "Weekly",
            used,
            resets_at=int((NOW - elapsed + timedelta(minutes=duration)).timestamp()),
            duration_minutes=duration,
        )
        return forecast_window("codex", "Codex", window, NOW, quiet)

    def test_learns_recurring_quiet_hours_from_completed_days(self):
        self.assertEqual(learn_quiet_hours(self.activity(), NOW), QuietHours(3, 5, 9))

    def test_sparse_or_partial_days_do_not_establish_quiet_hours(self):
        self.assertIsNone(learn_quiet_hours(self.activity(7), NOW))
        sparse = [NOW - timedelta(days=day) for day in range(1, 15)]
        self.assertIsNone(learn_quiet_hours(sparse, NOW))

    def test_cross_midnight_quiet_hours_are_one_stretch(self):
        quiet = [22, 23, 0, 1, 2, 3, 4, 5]
        self.assertEqual(
            learn_quiet_hours(self.activity(quiet=quiet), NOW), QuietHours(22, 8, 9)
        )

    def test_days_with_all_day_activity_have_no_quiet_pattern(self):
        self.assertIsNone(learn_quiet_hours(self.activity(quiet=[]), NOW))

    def test_one_busy_night_does_not_erase_recurring_quiet_hours(self):
        activity = self.activity()
        activity.extend((NOW - timedelta(days=1)).replace(hour=h) for h in range(3, 8))
        self.assertEqual(learn_quiet_hours(activity, NOW), QuietHours(3, 5, 9))

    def test_early_active_sample_is_discounted_for_missing_quiet_hours(self):
        elapsed = timedelta(hours=2)
        raw = self.weekly(elapsed)
        adjusted = self.weekly(elapsed, quiet=QuietHours(3, 5, 9))
        self.assertIsNotNone(adjusted.quiet_hours)
        self.assertLess(adjusted.pace, raw.pace)
        self.assertAlmostEqual(adjusted.pace / raw.pace, 19.5 / 24)
        self.assertLess(raw.exhaustion, adjusted.exhaustion)

    def test_full_day_and_short_windows_are_not_adjusted(self):
        quiet = QuietHours(3, 5, 9)
        for elapsed, duration in [
            (timedelta(days=1), 10080),
            (timedelta(hours=2), 300),
        ]:
            raw = self.weekly(elapsed, duration=duration)
            adjusted = self.weekly(elapsed, quiet=quiet, duration=duration)
            self.assertEqual(raw.pace, adjusted.pace)
            self.assertIsNone(adjusted.quiet_hours)

    def test_idle_heavy_sample_is_not_penalized(self):
        quiet = QuietHours(8, 4, 9)
        raw = self.weekly(timedelta(hours=4))
        adjusted = self.weekly(timedelta(hours=4), quiet=quiet)
        self.assertEqual(raw.pace, adjusted.pace)
        self.assertIsNone(adjusted.quiet_hours)

    def test_adjusted_exhaustion_matches_activity_curve(self):
        quiet = QuietHours(3, 5, 9)
        result = self.weekly(timedelta(hours=2), used=2, quiet=quiet)
        work = quiet.work_seconds(result.start, NOW)
        at_exhaustion = quiet.work_seconds(result.start, result.exhaustion)
        self.assertAlmostEqual(at_exhaustion * 0.02 / work, 1, places=6)

    def test_history_uses_codex_activity_and_excludes_auto_review(self):
        history = HistoryStore(memory=True)
        self.addCleanup(history.close)
        for index, at in enumerate(self.activity()):
            history._connection.execute(
                "INSERT INTO codex_token_usage VALUES (?, ?, ?, 100, 0, 10, NULL)",
                (str(index), int(at.timestamp()), "gpt-6.1-sol"),
            )
        for index, at in enumerate(self.activity(quiet=[])):
            history._connection.execute(
                "INSERT INTO codex_token_usage VALUES (?, ?, ?, 100, 0, 10, NULL)",
                (f"review-{index}", int(at.timestamp()), "codex-auto-review"),
            )
        window = self.weekly(timedelta(hours=2)).window
        status = DashboardStatus((ProviderStatus("codex", "Codex", (window,)),))
        self.assertEqual(
            history.forecasts(status, NOW)[0].quiet_hours, QuietHours(3, 5, 9)
        )

    def test_integration_counts_repeated_dst_hour(self):
        os.environ["TZ"] = "America/Chicago"
        time.tzset()
        start = datetime(2026, 11, 1, 0).astimezone()
        end = datetime(2026, 11, 1, 4).astimezone()
        self.assertEqual(QuietHours(0, 4, 9).work_seconds(start, end), 5 * 360)
