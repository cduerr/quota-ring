"""Persistent usage history.

Spend is reported as a whole percent, so a series of polls is a step function:
between two five-minute samples a weekly window moves by zero or one point.
Storing every sample would therefore record mostly noise, so only *changes* are
kept, along with a per-series heartbeat. The heartbeat matters as much as the
transitions — without it a flat stretch is ambiguous between "the user was idle"
and "the indicator was not running", and those two deserve opposite treatment.

Kept free of GTK imports so it can be tested without the desktop bindings.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from quota_ring.models import DashboardStatus

DEFAULT_RETENTION_DAYS = 90

SCHEMA = """
CREATE TABLE IF NOT EXISTS observation (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    window_name TEXT NOT NULL,
    instance_key TEXT NOT NULL,
    observed_at INTEGER NOT NULL,
    used_percent INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS observation_series
    ON observation (provider, window_name, observed_at);
CREATE TABLE IF NOT EXISTS series_state (
    provider TEXT NOT NULL,
    window_name TEXT NOT NULL,
    instance_key TEXT NOT NULL,
    used_percent INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    PRIMARY KEY (provider, window_name)
);
"""


@dataclass(frozen=True)
class Observation:
    provider: str
    window_name: str
    instance_key: str
    observed_at: datetime
    used_percent: int


@dataclass(frozen=True)
class Instance:
    """One run of a window between resets."""

    instance_key: str
    peak_used_percent: int
    first_seen: datetime
    last_seen: datetime


def default_path() -> Path:
    """Where history lives.

    Deliberately under XDG_STATE_HOME: ``~/.local/share/quota-ring`` is the
    install prefix and ``scripts/uninstall.sh`` clears it on every uninstall,
    so history kept there would not survive a reinstall.
    """
    state_home = Path(
        os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
    )
    return state_home / "quota-ring" / "history.db"


class HistoryStore:
    def __init__(
        self,
        path: Path | None = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        memory: bool = False,
    ):
        self.path = None if memory else (path or default_path())
        self.retention_days = retention_days
        if self.path is None:
            self._connection = sqlite3.connect(":memory:")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)
        self._connection.commit()
        # Usage history is a record of when its owner was working, so keep it
        # readable only by them.
        if self.path is not None:
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def close(self) -> None:
        self._connection.close()

    def record(self, status: DashboardStatus, now: datetime | None = None) -> int:
        """Store any window whose spend moved. Returns the number written."""
        current = now or datetime.now().astimezone()
        stamp = int(current.timestamp())
        written = 0
        for provider in status.providers:
            if not provider.available:
                continue
            for window in provider.windows:
                prior = self._series_state(provider.provider, window.name)
                key = self._instance_key(
                    window.resets_at, prior, window.used_percent, stamp
                )
                changed = (
                    prior is None
                    or prior["instance_key"] != key
                    or prior["used_percent"] != window.used_percent
                )
                if changed:
                    self._connection.execute(
                        "INSERT INTO observation (provider, window_name, "
                        "instance_key, observed_at, used_percent) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            provider.provider,
                            window.name,
                            key,
                            stamp,
                            window.used_percent,
                        ),
                    )
                    written += 1
                self._connection.execute(
                    "INSERT INTO series_state (provider, window_name, "
                    "instance_key, used_percent, last_seen) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(provider, window_name) DO UPDATE SET "
                    "instance_key=excluded.instance_key, "
                    "used_percent=excluded.used_percent, "
                    "last_seen=excluded.last_seen",
                    (
                        provider.provider,
                        window.name,
                        key,
                        window.used_percent,
                        stamp,
                    ),
                )
        self._connection.commit()
        return written

    def current_instance_key(self, provider: str, window_name: str) -> str | None:
        row = self._series_state(provider, window_name)
        return row["instance_key"] if row else None

    def last_seen(self, provider: str, window_name: str) -> datetime | None:
        row = self._series_state(provider, window_name)
        if row is None:
            return None
        return datetime.fromtimestamp(row["last_seen"]).astimezone()

    def series(
        self, provider: str, window_name: str, instance_key: str
    ) -> list[Observation]:
        """Every recorded step of one window instance, oldest first."""
        rows = self._connection.execute(
            "SELECT * FROM observation WHERE provider = ? AND window_name = ? "
            "AND instance_key = ? ORDER BY observed_at",
            (provider, window_name, instance_key),
        ).fetchall()
        return [_observation(row) for row in rows]

    def current_series(self, provider: str, window_name: str) -> list[Observation]:
        key = self.current_instance_key(provider, window_name)
        return self.series(provider, window_name, key) if key else []

    def recent_instances(
        self, provider: str, window_name: str, limit: int = 12
    ) -> list[Instance]:
        """Completed and running windows, most recent last.

        The peak spend of each past instance is the "how much of it did I
        actually use" history that a single live reading cannot show.
        """
        rows = self._connection.execute(
            "SELECT instance_key, MAX(used_percent) AS peak, "
            "MIN(observed_at) AS first_seen, MAX(observed_at) AS last_seen "
            "FROM observation WHERE provider = ? AND window_name = ? "
            "GROUP BY instance_key ORDER BY first_seen DESC LIMIT ?",
            (provider, window_name, limit),
        ).fetchall()
        return [
            Instance(
                instance_key=row["instance_key"],
                peak_used_percent=row["peak"],
                first_seen=datetime.fromtimestamp(row["first_seen"]).astimezone(),
                last_seen=datetime.fromtimestamp(row["last_seen"]).astimezone(),
            )
            for row in reversed(rows)
        ]

    def prune(self, now: datetime | None = None) -> int:
        current = now or datetime.now().astimezone()
        cutoff = int((current - timedelta(days=self.retention_days)).timestamp())
        cursor = self._connection.execute(
            "DELETE FROM observation WHERE observed_at < ?", (cutoff,)
        )
        self._connection.commit()
        return cursor.rowcount

    def clear(self) -> None:
        self._connection.execute("DELETE FROM observation")
        self._connection.execute("DELETE FROM series_state")
        self._connection.commit()

    def _series_state(self, provider: str, window_name: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM series_state WHERE provider = ? AND window_name = ?",
            (provider, window_name),
        ).fetchone()

    @staticmethod
    def _instance_key(
        resets_at: int | None,
        prior: sqlite3.Row | None,
        used_percent: int,
        stamp: int,
    ) -> str:
        """Identify which run of the window a reading belongs to.

        The reset timestamp is the natural identity. Without one, a drop in
        spend is the only evidence that the window rolled over, so the key is
        held steady until that happens. Velocity must never be computed across
        this boundary.
        """
        if resets_at is not None:
            return str(resets_at)
        if prior is None or used_percent < prior["used_percent"]:
            return str(stamp)
        return str(prior["instance_key"])


def _observation(row: sqlite3.Row) -> Observation:
    return Observation(
        provider=row["provider"],
        window_name=row["window_name"],
        instance_key=row["instance_key"],
        observed_at=datetime.fromtimestamp(row["observed_at"]).astimezone(),
        used_percent=row["used_percent"],
    )
