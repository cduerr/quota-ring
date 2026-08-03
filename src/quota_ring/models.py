from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class UsageWindow:
    name: str
    used_percent: int
    resets_at: int | None = None
    duration_minutes: int | None = None
    reset_text: str | None = None

    @property
    def remaining_percent(self) -> int:
        return max(0, min(100, 100 - self.used_percent))

    @property
    def reset_datetime(self) -> datetime | None:
        if self.resets_at is None:
            return None
        return datetime.fromtimestamp(self.resets_at).astimezone()


@dataclass(frozen=True)
class ProviderStatus:
    provider: str
    display_name: str
    windows: tuple[UsageWindow, ...] = ()
    plan_type: str | None = None
    unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.windows) and self.unavailable_reason is None

    @property
    def remaining_percent(self) -> int | None:
        if not self.available:
            return None
        return min(window.remaining_percent for window in self.windows)

    @classmethod
    def codex_response(cls, response: dict[str, Any]) -> ProviderStatus:
        snapshots = response.get("rateLimitsByLimitId") or {}
        snapshot = snapshots.get("codex") if isinstance(snapshots, dict) else None
        if not isinstance(snapshot, dict):
            snapshot = response.get("rateLimits")
        if not isinstance(snapshot, dict):
            raise ValueError("Codex returned no rate-limit information")

        windows: list[UsageWindow] = []
        for key, fallback_name in (("primary", "Primary"), ("secondary", "Secondary")):
            raw = snapshot.get(key)
            if not isinstance(raw, dict) or "usedPercent" not in raw:
                continue
            duration = _optional_int(raw.get("windowDurationMins"))
            windows.append(
                UsageWindow(
                    name=_window_name(duration, fallback_name),
                    used_percent=int(raw["usedPercent"]),
                    resets_at=_optional_int(raw.get("resetsAt")),
                    duration_minutes=duration,
                )
            )
        if not windows:
            raise ValueError("Codex returned no active usage windows")
        return cls(
            provider="codex",
            display_name="Codex",
            windows=tuple(windows),
            plan_type=_optional_str(snapshot.get("planType")),
        )


@dataclass(frozen=True)
class DashboardStatus:
    providers: tuple[ProviderStatus, ...]

    @property
    def remaining_percent(self) -> int | None:
        remaining = [
            status.remaining_percent
            for status in self.providers
            if status.remaining_percent is not None
        ]
        return min(remaining) if remaining else None


def icon_state(remaining_percent: int | None) -> str:
    if remaining_percent is None:
        return "unknown"
    if remaining_percent <= 15:
        return "red"
    if remaining_percent <= 25:
        return "orange"
    if remaining_percent <= 40:
        return "yellow"
    return "green"


def icon_name(remaining_percent: int | None) -> str:
    if remaining_percent is not None and 0 <= remaining_percent <= 5:
        return f"quota-ring-critical-{remaining_percent}-red"
    if remaining_percent is not None and 6 <= remaining_percent < 10:
        return f"quota-ring-low-{remaining_percent}"
    return f"quota-ring-{icon_state(remaining_percent)}"


def refresh_interval(
    remaining_percent: int | None, normal_seconds: int, low_seconds: int
) -> int:
    return (
        low_seconds
        if remaining_percent is not None and remaining_percent < 5
        else normal_seconds
    )


def _window_name(minutes: int | None, fallback: str) -> str:
    if minutes is None:
        return fallback
    if minutes % (7 * 24 * 60) == 0:
        weeks = minutes // (7 * 24 * 60)
        return f"{weeks}-week" if weeks != 1 else "Weekly"
    if minutes % (24 * 60) == 0:
        days = minutes // (24 * 60)
        return f"{days}-day" if days != 1 else "Daily"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours}-hour"
    return f"{minutes}-minute"


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None
