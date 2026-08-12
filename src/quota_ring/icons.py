from __future__ import annotations

import hashlib
import math
from pathlib import Path

from quota_ring.models import icon_state

CENTER_X = 13
CENTER_Y = 12
RING_RADII = (10.25, 6.75, 3.25)  # outer, middle, inner
STROKE_WIDTH = 2.5
TRACK_COLOR = "#757575"
TRACK_OPACITY = 0.25
STATE_COLORS = {
    "green": "#43a047",
    "yellow": "#fdd835",
    "orange": "#fb8c00",
    "red": "#ef3e32",
    "unknown": TRACK_COLOR,
}
PULSE_LIGHT_COLOR = "#ffc9b8"


def rings_svg(
    states: tuple[int | None, int | None, int | None], pulse_light: bool = False
) -> str:
    """Render three concentric gauge rings (outer, middle, inner) as an SVG.

    Each state is a remaining percentage, or None for a disabled/unavailable
    provider, which leaves only the faint track. ``pulse_light`` renders
    critical rings (<= 2%) in a pale shade for the pulse animation.
    """
    elements = []
    for remaining, radius in zip(states, RING_RADII, strict=True):
        elements.append(
            f'<circle cx="{CENTER_X}" cy="{CENTER_Y}" r="{radius}" fill="none" '
            f'stroke="{TRACK_COLOR}" stroke-opacity="{TRACK_OPACITY}" '
            f'stroke-width="{STROKE_WIDTH}"/>'
        )
        if remaining is None:
            continue
        state = icon_state(remaining)
        if pulse_light and remaining <= 2:
            color = PULSE_LIGHT_COLOR
        else:
            color = STATE_COLORS[state]
        circumference = 2 * math.pi * radius
        fraction = max(0, min(100, remaining)) / 100
        arc = fraction * circumference
        elements.append(
            f'<circle cx="{CENTER_X}" cy="{CENTER_Y}" r="{radius}" fill="none" '
            f'stroke="{color}" stroke-width="{STROKE_WIDTH}" '
            f'stroke-linecap="round" stroke-dasharray="{arc:.2f} '
            f'{circumference:.2f}" '
            f'transform="rotate(-90 {CENTER_X} {CENTER_Y})"/>'
        )
    body = "".join(elements)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="26" height="24" '
        f'viewBox="0 0 26 24">{body}</svg>'
    )


def prune_icons(cache_dir: Path, keep: int = 64) -> int:
    """Drop the oldest generated icons, keeping the ``keep`` most recent.

    Every distinct ring combination leaves a file behind, so the cache would
    otherwise grow for as long as the app is installed. Returns the number
    removed. The static unknown icon is never touched.
    """
    generated = [
        path
        for path in cache_dir.glob("quota-ring-*.svg")
        if path.name != "quota-ring-unknown.svg"
    ]
    if len(generated) <= keep:
        return 0
    generated.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    removed = 0
    for path in generated[keep:]:
        try:
            path.unlink()
        except OSError:
            continue
        removed += 1
    return removed


def write_icon(svg: str, cache_dir: Path) -> str:
    """Write ``svg`` to a content-addressed file in ``cache_dir``.

    Returns the icon name (filename without extension) for
    ``Indicator.set_icon_full``. Existing files are reused, so repeated
    renders of the same state cost nothing.
    """
    digest = hashlib.sha1(svg.encode("utf-8")).hexdigest()[:12]
    name = f"quota-ring-{digest}"
    path = cache_dir / f"{name}.svg"
    if not path.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(svg, encoding="utf-8")
    return name
