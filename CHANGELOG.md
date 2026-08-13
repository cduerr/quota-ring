# Changelog

All notable changes to Quota Ring will be documented in this file. The project
uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Codex, Kimi, and Claude Code usage monitoring through existing local logins.
- A color-coded top-bar indicator with critical percentage and pulse states.
- Provider details, reset times, manual refresh, and persistent settings.
- Adaptive polling below 5% remaining.
- User-level installation, desktop autostart, and uninstall scripts.
- An **Insights…** window with per-window burn rate, a projected run-out time,
  and a burn-up chart against the on-pace line.
- Usage history in `~/.local/state/quota-ring/history.db`, recording only the
  points at which spend changed, pruned after 90 days.
- Window start times for Kimi and Claude Code, derived from the reported
  window length and reset time, which is what makes a pace calculable.

[Unreleased]: https://github.com/cduerr/quota-ring/commits/main
