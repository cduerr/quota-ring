from __future__ import annotations

import json
import os
import pty
import re
import selectors
import shlex
import shutil
import subprocess
import termios
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Callable

from quota_ring.config import Config
from quota_ring.models import ProviderStatus, UsageWindow


class CodexClient:
    def __init__(self, config: Config):
        self.config = config

    def fetch(self) -> ProviderStatus:
        command = [
            *_resolve_command(
                self.config.codex_command, os.path.expanduser("~/.local/bin/codex")
            ),
            "app-server",
            "--stdio",
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=os.environ.copy(),
            )
        except OSError as exc:
            raise RuntimeError(f"Could not start Codex: {exc}") from exc

        try:
            self._send(
                process,
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {
                            "name": "llm-usage-indicator",
                            "title": "Quota Ring",
                            "version": "0.2.0",
                        }
                    },
                },
            )
            self._response(process, 1)
            self._send(process, {"method": "initialized"})
            self._send(
                process,
                {"id": 2, "method": "account/rateLimits/read", "params": None},
            )
            return ProviderStatus.codex_response(self._response(process, 2))
        finally:
            _stop_process(process)

    @staticmethod
    def _send(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
        if process.stdin is None:
            raise RuntimeError("Codex app server input is unavailable")
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def _response(self, process: subprocess.Popen[str], request_id: int) -> dict[str, Any]:
        if process.stdout is None:
            raise RuntimeError("Codex app server output is unavailable")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + self.config.request_timeout_seconds
        try:
            while time.monotonic() < deadline:
                ready = selector.select(max(0, deadline - time.monotonic()))
                if not ready:
                    break
                line = process.stdout.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    error = message["error"]
                    detail = error.get("message", error) if isinstance(error, dict) else error
                    raise RuntimeError(f"Codex error: {detail}")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError("Codex returned an invalid response")
                return result
        finally:
            selector.close()
        detail = ""
        if process.poll() is not None and process.stderr is not None:
            detail = process.stderr.read().strip()
        if detail:
            raise RuntimeError(detail.splitlines()[-1])
        raise RuntimeError("Timed out waiting for Codex usage")


class KimiClient:
    def __init__(self, config: Config):
        self.config = config

    def fetch(self) -> ProviderStatus:
        command = [
            *_resolve_command(
                self.config.kimi_command, os.path.expanduser("~/.kimi-code/bin/kimi")
            ),
            "web",
            "--no-open",
            "--port",
            "58690",
        ]
        master, slave = pty.openpty()
        termios.tcsetwinsize(slave, (24, 100))
        try:
            process = subprocess.Popen(
                command,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
                env=os.environ.copy(),
            )
        except OSError as exc:
            os.close(master)
            os.close(slave)
            raise RuntimeError(f"Could not start Kimi: {exc}") from exc
        os.close(slave)
        try:
            base_url, token = self._wait_for_server(master)
            request = urllib.request.Request(
                f"{base_url}api/v1/oauth/usage",
                headers={"Authorization": f"Bearer {token}"},
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.config.request_timeout_seconds
                ) as response:
                    payload = json.load(response)
            except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Could not read Kimi usage: {exc}") from exc
            return _parse_kimi_response(payload)
        finally:
            os.close(master)
            _stop_process(process)

    def _wait_for_server(self, master: int) -> tuple[str, str]:
        deadline = time.monotonic() + self.config.request_timeout_seconds
        output = ""
        while time.monotonic() < deadline:
            readable, _, _ = select_select([master], [], [], 0.25)
            if readable:
                try:
                    output += _clean_terminal(
                        os.read(master, 65536).decode("utf-8", errors="replace")
                    )
                except OSError:
                    break
            url = re.search(r"Local:\s+(http://127\.0\.0\.1:\d+/)", output)
            token = re.search(r"Token:\s+([A-Za-z0-9_-]+)", output)
            if url and token:
                return url.group(1), token.group(1)
        raise RuntimeError("Timed out starting Kimi usage service")


class ClaudeClient:
    def __init__(self, config: Config):
        self.config = config

    def fetch(self) -> ProviderStatus:
        command = _resolve_command(
            self.config.claude_command, os.path.expanduser("~/.local/bin/claude")
        )
        try:
            auth = subprocess.run(
                [*command, "auth", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=self.config.request_timeout_seconds,
                check=False,
            )
            auth_status = json.loads(auth.stdout or "{}")
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not check Claude login: {exc}") from exc
        if not auth_status.get("loggedIn"):
            raise RuntimeError("Not logged in")
        output = _query_tui(
            command,
            ready=lambda text: "shortcuts" in text.lower(),
            complete=lambda text: (
                "Failed to load usage data" in text
                or ("currentsession" in _compact_terminal(text) and "%" in text)
            ),
            timeout=max(20, self.config.request_timeout_seconds),
            cwd=_claude_trusted_cwd(),
        )
        windows = _parse_claude_usage(output)
        if not windows:
            if "Failed to load usage data" in output:
                raise RuntimeError("No active allowance")
            raise RuntimeError("Claude usage unavailable")
        return ProviderStatus("claude", "Claude Code", windows=tuple(windows))


def _query_tui(
    command: list[str],
    ready: Callable[[str], bool],
    complete: Callable[[str], bool],
    timeout: int,
    cwd: str | None = None,
) -> str:
    master, slave = pty.openpty()
    termios.tcsetwinsize(slave, (36, 100))
    environment = os.environ.copy()
    environment.update({"TERM": "xterm-256color", "NO_COLOR": "1"})
    try:
        process = subprocess.Popen(
            command,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            env=environment,
            cwd=cwd,
        )
    except OSError as exc:
        os.close(master)
        os.close(slave)
        raise RuntimeError(f"Could not start {command[0]}: {exc}") from exc
    os.close(slave)
    raw = bytearray()
    sent_usage = False
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select_select([master], [], [], 0.25)
            if readable:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                raw.extend(chunk)
            text = _clean_terminal(raw.decode("utf-8", errors="replace"))
            if not sent_usage and ready(text):
                os.write(master, b"/usage\r")
                sent_usage = True
            elif sent_usage and complete(text):
                return text
        text = _clean_terminal(raw.decode("utf-8", errors="replace"))
        if not sent_usage:
            raise RuntimeError(f"{command[0]} did not become ready")
        return text
    finally:
        try:
            os.write(master, b"\x1b/exit\r")
        except OSError:
            pass
        os.close(master)
        _stop_process(process)


def _parse_kimi_usage(text: str) -> list[UsageWindow]:
    windows: list[UsageWindow] = []
    for name, pattern in (
        ("Weekly", r"Weekly limit[^\n]*?(\d+)% used\s+resets in\s+([^\n│]+)"),
        ("5-hour", r"5h limit[^\n]*?(\d+)% used\s+resets in\s+([^\n│]+)"),
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            windows.append(
                UsageWindow(
                    name=name,
                    used_percent=int(match.group(1)),
                    reset_text=f"in {match.group(2).strip()}",
                )
            )
    return windows


def _parse_kimi_response(payload: dict[str, Any]) -> ProviderStatus:
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("kind") != "ok":
        reason = data.get("message") if isinstance(data, dict) else None
        raise RuntimeError(str(reason or "Kimi usage unavailable"))
    raw_limits = data.get("limits")
    if not isinstance(raw_limits, list):
        raw_limits = []
    summary = data.get("summary")
    raw_entries = ([summary] if isinstance(summary, dict) else []) + raw_limits
    windows: list[UsageWindow] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        used, limit = raw.get("used"), raw.get("limit")
        if not isinstance(used, (int, float)) or not isinstance(limit, (int, float)):
            continue
        if limit <= 0:
            continue
        window = raw.get("window") if isinstance(raw.get("window"), dict) else {}
        duration, unit = window.get("duration"), window.get("unit")
        name = str(raw.get("name") or _duration_name(duration, unit))
        windows.append(
            UsageWindow(
                name=name,
                used_percent=round(used / limit * 100),
                resets_at=_iso_timestamp(raw.get("reset_at")),
            )
        )
    if not windows:
        raise RuntimeError("Kimi returned no plan usage")
    return ProviderStatus("kimi", "Kimi", windows=tuple(windows))


def _parse_claude_usage(text: str) -> list[UsageWindow]:
    compact = _compact_terminal(_clean_terminal(text))
    windows: list[UsageWindow] = []
    for label, name in (
        ("currentsession", "5-hour"),
        ("currentweek(allmodels)", "Weekly"),
        ("currentweek(sonnetonly)", "Weekly Sonnet"),
    ):
        position = compact.rfind(label)
        if position < 0:
            continue
        section = compact[position : position + 500]
        percent = re.search(r"(\d+)%", section)
        if not percent:
            continue
        reset = re.search(r"resetsin([^\n│]+)", section, re.IGNORECASE)
        windows.append(
            UsageWindow(
                name=name,
                used_percent=int(percent.group(1)),
                reset_text=f"in {reset.group(1).strip()}" if reset else None,
            )
        )
    return windows


def _clean_terminal(text: str) -> str:
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1bP.*?\x1b\\", "", text, flags=re.DOTALL)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\x1b[()][A-Z0-9]", "", text)
    text = text.replace("\r", "")
    return text


def _compact_terminal(text: str) -> str:
    return re.sub(r"[ \t]", "", text).lower()


def _duration_name(duration: object, unit: object) -> str:
    if duration == 5 and unit == "hour":
        return "5-hour"
    if duration == 1 and unit == "week":
        return "Weekly"
    return f"{duration}-{unit}" if duration and unit else "Usage"


def _iso_timestamp(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()


def _resolve_command(configured: str, fallback_path: str) -> list[str]:
    command = shlex.split(configured)
    if not command:
        raise RuntimeError("Provider command is empty")
    executable = os.path.expanduser(command[0])
    if os.path.sep in executable or shutil.which(executable):
        command[0] = executable
        return command
    if os.path.isfile(fallback_path) and os.access(fallback_path, os.X_OK):
        command[0] = fallback_path
        return command
    return command


def _claude_trusted_cwd() -> str | None:
    config_path = os.path.expanduser("~/.claude.json")
    try:
        with open(config_path, encoding="utf-8") as config_file:
            projects = json.load(config_file).get("projects", {})
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(projects, dict):
        return None
    current = os.getcwd()
    current_settings = projects.get(current)
    if (
        isinstance(current_settings, dict)
        and current_settings.get("hasTrustDialogAccepted") is True
    ):
        return current
    trusted = [
        path
        for path, settings in projects.items()
        if isinstance(path, str)
        and isinstance(settings, dict)
        and settings.get("hasTrustDialogAccepted") is True
        and os.path.isdir(path)
    ]
    return trusted[-1] if trusted else None


# Kept as an alias so tests can replace it without patching the select module.
from select import select as select_select  # noqa: E402
