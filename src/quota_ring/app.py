from __future__ import annotations

import sys
import threading
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3, GLib, Gtk  # noqa: E402

from quota_ring.client import ClaudeClient, CodexClient, KimiClient
from quota_ring.config import Config
from quota_ring.models import (
    DashboardStatus,
    ProviderStatus,
    icon_name,
    refresh_interval,
)
from quota_ring.runtime import InstanceLock, configure_logging


LOGGER = logging.getLogger(__name__)


class QuotaRingIndicator:
    def __init__(self, config: Config):
        self.config = config
        self._refreshing = False
        self._last_checked: datetime | None = None
        self._status: DashboardStatus | None = None
        self._error: str | None = None
        self._timer_id: int | None = None
        self._icon_timer_id: int | None = None
        self._critical_remaining: int | None = None
        self._icon_light_phase = False
        self.icon_dir = Path(__file__).resolve().parent / "assets" / "icons"
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "quota-ring",
            "quota-ring-unknown",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_icon_theme_path(str(self.icon_dir))
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
            f"LLM usage: {remaining}% remaining"
            if remaining is not None
            else "LLM usage unavailable"
        )
        self._set_usage_icon(remaining, description)
        self.indicator.set_title(description)
        self._schedule_refresh()
        self._rebuild_menu()
        return False

    def _apply_error(self, message: str) -> bool:
        LOGGER.error("Refresh failed: %s", message)
        self._status = None
        self._error = message
        self._last_checked = datetime.now().astimezone()
        self._refreshing = False
        self._stop_icon_animation()
        self.indicator.set_icon_full("quota-ring-unknown", "LLM usage unavailable")
        self.indicator.set_title("LLM usage unavailable")
        self._rebuild_menu()
        return False

    def _set_usage_icon(self, remaining: int | None, description: str) -> None:
        self._stop_icon_animation()
        self.indicator.set_icon_full(icon_name(remaining), description)
        if remaining is not None and 0 <= remaining <= 2:
            self._critical_remaining = remaining
            self._icon_timer_id = GLib.timeout_add(1400, self._pulse_icon)

    def _pulse_icon(self) -> bool:
        if self._critical_remaining is None:
            return False
        self._icon_light_phase = not self._icon_light_phase
        shade = "light" if self._icon_light_phase else "red"
        remaining = self._critical_remaining
        self.indicator.set_icon_full(
            f"quota-ring-critical-{remaining}-{shade}",
            f"LLM usage: {remaining}% remaining",
        )
        return True

    def _stop_icon_animation(self) -> None:
        if self._icon_timer_id is not None:
            GLib.source_remove(self._icon_timer_id)
            self._icon_timer_id = None
        self._critical_remaining = None
        self._icon_light_phase = False

    def _rebuild_menu(self) -> None:
        menu = Gtk.Menu()
        if self._error and not self._status:
            menu.append(_info_item("Usage unavailable"))
            menu.append(_info_item(self._error, self._error))
        elif self._status:
            overall = self._status.remaining_percent
            menu.append(
                _info_item(
                    f"Overall · {overall}% remaining"
                    if overall is not None
                    else "Overall · unavailable"
                )
            )
            for index, provider in enumerate(self._status.providers):
                if index:
                    menu.append(Gtk.SeparatorMenuItem())
                if provider.available:
                    menu.append(
                        _info_item(
                            f"{provider.display_name} · {provider.remaining_percent}% remaining"
                        )
                    )
                    for window in provider.windows:
                        reset = ""
                        if window.reset_datetime:
                            reset = window.reset_datetime.strftime(" · resets %a %-I:%M %p")
                        elif window.reset_text:
                            reset = f" · resets {window.reset_text}"
                        menu.append(
                            _info_item(
                                f"  {window.name}: {window.remaining_percent}% remaining{reset}"
                            )
                        )
                    if provider.plan_type:
                        menu.append(_info_item(f"  Plan: {provider.plan_type.title()}"))
                else:
                    reason = provider.unavailable_reason or "Unavailable"
                    menu.append(_info_item(f"{provider.display_name} · {reason}"))
        else:
            menu.append(_info_item("Waiting for first update…"))

        if self._refreshing:
            menu.append(Gtk.SeparatorMenuItem())
            menu.append(_info_item("Refreshing…"))

        menu.append(Gtk.SeparatorMenuItem())
        checked = (
            self._last_checked.strftime("%-I:%M:%S %p") if self._last_checked else "Never"
        )
        menu.append(_info_item(f"Last checked: {checked}"))
        refresh_item = Gtk.MenuItem(label="Refresh")
        refresh_item.set_sensitive(not self._refreshing)
        refresh_item.connect("activate", lambda _item: self.refresh())
        menu.append(refresh_item)

        settings_item = Gtk.MenuItem(label="Settings…")
        settings_item.connect("activate", self._show_settings)
        menu.append(settings_item)
        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _item: Gtk.main_quit())
        menu.append(quit_item)
        menu.show_all()
        self.indicator.set_menu(menu)

    def _show_settings(self, _item: Gtk.MenuItem) -> None:
        dialog = Gtk.Dialog(title="Quota Ring Settings")
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.OK)
        dialog.set_default_size(430, -1)
        grid = Gtk.Grid(column_spacing=12, row_spacing=10, margin=16)
        provider_rows = (
            ("Codex", self.config.codex_enabled, self.config.codex_command),
            ("Kimi", self.config.kimi_enabled, self.config.kimi_command),
            ("Claude Code", self.config.claude_enabled, self.config.claude_command),
        )
        controls = []
        for row, (label, enabled, value) in enumerate(provider_rows):
            checkbox = Gtk.CheckButton(label=label)
            checkbox.set_active(enabled)
            command = Gtk.Entry(text=value)
            command.set_sensitive(enabled)
            checkbox.connect(
                "toggled", lambda button, entry=command: entry.set_sensitive(button.get_active())
            )
            grid.attach(checkbox, 0, row, 1, 1)
            grid.attach(command, 1, row, 1, 1)
            controls.append((checkbox, command))
        poll = Gtk.SpinButton.new_with_range(30, 3600, 30)
        poll.set_value(self.config.poll_seconds)
        grid.attach(Gtk.Label(label="Normal refresh (seconds)", xalign=0), 0, 3, 1, 1)
        grid.attach(poll, 1, 3, 1, 1)
        low_poll = Gtk.SpinButton.new_with_range(30, 3600, 30)
        low_poll.set_value(self.config.low_poll_seconds)
        grid.attach(Gtk.Label(label="Below 5% refresh (seconds)", xalign=0), 0, 4, 1, 1)
        grid.attach(low_poll, 1, 4, 1, 1)
        note = Gtk.Label(
            label="Uses each CLI’s existing local login. Credentials are not copied or stored.",
            xalign=0,
            wrap=True,
        )
        grid.attach(note, 0, 5, 2, 1)
        dialog.get_content_area().add(grid)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            new_config = replace(
                self.config,
                codex_enabled=controls[0][0].get_active(),
                codex_command=controls[0][1].get_text().strip() or "codex",
                kimi_enabled=controls[1][0].get_active(),
                kimi_command=controls[1][1].get_text().strip() or "kimi",
                claude_enabled=controls[2][0].get_active(),
                claude_command=controls[2][1].get_text().strip() or "claude",
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


def _info_item(label: str, tooltip: str | None = None) -> Gtk.MenuItem:
    item = Gtk.MenuItem(label=label)
    item.set_sensitive(False)
    if tooltip:
        item.set_tooltip_text(tooltip)
    return item


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
                "Could not connect to the GNOME display. Start the indicator from the desktop session."
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
