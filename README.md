# Quota Ring

Quota Ring is a lightweight Linux desktop indicator that keeps the remaining
allowance for AI coding plans visible at a glance. It currently supports Codex,
Kimi, and Claude Code through their existing local CLI logins.

![Status: beta](https://img.shields.io/badge/status-beta-orange)
![License: MIT](https://img.shields.io/badge/license-MIT-blue)

## What it shows

The indicator shows three concentric gauge rings — one per provider (Codex,
Kimi, and Claude Code). Each ring fills with the remaining percentage of the
provider's most constrained active window. Its color shows the worst forecast
among that provider's windows:

- White when projected to finish with at least 50% left
- Blue at 25–49% projected left
- Green at 0–24% projected left
- Yellow when projected to spend 1–9% over budget
- Orange when projected to spend 10–24% over budget
- Red when spent or projected to spend at least 25% over budget
- Gray while the forecast is unavailable or the window is still settling
- A slow red-to-pale pulse at 0–2% actually remaining

A disabled or unavailable provider keeps a faint empty ring. Open Settings to
assign providers to the outer, middle, and inner rings with the arrow buttons
on each provider row.

Click the indicator to see each provider, every reported usage window, reset
times, refresh state, and the last successful check. A failed or logged-out
provider remains visible in the menu but does not lower the overall percentage.

## Insights

**Insights…** at the top of the menu opens a window that answers a different
question than the rings: not how much is left, but whether it will last.

Each provider reports how long a window runs and when it resets, so the window
start is derivable, and with it the share of the allowance spent against the
share of the window elapsed. That ratio is the pace. At 1.0 the allowance runs
out exactly at the reset; above it, the reset arrives too late.

The chart plots spend against elapsed time. The dashed diagonal is spending
perfectly in step with the window, and a curve above it is on course to run
dry early — the projection marks where. Percentages are whole numbers, so
spending is a step function and is drawn as one rather than smoothed.

Pace estimates and ring colors begin after 5% of the window has elapsed, capped
at 15 minutes. Insights labels estimates as early until 15% of the window has
elapsed; initial bursts and whole-percent readings can make those projections
change sharply after a reset.

For Codex windows lasting at least a day, early estimates can allow for recurring
quiet hours learned from local token activity over the last three weeks. Learning
requires at least seven completed days with activity in six or more hours, and
a quiet stretch of 4–10 hours seen on at least 80% of those days. Auto review
activity is excluded. This describes local inactivity, rather than actual sleep
or work on other devices.

The adjustment applies only during the first 24 hours, when the sample contains
more active hours than a typical full day. It ends once a full day is represented
and falls back to the ordinary estimate when history is insufficient. Insights
shows the learned local hours and how many days support them while it is applied.
The estimate still uses the provider's reported reset time to derive the window
start; an inaccurate reset timestamp can still skew it.

The headline names the window that runs out *soonest*, which is not always the
one with the least left: a session window burning hard can empty long before a
weekly one sitting lower.

The Codex weekly chart splits its observed spend into colors using per-model
token records from the local Codex session logs. When repeated quota increases
contain only one model, Quota Ring estimates relative model rates from those
isolated steps. Until there is enough evidence, it uses weighted token share.
The legend states which method is active. Quota increases with no matching
local token activity remain gray; this can include work from another machine or
cloud chat because OpenAI reports only the combined allowance.

Projections assume the current average rate simply continues. That reads high
for anyone who works in bursts — a weekly window looks alarming on a Friday
evening and recovers by Monday without anything changing. Treat it as "at this
rate", not as a forecast.

## Usage history

The Insights window draws on a local history database:

```text
~/.local/state/quota-ring/history.db
```

Only the moments at which spend changed are stored, along with a per-window
heartbeat so an idle stretch is distinguishable from the indicator not running.
Readings older than 90 days are dropped. The file is created with user-only
permissions and never leaves the machine; `./scripts/uninstall.sh --purge`
removes it. Charts covering past windows fill in as the indicator runs, so a
fresh install shows the current window only.

## Provider access

Quota Ring never copies or stores provider credentials:

- **Codex** uses the local `codex app-server` rate-limit method.
- **Kimi** briefly starts an authenticated loopback-only `kimi web` service on
  an available ephemeral port, reads its usage endpoint, and stops it.
- **Claude Code** checks local authentication and reads the `/usage` screen in a
  short-lived terminal session.

Because Claude Code's usage view is interactive, a future CLI UI change can
temporarily require a parser update. Provider failures are isolated and logged.

## Supported desktops

Ubuntu 24.04 with GNOME and the AppIndicator extension is the primary supported
environment. Quota Ring uses Ayatana AppIndicator/StatusNotifierItem and may
also work on KDE Plasma, Xfce, Cinnamon, and MATE, but those desktops are not yet
part of the release test matrix.

## Install

Install the provider CLIs you use and log in to each one. On Ubuntu, install the
desktop dependencies:

```sh
sudo apt install python3-gi gir1.2-gtk-3.0 \
  gir1.2-ayatanaappindicator3-0.1 gnome-shell-extension-appindicator
```

Then install Quota Ring for the current user:

```sh
git clone https://github.com/cduerr/quota-ring.git
cd quota-ring
./scripts/install.sh
~/.local/bin/quota-ring
```

The installer adds an application-grid entry and enables desktop autostart. Use
`./scripts/install.sh --no-autostart` to opt out of launch at login.
Pass `--launch` to start the indicator as part of installation.

To uninstall while retaining settings and diagnostics:

```sh
./scripts/uninstall.sh
```

Pass `--purge` to remove settings and logs as well.

## Settings and diagnostics

The indicator's **Settings…** dialog enables providers, changes CLI commands,
and controls two refresh intervals. The defaults are five minutes normally and
90 seconds below 5% remaining.

Settings are stored with user-only permissions at:

```text
~/.config/quota-ring/config.json
```

Rotating diagnostic logs are stored at:

```text
~/.cache/quota-ring/quota-ring.log
```

Set `QUOTA_RING_CONFIG` to use a different configuration file.

## Development

Run tests and the development build from the repository root:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src /usr/bin/python3 -m quota_ring.app
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the complete validation commands.

## License

Quota Ring is available under the [MIT License](LICENSE). It is an independent
project and is not affiliated with OpenAI, Anthropic, or Moonshot AI.
