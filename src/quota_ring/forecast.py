"""Pace and shortfall estimates for usage windows.

Every provider reports the same three things: how much of a window is spent,
when it resets, and how long it runs. From those, the elapsed fraction of the
window gives an average burn rate, and the burn rate says whether the allowance
will survive to the reset. That needs no stored history, which is why this
module is pure: the history store only sharpens what is computed here.

Kept free of GTK imports so it can be tested without the desktop bindings.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from quota_ring.models import DashboardStatus, UsageWindow

# Pace divides spend by elapsed time, so it says nothing while the denominator
# is still tiny: one percent burned two minutes into a weekly window reads as a
# 500x overrun. Stay quiet below this much of the window.
MIN_ELAPSED_FRACTION = 0.05
# Past this much elapsed, the estimate is steady enough to show plainly.
CONFIDENT_ELAPSED_FRACTION = 0.15
# Treat pace this close to 1.0 as "on pace", so the verdict does not flap
# between refreshes when spend is tracking the window almost exactly.
PACE_TOLERANCE = 0.05

UNKNOWN = "unknown"
EARLY = "early"
IDLE = "idle"
UNDER = "under"
ON = "on"
OVER = "over"
SPENT = "spent"


@dataclass(frozen=True)
class Forecast:
    """What one usage window is on track to do before it resets."""

    provider: str
    display_name: str
    window: UsageWindow
    now: datetime
    start: datetime | None = None
    reset: datetime | None = None
    elapsed_fraction: float | None = None
    pace: float | None = None
    exhaustion: datetime | None = None

    @property
    def state(self) -> str:
        if self.start is None or self.reset is None:
            return UNKNOWN
        if self.window.used_percent >= 100:
            return SPENT
        if self.pace is None:
            return EARLY
        if self.pace <= 0:
            return IDLE
        if self.pace > 1 + PACE_TOLERANCE:
            return OVER
        if self.pace < 1 - PACE_TOLERANCE:
            return UNDER
        return ON

    @property
    def confident(self) -> bool:
        """Whether enough of the window has run to state this without a caveat."""
        return (
            self.elapsed_fraction is not None
            and self.elapsed_fraction >= CONFIDENT_ELAPSED_FRACTION
        )

    @property
    def shortfall(self) -> timedelta | None:
        """How long before the reset the allowance runs out. Negative if it survives."""
        if self.exhaustion is None or self.reset is None:
            return None
        return self.reset - self.exhaustion

    @property
    def at_risk(self) -> bool:
        return self.state in (OVER, SPENT)

    @property
    def projected_used_at_reset(self) -> int | None:
        """Spend the window lands on if the current rate holds. Can exceed 100."""
        if self.pace is None:
            return None
        return round(self.pace * 100)

    @property
    def headline(self) -> str:
        state = self.state
        if state == UNKNOWN:
            return "No reset time reported"
        if state == SPENT:
            return "Allowance spent"
        if state == EARLY:
            return "Too early in the window to judge"
        if state == IDLE:
            return "Unused so far"
        if state == OVER:
            shortfall = self.shortfall
            if shortfall is None or shortfall.total_seconds() <= 0:
                return "Running ahead of the window"
            return f"Runs out {format_duration(shortfall)} early"
        leftover = self.projected_used_at_reset
        if leftover is None:
            return "On pace"
        spare = max(0, 100 - leftover)
        if state == ON:
            return "On pace to finish the window exactly"
        return f"On pace to finish with {spare}% left"


def forecast_window(
    provider: str,
    display_name: str,
    window: UsageWindow,
    now: datetime | None = None,
) -> Forecast:
    current = now or datetime.now().astimezone()
    start = window.window_start
    reset = window.reset_datetime
    base = Forecast(
        provider=provider,
        display_name=display_name,
        window=window,
        now=current,
        start=start,
        reset=reset,
    )
    if start is None or reset is None:
        return base
    total = (reset - start).total_seconds()
    if total <= 0:
        return replace(base, start=None, reset=None)

    elapsed = (current - start).total_seconds()
    fraction = min(1.0, max(0.0, elapsed / total))
    base = replace(base, elapsed_fraction=fraction)

    if window.used_percent >= 100:
        # Already gone; the reset is the only thing left to wait for.
        return replace(base, exhaustion=current)
    if fraction < MIN_ELAPSED_FRACTION:
        return base

    pace = (window.used_percent / 100) / fraction
    base = replace(base, pace=pace)
    if pace <= 0:
        return base
    # Spend grows at the average rate, so it reaches 100% after total/pace.
    return replace(base, exhaustion=start + timedelta(seconds=total / pace))


def forecast_status(
    status: DashboardStatus, now: datetime | None = None
) -> list[Forecast]:
    """A forecast for every window of every provider that reported one."""
    current = now or datetime.now().astimezone()
    return [
        forecast_window(provider.provider, provider.display_name, window, current)
        for provider in status.providers
        if provider.available
        for window in provider.windows
    ]


def earliest_shortfall(forecasts: list[Forecast]) -> Forecast | None:
    """The window that runs dry soonest.

    Deliberately not the window with the least left: a 5-hour window at 60%
    remaining but burning hard runs out long before a weekly one sitting at
    20%, and it is the earlier of the two that the user needs to hear about.
    """
    at_risk = [
        forecast
        for forecast in forecasts
        if forecast.at_risk and forecast.exhaustion is not None
    ]
    if not at_risk:
        return None
    return min(at_risk, key=lambda forecast: forecast.exhaustion)  # type: ignore[arg-type,return-value]


def normalize_points(
    points: list[tuple[datetime, int]],
    start: datetime,
    reset: datetime,
) -> list[tuple[float, float]]:
    """Map observations onto the unit square for plotting.

    x is the fraction of the window elapsed, y the fraction of the allowance
    spent, so the diagonal from (0, 0) to (1, 1) is exactly on-pace spending.
    """
    total = (reset - start).total_seconds()
    if total <= 0:
        return []
    return [
        (
            min(1.0, max(0.0, (at - start).total_seconds() / total)),
            min(1.0, max(0.0, used / 100)),
        )
        for at, used in points
    ]


def format_duration(delta: timedelta) -> str:
    seconds = int(abs(delta).total_seconds())
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"
