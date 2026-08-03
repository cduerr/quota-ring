import unittest

from quota_ring.models import (
    DashboardStatus,
    ProviderStatus,
    icon_name,
    icon_state,
    refresh_interval,
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

    def test_unavailable_provider_does_not_affect_overall(self):
        codex = ProviderStatus.codex_response(
            {"rateLimits": {"primary": {"usedPercent": 94}}}
        )
        claude = ProviderStatus(
            "claude", "Claude Code", unavailable_reason="No active allowance"
        )
        self.assertEqual(DashboardStatus((codex, claude)).remaining_percent, 6)
        self.assertEqual(icon_state(None), "unknown")

    def test_single_digit_remaining_uses_escalating_number_icons(self):
        self.assertEqual(icon_name(0), "quota-ring-critical-0-red")
        self.assertEqual(icon_name(5), "quota-ring-critical-5-red")
        self.assertEqual(icon_name(6), "quota-ring-low-6")
        self.assertEqual(icon_name(9), "quota-ring-low-9")
        self.assertEqual(icon_name(10), "quota-ring-red")

    def test_low_usage_refresh_is_strictly_below_five_percent(self):
        self.assertEqual(refresh_interval(4, 300, 90), 90)
        self.assertEqual(refresh_interval(5, 300, 90), 300)
        self.assertEqual(refresh_interval(None, 300, 90), 300)
