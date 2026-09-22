#!/bin/sh
set -eu

project_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
config_home=${XDG_CONFIG_HOME:-"$HOME/.config"}
bin_home=${XDG_BIN_HOME:-"$HOME/.local/bin"}
autostart=true
launch=false

for argument in "$@"; do
    case "$argument" in
        --no-autostart) autostart=false ;;
        --launch) launch=true ;;
        *)
            printf 'Usage: %s [--no-autostart] [--launch]\n' "$0" >&2
            exit 2
            ;;
    esac
done

if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("AyatanaAppIndicator3", "0.1")' >/dev/null 2>&1; then
    printf '%s\n' 'Quota Ring requires python3-gi, GTK 3, and Ayatana AppIndicator.' >&2
    printf '%s\n' 'On Ubuntu: sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1' >&2
    exit 1
fi

app_dir="$data_home/quota-ring"
autostart_dir="$config_home/autostart"
launcher="$bin_home/quota-ring"
applications_dir="$data_home/applications"
icons_dir="$data_home/icons/hicolor"
desktop_file="$applications_dir/io.github.cduerr.QuotaRing.desktop"
autostart_file="$autostart_dir/io.github.cduerr.QuotaRing.desktop"
legacy_config="$config_home/codex-usage-indicator/config.json"
config_file="$config_home/quota-ring/config.json"

install -d \
    "$app_dir/quota_ring/assets/icons" \
    "$bin_home" \
    "$applications_dir" \
    "$autostart_dir" \
    "$icons_dir/scalable/apps" \
    "$icons_dir/symbolic/apps"
install -m 0644 "$project_dir"/src/quota_ring/*.py \
    "$app_dir/quota_ring/"
install -m 0644 "$project_dir"/src/quota_ring/assets/icons/*.svg \
    "$app_dir/quota_ring/assets/icons/"

sed "s|@APP_DIR@|$app_dir|g" "$project_dir/packaging/quota-ring.in" \
    > "$launcher"
chmod 0755 "$launcher"

sed \
    -e "s|@EXEC_PATH@|$launcher|g" \
    "$project_dir/packaging/quota-ring.desktop.in" \
    > "$desktop_file"
chmod 0644 "$desktop_file"

install -m 0644 \
    "$project_dir/packaging/icons/io.github.cduerr.QuotaRing.svg" \
    "$icons_dir/scalable/apps/io.github.cduerr.QuotaRing.svg"
install -m 0644 \
    "$project_dir/packaging/icons/io.github.cduerr.QuotaRing-symbolic.svg" \
    "$icons_dir/symbolic/apps/io.github.cduerr.QuotaRing-symbolic.svg"

if [ "$autostart" = true ]; then
    sed "s|@EXEC_PATH@|$launcher|g" \
        "$project_dir/packaging/quota-ring-autostart.desktop.in" \
        > "$autostart_file"
    chmod 0644 "$autostart_file"
else
    rm -f -- "$autostart_file"
fi

if [ ! -e "$config_file" ] && [ -f "$legacy_config" ]; then
    install -d "${config_file%/*}"
    install -m 0600 "$legacy_config" "$config_file"
    printf 'Migrated settings to %s\n' "$config_file"
fi

# Remove launch points from the pre-rename development build. Its settings are
# intentionally retained so they can be migrated safely.
rm -f -- \
    "$autostart_dir/codex-usage-indicator.desktop" \
    "$bin_home/codex-usage-indicator"

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -f "$icons_dir" 2>/dev/null || true
fi

# Pick up the freshly installed code: a running indicator is still executing
# the previous revision from memory.
process_pattern='python3 -m quota_ring.app'
was_running=false
restarted=false
if pgrep -f "$process_pattern" >/dev/null 2>&1; then
    was_running=true
    pkill -f "$process_pattern" || true
    # Wait for it to exit so it releases the single-instance lock before the
    # replacement tries to take it.
    attempt=0
    while pgrep -f "$process_pattern" >/dev/null 2>&1 && [ "$attempt" -lt 50 ]; do
        sleep 0.1
        attempt=$((attempt + 1))
    done
    if pgrep -f "$process_pattern" >/dev/null 2>&1; then
        pkill -KILL -f "$process_pattern" || true
        sleep 0.5
    fi
fi

if [ "$was_running" = true ] || [ "$launch" = true ]; then
    # Hand the replacement to the user service manager when available so it
    # survives this installer process exiting. A detached session is the
    # fallback for desktops without a working systemd user manager.
    if command -v systemd-run >/dev/null 2>&1 \
        && systemctl --user show-environment >/dev/null 2>&1; then
        systemd-run --user --quiet --collect "$launcher"
    else
        setsid -f "$launcher" </dev/null >/dev/null 2>&1
    fi

    # Starting in the background can succeed even when the application exits
    # immediately. Do not claim a restart until the new process is observable.
    attempt=0
    while ! pgrep -f "$process_pattern" >/dev/null 2>&1 \
        && [ "$attempt" -lt 50 ]; do
        sleep 0.1
        attempt=$((attempt + 1))
    done
    if ! pgrep -f "$process_pattern" >/dev/null 2>&1; then
        printf '%s\n' 'Installed Quota Ring, but could not launch the indicator.' >&2
        printf 'Run it manually with: %s\n' "$launcher" >&2
        exit 1
    fi
    restarted=true
fi

printf 'Installed Quota Ring.\n'
if [ "$restarted" = true ]; then
    if [ "$was_running" = true ]; then
        printf 'Restarted the running indicator on the new version.\n'
    else
        printf 'Launched the indicator.\n'
    fi
else
    printf 'Run it now with: %s\n' "$launcher"
fi
if [ "$autostart" = true ]; then
    printf 'It will start automatically at the next desktop login.\n'
fi
