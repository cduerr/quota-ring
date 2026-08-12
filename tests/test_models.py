import unittest
from datetime import datetime, timezone

from quota_ring.models import (
    DashboardStatus,
    ProviderStatus,
    UsageWindow,
    icon_state,
    refresh_interval,
    reset_description,
)


class UsageStatusTests(unittest.TestCase):
    def test_thresholds_are_inclusive(self):
        self.assertEqual(icon_state(15), "red")
        self.assertEqual(icon_state(16), "orange")
        self.assertEqual(icon_state(25), "orange")
        self.assertEqual(icon_state(26), "yellow")
        self.assertEqual(icon_state(40), "yellow")
        self.assertEqual(icon_state(41), "green")

    def test_most_constrained_window_controls_remaining(self):
        status = ProviderStatus.codex_response(
            {
                "rateLimits": {
                    "planType": "plus",
                    "primary": {"usedPercent": 20, "windowDurationMins": 300},
                    "secondary": {"usedPercent": 93, "windowDurationMins": 10080},
                }
            }
        )
        self.assertEqual(status.remaining_percent, 7)
        self.assertEqual(
            [window.name for window in status.windows], ["5-hour", "Weekly"]
        )

    def test_codex_bucket_is_preferred(self):
        status = ProviderStatus.codex_response(
            {
                "rateLimits": {"primary": {"usedPercent": 1}},
                "rateLimitsByLimitId": {"codex": {"primary": {"usedPercent": 70}}},
            }
        )
        self.assertEqual(status.remaining_percent, 30)

    def test_every_limit_id_contributes_a_window(self):
        status = ProviderStatus.codex_response(
            {
                "rateLimitsByLimitId": {
                    "codex": {
                        "limitName": None,
                        "planType": "prolite",
                        "primary": {"usedPercent": 22, "windowDurationMins": 10080},
                    },
                    "codex_bengalfox": {
                        "limitName": "GPT-5.3-Codex-Spark",
                        "primary": {"usedPercent": 96, "windowDurationMins": 10080},
                    },
                }
            }
        )
        self.assertEqual(
            [window.name for window in status.windows],
            ["Weekly", "Weekly (5.3)"],
        )
        # The exhausted per-model bucket is what constrains the plan.
        self.assertEqual(status.remaining_percent, 4)
        self.assertEqual(status.plan_type, "prolite")

    def test_unavailable_provider_does_not_affect_overall(self):
        codex = ProviderStatus.codex_response(
            {"rateLimits": {"primary": {"usedPercent": 94}}}
        )
        claude = ProviderStatus(
            "claude", "Claude Code", unavailable_reason="No active allowance"
        )
        self.assertEqual(DashboardStatus((codex, claude)).remaining_percent, 6)
        self.assertEqual(icon_state(None), "unknown")

    def test_low_usage_refresh_is_strictly_below_five_percent(self):
        self.assertEqual(refresh_interval(4, 300, 90), 90)
        self.assertEqual(refresh_interval(5, 300, 90), 300)
        self.assertEqual(refresh_interval(None, 300, 90), 300)

    def test_reset_description_compacts_dates(self):
        now = datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc)
        today = UsageWindow("Today", 0, resets_at=1786554000)
        soon = UsageWindow("Soon", 0, resets_at=1786726800)
        later = UsageWindow("Later", 0, resets_at=1787158800)
        expired = UsageWindow("Expired", 0, resets_at=1786467600)
        relative = UsageWindow("Relative", 0, reset_text="in 2h")
        self.assertEqual(reset_description(today, now), "Wed 12:00pm")
        self.assertEqual(reset_description(soon, now), "Fri 12:00pm")
        self.assertEqual(reset_description(later, now), "Aug 19 12:00pm")
        self.assertIsNone(reset_description(expired, now))
        self.assertEqual(reset_description(relative, now), "in 2h")

    def test_reset_description_normalizes_provider_text(self):
        now = datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(
            reset_description(UsageWindow("Time", 0, reset_text="5pm"), now),
            "Wed 5:00pm",
        )
        self.assertEqual(
            reset_description(UsageWindow("Date", 0, reset_text="Aug 14, 3pm"), now),
            "Fri 3:00pm",
        )
        self.assertEqual(
            reset_description(UsageWindow("Far", 0, reset_text="Aug 23, 6pm"), now),
            "Aug 23 6:00pm",
        )
