# Changelog

All notable changes to Quota Ring will be documented in this file. The project
uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Codex, Kimi, and Claude Code usage monitoring through existing local logins.
- A color-coded top-bar indicator with critical percentage and pulse states.
- Forecast-based ring and Insights chart colors, with white and blue for large
  projected reserves.
- Provider details, reset times, manual refresh, and persistent settings.
- Adaptive polling below 5% remaining.
- User-level installation, desktop autostart, and uninstall scripts.
- An **Insights…** window with per-window burn rate, a projected run-out time,
  and a burn-up chart against the on-pace line.
- Usage history in `~/.local/state/quota-ring/history.db`, recording only the
  points at which spend changed, pruned after 90 days.
- Window start times for Kimi and Claude Code, derived from the reported
  window length and reset time, which is what makes a pace calculable.
- A stacked Codex weekly chart that backfills local per-model token usage and
  estimates relative model rates from isolated quota increases.

### Fixed

- Cap the forecast settling period at 15 minutes and show ring colors with
  early estimates, avoiding hours of gray after a weekly reset.
- Give GPT-6 Sol, GPT-6.1 Sol, and GPT-6 Luna distinct Insights colors instead
  of relying on fallback colors that can collide with other models.
- Include current usage from resumed Codex threads whose rollout files remain
  under an older creation-date directory.

[Unreleased]: https://github.com/cduerr/quota-ring/commits/main
