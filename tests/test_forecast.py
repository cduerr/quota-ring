import unittest
from datetime import datetime, timedelta, timezone

from quota_ring.forecast import (
    EARLY,
    IDLE,
    ON,
    OVER,
    SPENT,
    UNDER,
    UNKNOWN,
    earliest_shortfall,
    forecast_status,
    forecast_window,
    format_duration,
    normalize_points,
)
from quota_ring.models import DashboardStatus, ProviderStatus, UsageWindow

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


def window(used, resets_in, duration_minutes, name="Weekly"):
    return UsageWindow(
        name=name,
        used_percent=used,
        resets_at=int((NOW + resets_in).timestamp()),
        duration_minutes=duration_minutes,
    )


def forecast(used, resets_in, duration_minutes=10080, name="Weekly"):
    return forecast_window(
        "codex", "Codex", window(used, resets_in, duration_minutes, name), NOW
    )


class PaceTests(unittest.TestCase):
    def test_pace_is_spend_over_elapsed_fraction(self):
        # Half the week gone, three quarters spent.
        result = forecast(75, timedelta(days=3.5))
        self.assertAlmostEqual(result.elapsed_fraction, 0.5)
        self.assertAlmostEqual(result.pace, 1.5)
        self.assertEqual(result.state, OVER)
        self.assertEqual(result.projected_used_at_reset, 150)

    def test_exhaustion_lands_before_the_reset_when_over_pace(self):
        result = forecast(75, timedelta(days=3.5))
        # At 1.5x the clock the allowance lasts two thirds of the window.
        start = NOW - timedelta(days=3.5)
        self.assertEqual(result.exhaustion, start + timedelta(days=7) / 1.5)
        self.assertGreater(result.shortfall, timedelta(0))
        self.assertTrue(result.at_risk)

    def test_under_pace_survives_the_window(self):
        result = forecast(25, timedelta(days=3.5))
        self.assertAlmostEqual(result.pace, 0.5)
        self.assertEqual(result.state, UNDER)
        self.assertLess(result.shortfall, timedelta(0))
        self.assertFalse(result.at_risk)
        self.assertIn("50% left", result.headline)

    def test_spending_in_step_with_the_clock_reads_as_on_pace(self):
        self.assertEqual(forecast(50, timedelta(days=3.5)).state, ON)
        # A couple of points either side stays on pace rather than flapping.
        self.assertEqual(forecast(52, timedelta(days=3.5)).state, ON)
        self.assertEqual(forecast(48, timedelta(days=3.5)).state, ON)
        self.assertEqual(forecast(60, timedelta(days=3.5)).state, OVER)

    def test_pace_is_withheld_early_in_the_window(self):
        # One percent two minutes in would otherwise read as a huge overrun.
        result = forecast(1, timedelta(days=7) - timedelta(minutes=2))
        self.assertIsNone(result.pace)
        self.assertIsNone(result.exhaustion)
        self.assertEqual(result.state, EARLY)
        self.assertFalse(result.confident)

    def test_unused_window_reports_no_exhaustion(self):
        result = forecast(0, timedelta(days=3.5))
        self.assertEqual(result.state, IDLE)
        self.assertIsNone(result.exhaustion)

    def test_spent_window_is_already_out(self):
        result = forecast(100, timedelta(days=3.5))
        self.assertEqual(result.state, SPENT)
        self.assertEqual(result.exhaustion, NOW)
        self.assertTrue(result.at_risk)

    def test_missing_duration_or_reset_yields_no_forecast(self):
        no_duration = forecast_window(
            "claude", "Claude Code", UsageWindow("5-hour", 50, resets_at=1), NOW
        )
        no_reset = forecast_window(
            "claude",
            "Claude Code",
            UsageWindow("5-hour", 50, duration_minutes=300),
            NOW,
        )
        for result in (no_duration, no_reset):
            self.assertEqual(result.state, UNKNOWN)
            self.assertIsNone(result.pace)
            self.assertEqual(result.headline, "No reset time reported")


class EarliestShortfallTests(unittest.TestCase):
    def test_picks_the_soonest_exhaustion_not_the_lowest_remaining(self):
        # The weekly window has far less left, but the session window burns
        # out first and is what the user needs to hear about.
        # Half of each window gone: the session is at 1.2x, the week at 1.8x.
        session = forecast(60, timedelta(hours=2.5), 300, name="5-hour")
        weekly = forecast(90, timedelta(days=3.5))
        risk = earliest_shortfall([weekly, session])
        self.assertIsNotNone(risk)
        self.assertEqual(risk.window.name, "5-hour")
        self.assertLess(
            weekly.window.remaining_percent, session.window.remaining_percent
        )

    def test_returns_nothing_when_every_window_lasts(self):
        self.assertIsNone(earliest_shortfall([forecast(25, timedelta(days=3.5))]))

    def test_unavailable_providers_are_skipped(self):
        weekly = window(75, timedelta(days=3.5), 10080)
        status = DashboardStatus(
            (
                ProviderStatus("codex", "Codex", windows=(weekly,)),
                ProviderStatus("kimi", "Kimi", unavailable_reason="Not logged in"),
            )
        )
        results = forecast_status(status, NOW)
        self.assertEqual([result.provider for result in results], ["codex"])


class NormalizeTests(unittest.TestCase):
    def test_maps_observations_onto_the_unit_square(self):
        start = NOW
        reset = NOW + timedelta(days=10)
        points = normalize_points(
            [(NOW + timedelta(days=5), 40), (NOW + timedelta(days=10), 90)],
            start,
            reset,
        )
        self.assertEqual(points, [(0.5, 0.4), (1.0, 0.9)])

    def test_clamps_readings_outside_the_window(self):
        points = normalize_points(
            [(NOW - timedelta(days=1), 150)], NOW, NOW + timedelta(days=1)
        )
        self.assertEqual(points, [(0.0, 1.0)])

    def test_zero_length_window_has_no_points(self):
        self.assertEqual(normalize_points([(NOW, 5)], NOW, NOW), [])


class FormatDurationTests(unittest.TestCase):
    def test_drops_units_that_would_read_as_noise(self):
        long_gap = timedelta(days=2, hours=4, minutes=9)
        self.assertEqual(format_duration(long_gap), "2d 4h")
        self.assertEqual(format_duration(timedelta(days=2)), "2d")
        self.assertEqual(format_duration(timedelta(hours=3, minutes=20)), "3h 20m")
        self.assertEqual(format_duration(timedelta(hours=3)), "3h")
        self.assertEqual(format_duration(timedelta(minutes=27)), "27m")
        self.assertEqual(format_duration(timedelta(minutes=-27)), "27m")


if __name__ == "__main__":
    unittest.main()
