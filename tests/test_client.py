import os
import termios
import unittest
from unittest.mock import MagicMock, patch

from quota_ring.client import (
    CodexClient,
    _available_loopback_port,
    _clean_terminal,
    _kimi_command,
    _parse_claude_usage,
    _parse_kimi_response,
    _parse_kimi_usage,
    _resolve_command,
    _set_terminal_size,
)
from quota_ring.config import Config


class FakeProcess:
    def __init__(self, output: str):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, output.encode())
        os.close(write_fd)
        self.stdout = os.fdopen(read_fd)
        self.stderr = None

    def poll(self):
        return None


class ClientTests(unittest.TestCase):
    def test_response_ignores_notifications(self):
        process = FakeProcess(
            '{"method":"notice","params":{}}\n{"id":2,"result":{"rateLimits":{}}}\n'
        )
        result = CodexClient(Config())._response(process, 2)  # type: ignore[arg-type]
        self.assertEqual(result, {"rateLimits": {}})
        process.stdout.close()

    def test_parse_kimi_usage(self):
        windows = _parse_kimi_usage(
            "Weekly limit ███░ 31% used resets in 1d 23h 40m\n"
            "5h limit ░░░░ 0% used resets in 1h 40m\n"
        )
        self.assertEqual([window.remaining_percent for window in windows], [69, 100])
        self.assertEqual(windows[0].reset_text, "in 1d 23h 40m")

    def test_parse_kimi_server_response(self):
        status = _parse_kimi_response(
            {
                "data": {
                    "kind": "ok",
                    "summary": {
                        "window": {"duration": 1, "unit": "week"},
                        "used": 31,
                        "limit": 100,
                        "reset_at": "2026-08-05T12:00:00Z",
                    },
                    "limits": [],
                }
            }
        )
        self.assertEqual(status.remaining_percent, 69)
        self.assertEqual(status.windows[0].name, "Weekly")

    def test_parse_claude_usage(self):
        windows = _parse_claude_usage(
            "Current session 12% used resets in 2h\n"
            "Current week (all models) 72% used resets in 3d\n"
        )
        self.assertEqual([window.remaining_percent for window in windows], [88, 28])

    def test_parse_claude_cursor_positioned_usage(self):
        windows = _parse_claude_usage(
            "Current\x1b[10Gsession\x1b[30G12% used\x1b[50Gresets in 2h\n"
            "Current\x1b[10Gweek (all models)\x1b[40G72% used\x1b[60Gresets in 3d\n"
        )
        self.assertEqual([window.remaining_percent for window in windows], [88, 28])

    def test_terminal_codes_are_removed(self):
        self.assertEqual(_clean_terminal("\x1b[31mred\x1b[0m\r\n"), "red\n")

    def test_command_uses_installed_fallback(self):
        self.assertEqual(
            _resolve_command("missing-kimi --flag", "/bin/sh"),
            ["/bin/sh", "--flag"],
        )

    def test_kimi_uses_an_available_loopback_port(self):
        listener = MagicMock()
        listener.__enter__.return_value = listener
        listener.getsockname.return_value = ("127.0.0.1", 43123)
        with patch("quota_ring.client.socket.socket", return_value=listener):
            self.assertEqual(_available_loopback_port(), 43123)
        listener.bind.assert_called_once_with(("127.0.0.1", 0))

    def test_kimi_command_uses_selected_port(self):
        self.assertEqual(
            _kimi_command(Config(kimi_command="/bin/echo"), 43123),
            ["/bin/echo", "web", "--no-open", "--port", "43123"],
        )

    def test_terminal_size_uses_native_python_api(self):
        with patch("quota_ring.client.termios.tcsetwinsize", create=True) as set_size:
            _set_terminal_size(7, 24, 100)
        set_size.assert_called_once_with(7, (24, 100))

    def test_terminal_size_falls_back_to_ioctl(self):
        with (
            patch("quota_ring.client.termios.tcsetwinsize", None, create=True),
            patch("fcntl.ioctl") as ioctl,
        ):
            _set_terminal_size(7, 24, 100)
        self.assertEqual(ioctl.call_args.args[:2], (7, termios.TIOCSWINSZ))
