"""The Insights window: pace, projection, and utilization over time.

The indicator answers "how much is left". This window answers "will it last",
which is a different question and needs the shape of the spend, not just its
level. The central drawing is a burn-up chart: the straight line from the
window's start to a fully spent reset is exactly on-pace spending, and the
plotted curve is what actually happened. Above the line is trouble.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime

import cairo
import gi

from quota_ring.forecast import (
    EARLY,
    OVER,
    SPENT,
    UNKNOWN,
    Forecast,
    earliest_shortfall,
    forecast_status,
    format_duration,
    normalize_points,
)
from quota_ring.history import HistoryStore
from quota_ring.icons import STATE_COLORS
from quota_ring.models import DashboardStatus, icon_state, reset_description

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

CHART_PADDING = (52, 16, 16, 34)  # left, right, top, bottom
GRID_STEPS = (0, 25, 50, 75, 100)


class InsightsWindow(Gtk.Window):
    def __init__(
        self,
        history: HistoryStore,
        on_refresh: Callable[[], None],
        parent_title: str = "Quota Ring",
    ):
        super().__init__(title=f"{parent_title} Insights")
        self.history = history
        self.on_refresh = on_refresh
        self._forecasts: list[Forecast] = []
        self._selected: tuple[str, str] | None = None
        self._rebuilding = False
        self.set_default_size(960, 680)
        self.set_position(Gtk.WindowPosition.CENTER)

        header = Gtk.HeaderBar(title=f"{parent_title} Insights")
        header.set_show_close_button(True)
        self.refresh_button = Gtk.Button.new_from_icon_name(
            "view-refresh-symbolic", Gtk.IconSize.BUTTON
        )
        self.refresh_button.set_tooltip_text("Check every provider now")
        self.refresh_button.connect("clicked", lambda _button: self.on_refresh())
        header.pack_end(self.refresh_button)
        clear_button = Gtk.Button.new_from_icon_name(
            "user-trash-symbolic", Gtk.IconSize.BUTTON
        )
        clear_button.set_tooltip_text("Delete the stored usage history")
        clear_button.connect("clicked", self._on_clear_history)
        header.pack_start(clear_button)
        self.set_titlebar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)

        self.headline = Gtk.Label(xalign=0)
        self.headline.set_margin_top(14)
        self.headline.set_margin_start(16)
        self.headline.set_margin_end(16)
        self.headline.set_line_wrap(True)
        outer.pack_start(self.headline, False, False, 0)

        self.subtitle = Gtk.Label(xalign=0)
        self.subtitle.set_margin_start(16)
        self.subtitle.set_margin_end(16)
        self.subtitle.set_margin_bottom(10)
        outer.pack_start(self.subtitle, False, False, 0)
        outer.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0
        )

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(352)
        outer.pack_start(paned, True, True, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.window_list = Gtk.ListBox()
        self.window_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.window_list.set_header_func(_provider_header, None)
        self.window_list.connect("row-selected", self._on_row_selected)
        scroller.add(self.window_list)
        paned.pack1(scroller, False, False)

        self.detail = _DetailPane(self.history)
        paned.pack2(self.detail, True, False)

    def update(
        self,
        status: DashboardStatus | None,
        last_checked: datetime | None,
        refreshing: bool,
        error: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Take a fresh reading from the indicator and redraw."""
        current = now or datetime.now().astimezone()
        self.refresh_button.set_sensitive(not refreshing)
        self._forecasts = forecast_status(status, current) if status else []
        self._set_headline(status, error)
        self._set_subtitle(last_checked, refreshing)
        self._rebuild_list()

    def _set_headline(
        self, status: DashboardStatus | None, error: str | None
    ) -> None:
        if error and not status:
            self.headline.set_markup(
                f"<span size='x-large' weight='bold'>{_escape(error)}</span>"
            )
            return
        if not self._forecasts:
            self.headline.set_markup(
                "<span size='x-large' weight='bold'>Waiting for the first "
                "reading…</span>"
            )
            return
        risk = earliest_shortfall(self._forecasts)
        if risk is None:
            self.headline.set_markup(
                "<span size='x-large' weight='bold'>Every window is on pace to "
                "last</span>"
            )
            return
        shortfall = risk.shortfall
        early = (
            f" — {format_duration(shortfall)} before it resets"
            if shortfall is not None and shortfall.total_seconds() > 0
            else ""
        )
        when = (
            risk.exhaustion.strftime("%a %-I:%M%p").replace("AM", "am").replace(
                "PM", "pm"
            )
            if risk.exhaustion
            else "soon"
        )
        color = STATE_COLORS["red" if risk.state == SPENT else "orange"]
        self.headline.set_markup(
            f"<span size='x-large' weight='bold' color='{color}'>"
            f"{_escape(risk.display_name)} {_escape(risk.window.name)} runs out "
            f"{_escape(when)}</span>"
            f"<span size='x-large' weight='bold'>{_escape(early)}</span>"
        )

    def _set_subtitle(self, last_checked: datetime | None, refreshing: bool) -> None:
        checked = (
            last_checked.strftime("%-I:%M:%S %p") if last_checked else "not yet"
        )
        state = "refreshing…" if refreshing else f"last checked {checked}"
        self.subtitle.set_markup(
            f"<span alpha='65%'>Projections assume the current average rate "
            f"holds — {_escape(state)}</span>"
        )

    def _rebuild_list(self) -> None:
        self._rebuilding = True
        for child in self.window_list.get_children():
            self.window_list.remove(child)
        selected_row = None
        for forecast in self._forecasts:
            row = _WindowRow(forecast)
            self.window_list.add(row)
            if self._selected == (forecast.provider, forecast.window.name):
                selected_row = row
        self.window_list.show_all()
        self._rebuilding = False
        if selected_row is None:
            rows = self.window_list.get_children()
            # Open on whatever is most at risk, since that is why the window
            # gets opened at all.
            risk = earliest_shortfall(self._forecasts)
            selected_row = next(
                (
                    row
                    for row in rows
                    if risk is not None
                    and isinstance(row, _WindowRow)
                    and row.key == (risk.provider, risk.window.name)
                ),
                rows[0] if rows else None,
            )
        if selected_row is not None:
            self.window_list.select_row(selected_row)

    def _on_clear_history(self, _button: Gtk.Button) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Delete stored usage history?",
        )
        dialog.format_secondary_text(
            "Every recorded reading is removed. Current percentages are "
            "unaffected, and history starts building again on the next check."
        )
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        self.history.clear()
        row = self.window_list.get_selected_row()
        if row is not None:
            self.detail.show_forecast(row.forecast)

    def _on_row_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if row is None or self._rebuilding:
            return
        self._selected = row.key
        self.detail.show_forecast(row.forecast)


class _WindowRow(Gtk.ListBoxRow):
    def __init__(self, forecast: Forecast):
        super().__init__()
        self.forecast = forecast
        self.key = (forecast.provider, forecast.window.name)
        self.provider_name = forecast.display_name

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(9)
        box.set_margin_bottom(9)
        box.set_margin_start(14)
        box.set_margin_end(14)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        name = Gtk.Label(xalign=0)
        name.set_markup(f"<b>{_escape(forecast.window.name)}</b>")
        top.pack_start(name, True, True, 0)
        remaining = Gtk.Label(xalign=1)
        # No explicit colour here: the row turns solid when selected and a
        # hard-coded foreground would be unreadable against it. The bar below
        # carries the colour instead.
        remaining.set_markup(f"<b>{forecast.window.remaining_percent}%</b>")
        top.pack_end(remaining, False, False, 0)
        box.pack_start(top, False, False, 0)

        bar = _PaceBar(forecast)
        box.pack_start(bar, False, False, 0)

        caption = Gtk.Label(xalign=0)
        caption.set_markup(
            f"<span size='small' alpha='65%'>{_escape(forecast.headline)}</span>"
        )
        caption.set_ellipsize(Pango.EllipsizeMode.END)
        box.pack_start(caption, False, False, 0)
        self.add(box)


class _PaceBar(Gtk.DrawingArea):
    """Spend as a filled bar, with a tick where the clock currently sits.

    Fill past the tick means the allowance is being spent faster than the
    window is elapsing — the same judgement the chart makes, small enough to
    sit in a list row.
    """

    def __init__(self, forecast: Forecast):
        super().__init__()
        self.forecast = forecast
        self.set_size_request(-1, 12)
        self.connect("draw", self._draw)

    def _draw(self, widget: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        width = widget.get_allocated_width()
        height = 8.0
        top = (widget.get_allocated_height() - height) / 2
        fg = _foreground(widget)
        used = self.forecast.window.used_percent / 100

        cr.set_source_rgba(fg[0], fg[1], fg[2], 0.14)
        _rounded_rect(cr, 0, top, width, height, height / 2)
        cr.fill()

        state = icon_state(self.forecast.window.remaining_percent)
        cr.set_source_rgb(*_rgb(STATE_COLORS[state]))
        _rounded_rect(cr, 0, top, max(height, width * used), height, height / 2)
        cr.fill()

        fraction = self.forecast.elapsed_fraction
        if fraction is not None:
            x = width * fraction
            cr.set_source_rgba(fg[0], fg[1], fg[2], 0.75)
            cr.set_line_width(2)
            cr.move_to(x, top - 3)
            cr.line_to(x, top + height + 3)
            cr.stroke()
        return False


class _DetailPane(Gtk.Box):
    def __init__(self, history: HistoryStore):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.history = history
        self.set_margin_top(14)
        self.set_margin_bottom(14)
        self.set_margin_start(18)
        self.set_margin_end(18)

        self.title = Gtk.Label(xalign=0)
        self.pack_start(self.title, False, False, 0)
        self.verdict = Gtk.Label(xalign=0)
        self.verdict.set_line_wrap(True)
        self.verdict.set_margin_bottom(12)
        self.pack_start(self.verdict, False, False, 0)

        self.stats = Gtk.Grid(column_spacing=18, row_spacing=6)
        self.stats.set_margin_bottom(14)
        self.pack_start(self.stats, False, False, 0)

        self.chart = _BurnUpChart()
        self.pack_start(self.chart, True, True, 0)
        self.chart_caption = Gtk.Label(xalign=0)
        self.chart_caption.set_line_wrap(True)
        self.pack_start(self.chart_caption, False, False, 0)

        self.strip_label = Gtk.Label(xalign=0)
        self.strip_label.set_margin_top(12)
        self.pack_start(self.strip_label, False, False, 0)
        self.strip = _HistoryStrip()
        self.pack_start(self.strip, False, False, 0)

    def show_forecast(self, forecast: Forecast) -> None:
        self.title.set_markup(
            f"<span size='large' weight='bold'>{_escape(forecast.display_name)} · "
            f"{_escape(forecast.window.name)}</span>"
        )
        neutral = forecast.state in (UNKNOWN, EARLY)
        color = STATE_COLORS["orange" if forecast.at_risk else "green"]
        attributes = "size='large'" if neutral else f"size='large' color='{color}'"
        verdict = f"<span {attributes}>{_escape(forecast.headline)}</span>"
        if forecast.pace is not None and not forecast.confident:
            # Barely into the window, so the rate has had little to average
            # over and a small burst still swings it a long way.
            verdict += "<span size='large' alpha='55%'> · early estimate</span>"
        self.verdict.set_markup(verdict)
        self._fill_stats(forecast)

        observations = self.history.current_series(
            forecast.provider, forecast.window.name
        )
        self.chart.set_forecast(forecast, observations)
        legend = (
            "The dashed diagonal is spending exactly in step with the window. "
            "Staying above it means the allowance runs out before the reset."
        )
        if len(observations) < 2:
            # Say so rather than let an inferred straight line pass for a
            # record of how the allowance actually went.
            legend = (
                "Nothing recorded for this window yet, so the line is the "
                "average rate the projection assumes, not observed spend. "
                "It fills in as the indicator runs."
            )
        self.chart_caption.set_markup(
            f"<span size='small' alpha='60%'>{_escape(legend)}</span>"
        )
        instances = self.history.recent_instances(
            forecast.provider, forecast.window.name
        )
        # The live window is already the chart's subject; the strip is about
        # the ones that finished.
        completed = [
            instance
            for instance in instances
            if instance.instance_key
            != self.history.current_instance_key(
                forecast.provider, forecast.window.name
            )
        ]
        self.strip_label.set_markup(
            "<span weight='bold'>Previous windows</span>"
            f"<span alpha='65%'>  ·  peak spend of the last "
            f"{len(completed)} completed</span>"
            if completed
            else "<span weight='bold'>Previous windows</span>"
            "<span alpha='65%'>  ·  none recorded yet — history builds as the "
            "indicator runs</span>"
        )
        self.strip.set_instances(completed)

    def _fill_stats(self, forecast: Forecast) -> None:
        for child in self.stats.get_children():
            self.stats.remove(child)
        window = forecast.window
        reset = reset_description(window, forecast.now)
        elapsed = _elapsed_text(forecast)
        pace = (
            f"{forecast.pace:.2f}× the clock"
            if forecast.pace is not None
            else "not enough of the window has run"
        )
        projected = forecast.projected_used_at_reset
        entries = [
            ("Spent", f"{window.used_percent}%"),
            ("Left", f"{window.remaining_percent}%"),
            ("Elapsed", elapsed),
            ("Pace", pace),
            (
                "At reset",
                f"{projected}% spent at this rate"
                if projected is not None
                else "unknown",
            ),
            ("Resets", reset or "unknown"),
        ]
        for row, (label, value) in enumerate(entries):
            key = Gtk.Label(xalign=0)
            key.set_markup(f"<span alpha='65%'>{_escape(label)}</span>")
            self.stats.attach(key, 0, row, 1, 1)
            self.stats.attach(Gtk.Label(label=value, xalign=0), 1, row, 1, 1)
        self.stats.show_all()


class _BurnUpChart(Gtk.DrawingArea):
    """Spend against elapsed time, with the on-pace line for comparison."""

    def __init__(self):
        super().__init__()
        self.forecast: Forecast | None = None
        self.points: list[tuple[float, float]] = []
        self.set_size_request(-1, 260)
        self.connect("draw", self._draw)

    def set_forecast(self, forecast: Forecast, observations) -> None:
        self.forecast = forecast
        if forecast.start is not None and forecast.reset is not None:
            self.points = normalize_points(
                [
                    (observation.observed_at, observation.used_percent)
                    for observation in observations
                ],
                forecast.start,
                forecast.reset,
            )
        else:
            self.points = []
        self.queue_draw()

    def _draw(self, widget: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        fg = _foreground(widget)
        left, right, top, bottom = CHART_PADDING
        plot_width = max(1, width - left - right)
        plot_height = max(1, height - top - bottom)
        forecast = self.forecast

        cr.select_font_face(
            "Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL
        )
        cr.set_font_size(11)

        def px(x: float) -> float:
            return left + plot_width * min(1.0, max(0.0, x))

        def py(y: float) -> float:
            return top + plot_height * (1 - min(1.0, max(0.0, y)))

        for step in GRID_STEPS:
            y = py(step / 100)
            cr.set_source_rgba(fg[0], fg[1], fg[2], 0.10)
            cr.set_line_width(1)
            cr.move_to(left, y)
            cr.line_to(left + plot_width, y)
            cr.stroke()
            cr.set_source_rgba(fg[0], fg[1], fg[2], 0.55)
            label = f"{step}%"
            extents = cr.text_extents(label)
            cr.move_to(left - 8 - extents.width, y + 4)
            cr.show_text(label)

        if forecast is None or forecast.start is None or forecast.reset is None:
            _centered_text(
                cr, width / 2, height / 2, "No reset time reported", fg, 0.55
            )
            return False

        # The on-pace reference: spending the whole allowance exactly as the
        # window elapses. Everything above this line is running hot.
        cr.set_source_rgba(fg[0], fg[1], fg[2], 0.45)
        cr.set_line_width(1.5)
        cr.set_dash([4, 4])
        cr.move_to(px(0), py(0))
        cr.line_to(px(1), py(1))
        cr.stroke()
        cr.set_dash([])

        fraction = forecast.elapsed_fraction or 0.0
        used = forecast.window.used_percent / 100
        state = icon_state(forecast.window.remaining_percent)
        color = _rgb(STATE_COLORS[state])

        self._draw_curve(cr, forecast, px, py, color, fraction, used)
        self._draw_projection(cr, forecast, px, py, color)

        cr.set_source_rgb(*color)
        cr.arc(px(fraction), py(used), 4, 0, 2 * math.pi)
        cr.fill()

        self._draw_axis_labels(
            cr, forecast, px, py, left, top, plot_width, plot_height, fg
        )
        return False

    def _draw_curve(self, cr, forecast, px, py, color, fraction, used) -> None:
        observed = [point for point in self.points if point[0] <= fraction + 1e-9]
        if len(observed) < 2:
            # One reading says nothing about the shape of the spend, and a step
            # drawn from it would claim the whole allowance went at once. Show
            # the average rate the projection actually assumes instead.
            cr.set_source_rgba(*color, 0.75)
            cr.set_line_width(2)
            cr.set_dash([2, 4])
            cr.move_to(px(0), py(0))
            cr.line_to(px(fraction), py(used))
            cr.stroke()
            cr.set_dash([])
            return

        curve = sorted([*observed, (fraction, used)], key=lambda point: point[0])
        # Spend is a step function, so draw it as steps rather than smoothing
        # between the readings the store happened to catch.
        cr.set_source_rgba(*color, 0.12)
        cr.move_to(px(curve[0][0]), py(0))
        for index, (x, y) in enumerate(curve):
            if index:
                cr.line_to(px(x), py(curve[index - 1][1]))
            cr.line_to(px(x), py(y))
        cr.line_to(px(curve[-1][0]), py(0))
        cr.close_path()
        cr.fill()

        cr.set_source_rgb(*color)
        cr.set_line_width(2.4)
        cr.move_to(px(curve[0][0]), py(curve[0][1]))
        for index, (x, y) in enumerate(curve[1:], start=1):
            cr.line_to(px(x), py(curve[index - 1][1]))
            cr.line_to(px(x), py(y))
        cr.stroke()

    def _draw_projection(self, cr, forecast, px, py, color) -> None:
        pace = forecast.pace
        if not pace or pace <= 0:
            return
        fraction = forecast.elapsed_fraction or 0.0
        # In these coordinates the average rate is the ray y = pace * x through
        # the origin, so extending it is the projection.
        end_x = min(1.0, 1 / pace)
        if end_x <= fraction:
            return
        cr.set_source_rgba(*color, 0.85)
        cr.set_line_width(2)
        cr.set_dash([5, 5])
        cr.move_to(px(fraction), py(pace * fraction))
        cr.line_to(px(end_x), py(pace * end_x))
        cr.stroke()
        cr.set_dash([])
        if forecast.state != OVER or 1 / pace > 1:
            return
        cr.set_source_rgb(*_rgb(STATE_COLORS["red"]))
        cr.arc(px(end_x), py(1), 4.5, 0, 2 * math.pi)
        cr.fill()
        # Near the reset the marker sits against the right edge, so the label
        # falls back to the inside of the plot. It rides just above the 100%
        # line either way, which is the one band the curve can never occupy.
        label = "runs out"
        extents = cr.text_extents(label)
        room = px(1) - px(end_x)
        offset = 8 if room > extents.width + 12 else -(extents.width + 8)
        cr.move_to(px(end_x) + offset, py(1) - 6)
        cr.show_text(label)

    def _draw_axis_labels(
        self, cr, forecast, px, py, left, top, plot_width, plot_height, fg
    ) -> None:
        cr.set_source_rgba(fg[0], fg[1], fg[2], 0.55)
        baseline = top + plot_height + 16
        # A weekly window starts and resets on the same weekday at the same
        # time, so the day name alone labels both ends identically.
        dated = (forecast.window.duration_minutes or 0) >= 24 * 60
        start_label = _clock(forecast.start, dated)
        reset_label = f"resets {_clock(forecast.reset, dated)}"
        start_width = cr.text_extents(start_label).width
        reset_width = cr.text_extents(reset_label).width
        reset_x = left + plot_width - reset_width
        cr.move_to(left, baseline)
        cr.show_text(start_label)
        cr.move_to(reset_x, baseline)
        cr.show_text(reset_label)

        fraction = forecast.elapsed_fraction
        if fraction is None:
            return
        x = px(fraction)
        cr.set_source_rgba(fg[0], fg[1], fg[2], 0.35)
        cr.set_line_width(1)
        cr.set_dash([2, 3])
        cr.move_to(x, py(0))
        cr.line_to(x, py(1))
        cr.stroke()
        cr.set_dash([])
        # "now" is only worth printing when it does not run into either end
        # label; the dotted rule already marks the position.
        now_width = cr.text_extents("now").width
        if (
            x - now_width / 2 > left + start_width + 10
            and x + now_width / 2 < reset_x - 10
        ):
            cr.set_source_rgba(fg[0], fg[1], fg[2], 0.55)
            cr.move_to(x - now_width / 2, baseline)
            cr.show_text("now")


class _HistoryStrip(Gtk.DrawingArea):
    """Peak spend of each completed window, oldest to newest."""

    def __init__(self):
        super().__init__()
        self.instances: list = []
        self.set_size_request(-1, 92)
        self.connect("draw", self._draw)

    def set_instances(self, instances: list) -> None:
        self.instances = instances
        self.queue_draw()

    def _draw(self, widget: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        fg = _foreground(widget)
        cr.select_font_face(
            "Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL
        )
        cr.set_font_size(10)
        if not self.instances:
            _centered_text(
                cr,
                width / 2,
                height / 2,
                "Completed windows will appear here",
                fg,
                0.45,
            )
            return False

        top = 8.0
        bottom = height - 20
        plot_height = max(1.0, bottom - top)
        cr.set_source_rgba(fg[0], fg[1], fg[2], 0.18)
        cr.set_line_width(1)
        cr.move_to(0, top)
        cr.line_to(width, top)
        cr.stroke()

        slot = width / max(1, len(self.instances))
        bar_width = min(38.0, slot * 0.62)
        for index, instance in enumerate(self.instances):
            peak = min(100, max(0, instance.peak_used_percent))
            bar_height = plot_height * peak / 100
            x = slot * index + (slot - bar_width) / 2
            cr.set_source_rgba(fg[0], fg[1], fg[2], 0.10)
            _rounded_rect(cr, x, top, bar_width, plot_height, 3)
            cr.fill()
            cr.set_source_rgb(*_rgb(STATE_COLORS[icon_state(100 - peak)]))
            _rounded_rect(
                cr, x, bottom - bar_height, bar_width, bar_height, 3
            )
            cr.fill()
            cr.set_source_rgba(fg[0], fg[1], fg[2], 0.65)
            label = instance.first_seen.strftime("%-m/%-d")
            extents = cr.text_extents(label)
            cr.move_to(x + (bar_width - extents.width) / 2, height - 6)
            cr.show_text(label)
        return False


def _provider_header(row, before, _data) -> None:
    if before is not None and before.provider_name == row.provider_name:
        row.set_header(None)
        return
    label = Gtk.Label(xalign=0)
    label.set_markup(
        f"<span weight='bold' alpha='75%'>{_escape(row.provider_name)}</span>"
    )
    label.set_margin_top(12 if before is not None else 8)
    label.set_margin_bottom(2)
    label.set_margin_start(14)
    label.show()
    row.set_header(label)


def _elapsed_text(forecast: Forecast) -> str:
    if forecast.start is None or forecast.reset is None:
        return "unknown"
    total = forecast.reset - forecast.start
    elapsed = forecast.now - forecast.start
    percent = forecast.elapsed_fraction or 0.0
    return (
        f"{format_duration(elapsed)} of {format_duration(total)} "
        f"({round(percent * 100)}%)"
    )


def _clock(value: datetime | None, dated: bool = False) -> str:
    if value is None:
        return "?"
    pattern = "%b %-d, %-I:%M%p" if dated else "%a %-I:%M%p"
    return value.strftime(pattern).replace("AM", "am").replace("PM", "pm")


def _centered_text(cr, x, y, text, fg, alpha) -> None:
    cr.set_source_rgba(fg[0], fg[1], fg[2], alpha)
    extents = cr.text_extents(text)
    cr.move_to(x - extents.width / 2, y)
    cr.show_text(text)


def _rounded_rect(cr, x, y, width, height, radius) -> None:
    radius = min(radius, width / 2, height / 2)
    if radius <= 0:
        cr.rectangle(x, y, width, height)
        return
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _rgb(color: str) -> tuple[float, float, float]:
    value = color.lstrip("#")
    return tuple(int(value[index : index + 2], 16) / 255 for index in (0, 2, 4))  # type: ignore[return-value]


def _foreground(widget: Gtk.Widget) -> tuple[float, float, float, float]:
    color = widget.get_style_context().get_color(Gtk.StateFlags.NORMAL)
    return color.red, color.green, color.blue, color.alpha


def _escape(text: str) -> str:
    return GLib.markup_escape_text(text)
