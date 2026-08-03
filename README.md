# Quota Ring

A small native Ubuntu/GNOME AppIndicator showing the remaining percentage in
your most constrained Codex, Kimi, or Claude Code usage window.

- Red: 15% or less remaining
- Orange: 25% or less remaining
- Yellow: 40% or less remaining
- Green: more than 40% remaining
- Gray: usage is unavailable

The icon is a small terminal fuel gauge. Click it to see each provider, every
available usage window, reset times, refresh status, settings, and Quit.

- Codex uses the local app-server protocol.
- Kimi briefly starts its authenticated loopback service and reads its
  documented usage endpoint. It does not create chat sessions.
- Claude Code checks local login state and uses its `/usage` screen when logged
  in.

Unavailable or logged-out providers appear gray in the menu and do not affect
the overall gauge. The indicator invokes the installed CLIs and never copies or
stores their account credentials.

## Requirements

Enable whichever CLIs you use in Settings. Ubuntu 24.04 normally provides the
remaining desktop dependencies; if needed:

```sh
sudo apt install python3-gi gir1.2-gtk-3.0 \
  gir1.2-ayatanaappindicator3-0.1 gnome-shell-extension-appindicator
codex login
kimi login
claude login
```

Run during development with the system Python, so Ubuntu's GI packages are
available:

```sh
PYTHONPATH=src /usr/bin/python3 -m quota_ring.app
```

Install for the current user and enable GNOME autostart:

```sh
chmod +x scripts/install.sh
./scripts/install.sh
~/.local/bin/quota-ring
```

## Settings

The menu's **Settings…** dialog enables providers and changes their CLI commands
and refresh intervals. The normal interval defaults to five minutes; below 5%
remaining, the indicator switches to a 90-second interval by default.
Configuration is stored at:

```text
~/.config/quota-ring/config.json
```

Example:

```json
{
  "codex_command": "codex",
  "kimi_command": "kimi",
  "claude_command": "claude",
  "codex_enabled": true,
  "kimi_enabled": true,
  "claude_enabled": true,
  "poll_seconds": 300,
  "low_poll_seconds": 90,
  "request_timeout_seconds": 15
}
```

Set `QUOTA_RING_CONFIG` to use a different config path.
