#!/bin/sh
set -eu

data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
config_home=${XDG_CONFIG_HOME:-"$HOME/.config"}
cache_home=${XDG_CACHE_HOME:-"$HOME/.cache"}
state_home=${XDG_STATE_HOME:-"$HOME/.local/state"}
bin_home=${XDG_BIN_HOME:-"$HOME/.local/bin"}
purge=false

if [ "${1:-}" = "--purge" ]; then
    purge=true
elif [ "$#" -gt 0 ]; then
    printf 'Usage: %s [--purge]\n' "$0" >&2
    exit 2
fi

rm -rf -- "$data_home/quota-ring"
rm -f -- \
    "$bin_home/quota-ring" \
    "$data_home/applications/io.github.cduerr.QuotaRing.desktop" \
    "$config_home/autostart/io.github.cduerr.QuotaRing.desktop" \
    "$data_home/icons/hicolor/scalable/apps/io.github.cduerr.QuotaRing.svg" \
    "$data_home/icons/hicolor/symbolic/apps/io.github.cduerr.QuotaRing-symbolic.svg"

if [ "$purge" = true ]; then
    rm -rf -- "$config_home/quota-ring" "$cache_home/quota-ring" \
        "$state_home/quota-ring"
    printf 'Removed Quota Ring, settings, usage history, and logs.\n'
else
    printf 'Removed Quota Ring. Settings, usage history, and logs were preserved.\n'
fi
