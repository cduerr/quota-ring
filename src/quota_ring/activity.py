"""Learn recurring quiet hours from local activity, without guessing sleep."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class QuietHours:
    start_hour: int
    hours: int
    days: int

    def weight(self, hour: int) -> float:
        # Leave some allowance for occasional work during the quiet stretch.
        return 0.1 if (hour - self.start_hour) % 24 < self.hours else 1.0

    def work_seconds(self, start: datetime, end: datetime) -> float:
        """Integrate local hourly weights using real elapsed seconds (DST safe)."""
        stamp = start.timestamp()
        stop = end.timestamp()
        result = 0.0
        while stamp < stop:
            local = datetime.fromtimestamp(stamp).astimezone()
            # Step to the next wall-clock hour, including repeated DST hours.
            boundary = stamp + 3600 - local.minute * 60 - local.second
            boundary -= local.microsecond / 1_000_000
            following = min(stop, boundary)
            result += (following - stamp) * self.weight(local.hour)
            stamp = following
        return result


def learn_quiet_hours(activity: list[datetime], now: datetime) -> QuietHours | None:
    """Require seven substantial, completed days and 80% hourly agreement.

    Partial edge days and days with too little activity cannot establish rest.
    Counts are per day, so one busy session cannot dominate the pattern.
    """
    today = now.astimezone().date()
    cutoff = today - timedelta(days=21)
    daily: dict = defaultdict(set)
    for at in activity:
        local = at.astimezone()
        if cutoff <= local.date() < today:
            daily[local.date()].add(local.hour)
    if daily:
        daily.pop(min(daily))  # The importer may have started mid-day.
    days = [hours for hours in daily.values() if len(hours) >= 6]
    if len(days) < 7:
        return None
    counts = Counter(hour for hours in days for hour in hours)
    quiet = {hour for hour in range(24) if counts[hour] / len(days) <= 0.2}
    candidates = []
    for start in range(24):
        if (start - 1) % 24 in quiet:
            continue
        length = 0
        while length < 11 and (start + length) % 24 in quiet:
            length += 1
        if 4 <= length <= 10:
            candidates.append((length, start))
    if not candidates:
        return None
    length, start = max(candidates)
    return QuietHours(start, length, len(days))
