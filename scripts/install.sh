#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
config_home=${XDG_CONFIG_HOME:-"$HOME/.config"}
bin_home=${XDG_BIN_HOME:-"$HOME/.local/bin"}

app_dir="$data_home/quota-ring"
autostart_dir="$config_home/autostart"
launcher="$bin_home/quota-ring"
desktop_file="$autostart_dir/quota-ring.desktop"

install -d "$app_dir/quota_ring/assets/icons" "$bin_home" "$autostart_dir"
install -m 0644 "$project_dir"/src/quota_ring/*.py \
    "$app_dir/quota_ring/"
install -m 0644 "$project_dir"/src/quota_ring/assets/icons/*.svg \
    "$app_dir/quota_ring/assets/icons/"

sed "s|@APP_DIR@|$app_dir|g" "$project_dir/packaging/quota-ring.in" \
    > "$launcher"
chmod 0755 "$launcher"

sed \
    -e "s|@EXEC_PATH@|$launcher|g" \
    -e "s|@ICON_PATH@|$app_dir/quota_ring/assets/icons/quota-ring-green.svg|g" \
    "$project_dir/packaging/quota-ring.desktop.in" \
    > "$desktop_file"
chmod 0644 "$desktop_file"

printf 'Installed Quota Ring.\n'
printf 'Run it now with: %s\n' "$launcher"
printf 'It will start automatically at the next GNOME login.\n'
