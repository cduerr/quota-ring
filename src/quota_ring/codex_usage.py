"""Read per-model token usage from local Codex session logs."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


_TIMESTAMP = re.compile(br'^\{"timestamp":"([^"]+)"')
_TURN_CONTEXT = b'"type":"turn_context"'


@dataclass(frozen=True)
class TokenUsage:
    source_key: str
    observed_at: datetime
    model: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    quota_percent: float | None = None

    @property
    def weighted_tokens(self) -> float:
        """Comparable work units before applying a model-specific rate.

        Current Codex rate cards consistently price cached input at one tenth
        of ordinary input and output at roughly five times input. Keeping that
        distinction prevents a long cached conversation from looking ten times
        more expensive than it is. Model-to-model differences are inferred
        separately from quota movement.
        """
        uncached = max(0, self.input_tokens - self.cached_input_tokens)
        return uncached + self.cached_input_tokens * 0.1 + self.output_tokens * 5


def default_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def read_token_usage(
    start: datetime,
    end: datetime,
    codex_home: Path | None = None,
) -> list[TokenUsage]:
    """Read every rollout which may contain events in the requested range.

    A rollout remains in the directory for the day its thread was created.
    Long-running and resumed threads can therefore contain events weeks newer
    than their directory name. File modification time finds those rollouts;
    timestamp seeking keeps old, very large threads inexpensive to revisit.
    """
    home = codex_home or default_codex_home()
    sessions = home / "sessions"
    events: list[TokenUsage] = []
    if not sessions.is_dir():
        return events
    for path in sessions.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime < start.timestamp():
                continue
        except OSError:
            continue
        events.extend(_read_session(path, sessions, start, end))
    events.sort(key=lambda event: event.observed_at)
    return events


def _read_session(
    path: Path,
    sessions_root: Path,
    start: datetime,
    end: datetime,
) -> list[TokenUsage]:
    model = "unknown"
    modern: list[TokenUsage] = []
    legacy: list[TokenUsage] = []
    relative = path.relative_to(sessions_root)
    try:
        lines = path.open("rb")
    except OSError:
        return []
    with lines:
        offset = _seek_to_time(lines, start)
        model = _model_before(lines, offset) or model
        lines.seek(offset)
        line_number = 0 if offset == 0 else None
        while True:
            line_offset = lines.tell()
            line = lines.readline()
            if not line:
                break
            if line_number is not None:
                line_number += 1
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            if record.get("type") == "turn_context":
                value = payload.get("model")
                if isinstance(value, str) and value:
                    model = value
                continue
            usage = None
            kind = None
            if record.get("type") == "token_usage_record":
                usage = payload.get("usage")
                kind = "usage"
            elif (
                record.get("type") == "event_msg"
                and payload.get("type") == "token_count"
            ):
                info = payload.get("info")
                if isinstance(info, dict):
                    usage = info.get("last_token_usage")
                    kind = "count"
            if not isinstance(usage, dict) or kind is None:
                continue
            observed_at = _timestamp(record.get("timestamp"))
            if observed_at is None or observed_at < start or observed_at > end:
                continue
            event = TokenUsage(
                source_key=(
                    f"{kind}:{relative}:"
                    f"{_record_key(record, line_offset, line_number)}"
                ),
                observed_at=observed_at,
                model=model,
                input_tokens=_integer(usage.get("input_tokens")),
                cached_input_tokens=_integer(usage.get("cached_input_tokens")),
                output_tokens=_integer(usage.get("output_tokens")),
                quota_percent=_quota_percent(payload),
            )
            (modern if kind == "usage" else legacy).append(event)
    # Newer logs emit token_usage_record immediately followed by token_count.
    # The latter has the same token totals plus the quota snapshot needed for
    # calibration, so prefer it and still count every model call only once.
    return legacy if legacy else modern


def _record_key(record: dict, offset: int, line_number: int | None) -> str:
    """Use Codex's stable ordinal, matching the old one-based line key."""
    ordinal = record.get("ordinal")
    if isinstance(ordinal, int) and ordinal >= 0:
        return str(ordinal + 1)
    if line_number is not None:
        return str(line_number)
    return f"offset-{offset}"


def _seek_to_time(lines, start: datetime) -> int:
    """Find the first record at or after start in a timestamp-sorted JSONL."""
    lines.seek(0, os.SEEK_END)
    low = 0
    high = lines.tell()
    # Small rollouts are cheaper and more robust to scan normally. This also
    # supports hand-written/legacy records which lack top-level timestamps.
    if high < 16 * 1024 * 1024:
        return 0
    while high - low > 1:
        middle = (low + high) // 2
        lines.seek(middle)
        if middle:
            lines.readline()
        position = lines.tell()
        if position >= high:
            high = middle
            continue
        line = lines.readline()
        if not line:
            high = middle
            continue
        observed_at = _line_timestamp(line)
        if observed_at is not None and observed_at < start:
            low = max(middle + 1, lines.tell())
        else:
            high = position
    lines.seek(low)
    if low:
        lines.readline()
    return lines.tell()


def _line_timestamp(line: bytes) -> datetime | None:
    match = _TIMESTAMP.match(line)
    if match is None:
        return None
    try:
        value = match.group(1).decode("ascii")
    except UnicodeDecodeError:
        return None
    return _timestamp(value)


def _model_before(lines, offset: int) -> str | None:
    """Recover the active model when seeking into the middle of a rollout."""
    end = offset
    overlap = len(_TURN_CONTEXT) - 1
    while end > 0:
        begin = max(0, end - 1024 * 1024)
        lines.seek(begin)
        chunk = lines.read(end - begin)
        search_end = len(chunk)
        while True:
            index = chunk.rfind(_TURN_CONTEXT, 0, search_end)
            if index < 0:
                break
            absolute = begin + index
            line_start = _line_start(lines, absolute)
            lines.seek(line_start)
            try:
                record = json.loads(lines.readline())
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                search_end = index
                continue
            payload = record.get("payload")
            if isinstance(payload, dict):
                model = payload.get("model")
                if isinstance(model, str) and model:
                    return model
            search_end = index
        if begin == 0:
            break
        end = begin + overlap
    return None


def _line_start(lines, position: int) -> int:
    """Find the beginning of the JSONL record containing position."""
    end = position
    while end > 0:
        begin = max(0, end - 64 * 1024)
        lines.seek(begin)
        chunk = lines.read(end - begin)
        newline = chunk.rfind(b"\n")
        if newline >= 0:
            return begin + newline + 1
        end = begin
    return 0


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _integer(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _quota_percent(payload: dict) -> float | None:
    limits = payload.get("rate_limits")
    if not isinstance(limits, dict) or limits.get("limit_id") != "codex":
        return None
    primary = limits.get("primary")
    if not isinstance(primary, dict):
        return None
    window = primary.get("window_minutes")
    if window is not None and _integer(window) != 7 * 24 * 60:
        return None
    value = primary.get("used_percent")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def model_label(model: str) -> str:
    names = {
        "gpt-6-astra": "6 Astra",
        "gpt-5.6-sol": "5.6 Sol",
        "gpt-5.6-terra": "5.6 Terra",
        "gpt-5.6-luna": "5.6 Luna",
        "codex-auto-review": "Auto review",
        "unknown": "Unknown model",
    }
    return names.get(model, model.removeprefix("gpt-").replace("-", " ").title())
