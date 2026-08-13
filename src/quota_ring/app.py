from __future__ import annotations

import logging
import shutil
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import gi

from quota_ring import __version__
from quota_ring.client import ClaudeClient, CodexClient, KimiClient
from quota_ring.config import Config
from quota_ring.history import HistoryStore
from quota_ring.icons import prune_icons, rings_svg, write_icon
from quota_ring.insights import InsightsWindow
from quota_ring.models import (
    DashboardStatus,
    ProviderStatus,
    refresh_interval,
    reset_description,
)
from quota_ring.runtime import InstanceLock, configure_logging

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3, GLib, Gtk  # noqa: E402

LOGGER = logging.getLogger(__name__)

PROVIDER_DISPLAY_NAMES = {
    "codex": "Codex",
    "kimi": "Kimi",
    "claude": "Claude Code",
}


class QuotaRingIndicator:
    def __init__(self, config: Config):
        self.config = config
        self._refreshing = False
        self._last_checked: datetime | None = None
        self._status: DashboardStatus | None = None
        self._error: str | None = None
        self._timer_id: int | None = None
        self._icon_timer_id: int | None = None
        self._pulse_states: tuple[int | None, ...] | None = None
        self._icon_light_phase = False
        self._insights: InsightsWindow | None = None
        self.history = _open_history()
        self.icon_dir = Path(__file__).resolve().parent / "assets" / "icons"
        self.icon_cache_dir = Path(GLib.get_user_cache_dir()) / "quota-ring" / "icons"
        self.icon_cache_dir.mkdir(parents=True, exist_ok=True)
        # Pruning only at startup keeps it clear of whatever the panel is
        # currently displaying.
        prune_icons(self.icon_cache_dir)
        shutil.copy2(
            self.icon_dir / "quota-ring-unknown.svg",
            self.icon_cache_dir / "quota-ring-unknown.svg",
        )
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "io.github.cduerr.QuotaRing",
            "quota-ring-unknown",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_icon_theme_path(str(self.icon_cache_dir))
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Quota Ring")
        self._rebuild_menu()

    def run(self) -> None:
        self.refresh()
        self._schedule_refresh()
        Gtk.main()

    def _schedule_refresh(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
        remaining = self._status.remaining_percent if self._status else None
        interval = refresh_interval(
            remaining,
            self.config.poll_seconds,
            self.config.low_poll_seconds,
        )
        self._timer_id = GLib.timeout_add_seconds(interval, self.refresh)

    def refresh(self) -> bool:
        if self._refreshing:
            return True
        self._refreshing = True
        self._rebuild_menu()
        self._update_insights()
        threading.Thread(target=self._fetch_status, daemon=True).start()
        return True

    def _fetch_status(self) -> None:
        clients = []
        if self.config.codex_enabled:
            clients.append(("codex", "Codex", CodexClient(self.config)))
        if self.config.kimi_enabled:
            clients.append(("kimi", "Kimi", KimiClient(self.config)))
        if self.config.claude_enabled:
            clients.append(("claude", "Claude Code", ClaudeClient(self.config)))
        if not clients:
            GLib.idle_add(self._apply_error, "No providers are enabled")
            return

        statuses: dict[str, ProviderStatus] = {}
        with ThreadPoolExecutor(max_workers=len(clients)) as executor:
            futures = {
                executor.submit(client.fetch): (key, name)
                for key, name, client in clients
            }
            for future in as_completed(futures):
                key, name = futures[future]
                try:
                    statuses[key] = future.result()
                except Exception as exc:
                    LOGGER.warning("%s refresh failed: %s", name, exc)
                    statuses[key] = ProviderStatus(
                        key, name, unavailable_reason=_short_error(str(exc))
                    )
        ordered = tuple(statuses[key] for key, _name, _client in clients)
        GLib.idle_add(self._apply_status, DashboardStatus(ordered))

    def _apply_status(self, status: DashboardStatus) -> bool:
        self._status = status
        self._error = None
        self._last_checked = datetime.now().astimezone()
        self._refreshing = False
        remaining = status.remaining_percent
        description = (
            f"LLM quota: {remaining}%"
            if remaining is not None
            else "LLM quota unavailable"
        )
        self._set_rings_icon(self._ring_states(status), description)
        self.indicator.set_title(description)
        try:
            self.history.record(status, self._last_checked)
        except sqlite3.Error as exc:
            LOGGER.warning("Could not record usage history: %s", exc)
        self._schedule_refresh()
        self._rebuild_menu()
        self._update_insights()
        return False

    def _apply_error(self, message: str) -> bool:
        LOGGER.error("Refresh failed: %s", message)
        self._status = None
        self._error = message
        self._last_checked = datetime.now().astimezone()
        self._refreshing = False
        self._stop_icon_animation()
        self.indicator.set_icon_full("quota-ring-unknown", "LLM quota unavailable")
        self.indicator.set_title("LLM quota unavailable")
        self._rebuild_menu()
        self._update_insights()
        return False

    def _ring_states(
        self, status: DashboardStatus
    ) -> tuple[int | None, int | None, int | None]:
        by_provider = {provider.provider: provider for provider in status.providers}
        states = []
        for key in self.config.ring_order:
            provider = by_provider.get(key)
            states.append(provider.remaining_percent if provider else None)
        return tuple(states)  # type: ignore[return-value]

    def _set_rings_icon(self, states: tuple[int | None, ...], description: str) -> None:
        self._stop_icon_animation()
        name = write_icon(rings_svg(states), self.icon_cache_dir)
        self.indicator.set_icon_full(name, description)
        if any(state is not None and state <= 2 for state in states):
            self._pulse_states = states
            self._icon_timer_id = GLib.timeout_add(1400, self._pulse_icon)

    def _pulse_icon(self) -> bool:
        if self._pulse_states is None:
            return False
        self._icon_light_phase = not self._icon_light_phase
        name = write_icon(
            rings_svg(self._pulse_states, pulse_light=self._icon_light_phase),
            self.icon_cache_dir,
        )
        self.indicator.set_icon_full(name, "LLM usage critical")
        return True

    def _stop_icon_animation(self) -> None:
        if self._icon_timer_id is not None:
            GLib.source_remove(self._icon_timer_id)
            self._icon_timer_id = None
        self._pulse_states = None
        self._icon_light_phase = False

    def _rebuild_menu(self) -> None:
        menu = Gtk.Menu()
        insights_item = Gtk.MenuItem(label="Insights…")
        insights_item.connect("activate", self._show_insights)
        menu.append(insights_item)
        menu.append(Gtk.SeparatorMenuItem())
        if self._error and not self._status:
            menu.append(_info_item("Usage unavailable"))
            menu.append(_info_item(self._error, self._error))
        elif self._status:
            overall = self._status.remaining_percent
            menu.append(
                _info_item(
                    f"Overall · {overall}%"
                    if overall is not None
                    else "Overall · unavailable"
                )
            )
            for provider in self._status.providers:
                menu.append(_provider_item(provider))
        else:
            menu.append(_info_item("Waiting for first update…"))

        if self._refreshing:
            menu.append(Gtk.SeparatorMenuItem())
            menu.append(_info_item("Refreshing…"))

        menu.append(Gtk.SeparatorMenuItem())
        checked = (
            self._last_checked.strftime("%-I:%M:%S %p")
            if self._last_checked
            else "Never"
        )
        menu.append(_info_item(f"Last checked: {checked}"))
        refresh_item = Gtk.MenuItem(label="Refresh")
        refresh_item.set_sensitive(not self._refreshing)
        refresh_item.connect("activate", lambda _item: self.refresh())
        menu.append(refresh_item)

        settings_item = Gtk.MenuItem(label="Settings…")
        settings_item.connect("activate", self._show_settings)
        menu.append(settings_item)
        about_item = Gtk.MenuItem(label="About Quota Ring")
        about_item.connect("activate", self._show_about)
        menu.append(about_item)
        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _item: Gtk.main_quit())
        menu.append(quit_item)
        menu.show_all()
        self.indicator.set_menu(menu)

    def _show_insights(self, _item: Gtk.MenuItem) -> None:
        if self._insights is not None:
            self._insights.present()
            return
        window = InsightsWindow(self.history, self.refresh)
        window.connect("destroy", self._on_insights_destroyed)
        self._insights = window
        self._update_insights()
        window.show_all()

    def _on_insights_destroyed(self, _window: Gtk.Window) -> None:
        self._insights = None

    def _update_insights(self) -> None:
        if self._insights is None:
            return
        self._insights.update(
            self._status, self._last_checked, self._refreshing, self._error
        )

    def _show_settings(self, _item: Gtk.MenuItem) -> None:
        dialog = Gtk.Dialog(title="Quota Ring Settings")
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.OK)
        dialog.set_default_size(430, -1)
        grid = Gtk.Grid(column_spacing=12, row_spacing=10, margin=16)
        # Provider rows double as the ring assignment: their order here is the
        # ring order, top = outer. The arrows move a row up or down.
        controls = []
        for key in self.config.ring_order:
            checkbox = Gtk.CheckButton(label=PROVIDER_DISPLAY_NAMES[key])
            checkbox.set_active(getattr(self.config, f"{key}_enabled"))
            command = Gtk.Entry(text=getattr(self.config, f"{key}_command"))
            command.set_sensitive(checkbox.get_active())
            checkbox.connect(
                "toggled",
                lambda button, entry=command: entry.set_sensitive(button.get_active()),
            )
            buttons = Gtk.Box(spacing=4)
            up = Gtk.Button.new_from_icon_name("go-up-symbolic", Gtk.IconSize.BUTTON)
            up.set_tooltip_text("Move outward")
            down = Gtk.Button.new_from_icon_name(
                "go-down-symbolic", Gtk.IconSize.BUTTON
            )
            down.set_tooltip_text("Move inward")
            buttons.pack_start(up, False, False, 0)
            buttons.pack_start(down, False, False, 0)
            control = {
                "key": key,
                "checkbox": checkbox,
                "command": command,
                "buttons": buttons,
            }
            up.connect(
                "clicked",
                lambda _b, c=control: self._move_provider_row(grid, controls, c, -1),
            )
            down.connect(
                "clicked",
                lambda _b, c=control: self._move_provider_row(grid, controls, c, 1),
            )
            control["up"] = up
            control["down"] = down
            controls.append(control)
        for row, control in enumerate(controls):
            grid.attach(control["checkbox"], 0, row, 1, 1)
            grid.attach(control["command"], 1, row, 1, 1)
            grid.attach(control["buttons"], 2, row, 1, 1)
        self._update_provider_move_buttons(controls)
        poll = Gtk.SpinButton.new_with_range(30, 3600, 30)
        poll.set_value(self.config.poll_seconds)
        grid.attach(Gtk.Label(label="Normal refresh (seconds)", xalign=0), 0, 3, 1, 1)
        grid.attach(poll, 1, 3, 1, 1)
        low_poll = Gtk.SpinButton.new_with_range(30, 3600, 30)
        low_poll.set_value(self.config.low_poll_seconds)
        grid.attach(Gtk.Label(label="Below 5% refresh (seconds)", xalign=0), 0, 4, 1, 1)
        grid.attach(low_poll, 1, 4, 1, 1)
        note = Gtk.Label(
            label=(
                "Provider order sets the rings: top is outer. Uses each CLI’s "
                "existing local login. Credentials are not copied or stored."
            ),
            xalign=0,
            wrap=True,
        )
        grid.attach(note, 0, 5, 2, 1)
        dialog.get_content_area().add(grid)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            provider_values = {}
            for control in controls:
                key = control["key"]
                provider_values[f"{key}_enabled"] = control["checkbox"].get_active()
                provider_values[f"{key}_command"] = (
                    control["command"].get_text().strip() or key
                )
            new_config = replace(
                self.config,
                **provider_values,
                ring_order=tuple(control["key"] for control in controls),
                poll_seconds=poll.get_value_as_int(),
                low_poll_seconds=low_poll.get_value_as_int(),
            )
            try:
                new_config.save()
            except (OSError, ValueError) as exc:
                self._apply_error(f"Could not save settings: {exc}")
            else:
                self.config = new_config
                self._schedule_refresh()
                self.refresh()
        dialog.destroy()

    def _move_provider_row(
        self,
        grid: Gtk.Grid,
        controls: list,
        control: dict,
        direction: int,
    ) -> None:
        index = controls.index(control)
        target = index + direction
        if not 0 <= target < len(controls):
            return
        for moved in (controls[index], controls[target]):
            for widget in (moved["checkbox"], moved["command"], moved["buttons"]):
                grid.remove(widget)
        controls[index], controls[target] = controls[target], controls[index]
        # Only the two swapped rows were detached; re-attaching every row would
        # add widgets that are still parented to the grid.
        for row in (index, target):
            moved = controls[row]
            grid.attach(moved["checkbox"], 0, row, 1, 1)
            grid.attach(moved["command"], 1, row, 1, 1)
            grid.attach(moved["buttons"], 2, row, 1, 1)
        grid.show_all()
        self._update_provider_move_buttons(controls)

    def _update_provider_move_buttons(self, controls: list) -> None:
        for index, control in enumerate(controls):
            control["up"].set_sensitive(index > 0)
            control["down"].set_sensitive(index < len(controls) - 1)

    def _show_about(self, _item: Gtk.MenuItem) -> None:
        dialog = Gtk.AboutDialog()
        dialog.set_program_name("Quota Ring")
        dialog.set_version(__version__)
        dialog.set_comments("AI coding-plan usage at a glance")
        dialog.set_website("https://github.com/cduerr/quota-ring")
        dialog.set_website_label("Quota Ring on GitHub")
        dialog.set_copyright("Copyright © 2026 Chris Duerr")
        dialog.set_license_type(Gtk.License.MIT_X11)
        dialog.set_transient_for(None)
        dialog.run()
        dialog.destroy()


def _info_item(label: str, tooltip: str | None = None) -> Gtk.MenuItem:
    item = Gtk.MenuItem(label=label)
    item.set_sensitive(False)
    if tooltip:
        item.set_tooltip_text(tooltip)
    return item


def _provider_item(provider: ProviderStatus) -> Gtk.MenuItem:
    if not provider.available:
        reason = provider.unavailable_reason or "Unavailable"
        return _info_item(f"{provider.display_name} · {reason}")

    item = Gtk.MenuItem(
        label=f"{provider.display_name} · {provider.remaining_percent}%"
    )
    details = Gtk.Menu()
    for window in provider.windows:
        reset = reset_description(window)
        suffix = f" · resets {reset}" if reset else ""
        details.append(
            _info_item(f"{window.name}: {window.remaining_percent}%{suffix}")
        )
    if provider.plan_type:
        details.append(Gtk.SeparatorMenuItem())
        details.append(_info_item(f"Plan: {provider.plan_type.title()}"))
    details.show_all()
    item.set_submenu(details)
    return item


def _open_history() -> HistoryStore:
    """Open the history database, degrading to memory rather than failing.

    History is an enhancement; a read-only home directory or a corrupt file
    should cost the session its charts, not its indicator.
    """
    try:
        history = HistoryStore()
        history.prune()
        return history
    except (OSError, sqlite3.Error) as exc:
        LOGGER.warning("Usage history unavailable, keeping it in memory: %s", exc)
        return HistoryStore(memory=True)


def _short_error(message: str) -> str:
    value = message.strip().splitlines()[-1] if message.strip() else "Unavailable"
    return value[:80] + ("…" if len(value) > 80 else "")


def main() -> None:
    log_path = configure_logging()
    instance_lock = InstanceLock()
    if not instance_lock.acquire():
        print("quota-ring: already running", file=sys.stderr)
        return
    try:
        initialized, _argv = Gtk.init_check()
        if not initialized:
            raise RuntimeError(
                "Could not connect to the GNOME display. Start the indicator "
                "from the desktop session."
            )
        QuotaRingIndicator(Config.load()).run()
    except (RuntimeError, ValueError) as exc:
        LOGGER.exception("Quota Ring could not start")
        print(f"quota-ring: {exc}", file=sys.stderr)
        print(f"See {log_path} for details.", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        instance_lock.release()


if __name__ == "__main__":
    main()
