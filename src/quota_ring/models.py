from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
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
        # A plan can carry several independent limits (the base one plus
        # per-model buckets such as GPT-5.3-Codex-Spark). Every one of them can
        # run out on its own, so all of them count towards the reading.
        snapshots = response.get("rateLimitsByLimitId")
        entries: list[tuple[str | None, dict[str, Any]]] = []
        if isinstance(snapshots, dict):
            entries = [
                (_optional_str(raw.get("limitName")), raw)
                for raw in snapshots.values()
                if isinstance(raw, dict)
            ]
        if not entries:
            fallback = response.get("rateLimits")
            if isinstance(fallback, dict):
                entries = [(None, fallback)]
        if not entries:
            raise ValueError("Codex returned no rate-limit information")
        # The base plan limit carries no name; keep it first so it heads the
        # menu regardless of the order the payload happened to use.
        entries.sort(key=lambda entry: entry[0] is not None)

        windows: list[UsageWindow] = []
        plan_type: str | None = None
        for limit_name, snapshot in entries:
            plan_type = plan_type or _optional_str(snapshot.get("planType"))
            for key, fallback_name in (
                ("primary", "Primary"),
                ("secondary", "Secondary"),
            ):
                raw = snapshot.get(key)
                if not isinstance(raw, dict) or "usedPercent" not in raw:
                    continue
                duration = _optional_int(raw.get("windowDurationMins"))
                window_name = _window_name(duration, fallback_name)
                windows.append(
                    UsageWindow(
                        name=_named_limit_window(limit_name, window_name),
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
            plan_type=plan_type,
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


def reset_description(window: UsageWindow, now: datetime | None = None) -> str | None:
    """Return a compact reset label suited to the indicator menu."""
    current = now or datetime.now().astimezone()
    reset = window.reset_datetime
    if reset is None:
        if not window.reset_text:
            return None
        parsed = _parse_reset_text(window.reset_text, current)
        return (
            _format_reset_datetime(parsed, current)
            if parsed is not None
            else window.reset_text
        )
    current = current.astimezone(reset.tzinfo)
    if reset <= current:
        return None
    return _format_reset_datetime(reset, current)


def _format_reset_datetime(reset: datetime, current: datetime) -> str:
    days = (reset.date() - current.date()).days
    pattern = "%a %-I:%M%p" if 0 <= days < 7 else "%b %-d %-I:%M%p"
    return reset.strftime(pattern).replace("AM", "am").replace("PM", "pm")


def _parse_reset_text(value: str, current: datetime) -> datetime | None:
    text = value.strip()
    time_match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)", text, re.IGNORECASE)
    if time_match:
        candidate = _with_time(current, *time_match.groups())
        return candidate if candidate > current else candidate + timedelta(days=1)

    date_match = re.fullmatch(
        r"([A-Za-z]+)\s+(\d{1,2}),?\s+"
        r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)",
        text,
        re.IGNORECASE,
    )
    if not date_match:
        return None
    month_name, day, hour, minute, meridiem = date_match.groups()
    month = None
    for pattern in ("%b", "%B"):
        try:
            month = datetime.strptime(month_name.title(), pattern).month
            break
        except ValueError:
            continue
    if month is None:
        return None
    try:
        candidate = _with_time(
            current.replace(month=month, day=int(day)), hour, minute, meridiem
        )
        if candidate <= current:
            candidate = candidate.replace(year=candidate.year + 1)
    except ValueError:
        return None
    return candidate


def _with_time(
    value: datetime, hour: str, minute: str | None, meridiem: str
) -> datetime:
    parsed_hour = int(hour) % 12
    if meridiem.lower() == "pm":
        parsed_hour += 12
    return value.replace(
        hour=parsed_hour,
        minute=int(minute or 0),
        second=0,
        microsecond=0,
    )


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


def _named_limit_window(limit_name: str | None, window_name: str) -> str:
    if not limit_name:
        return window_name
    codex_version = re.search(
        r"(?:GPT-)?(\d+(?:\.\d+)+)-Codex", limit_name, re.IGNORECASE
    )
    if codex_version:
        return f"{window_name} ({codex_version.group(1)})"
    return f"{window_name} ({limit_name})"


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None
