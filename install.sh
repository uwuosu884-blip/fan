#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# omen88d2 installer for Arch Linux (works on most systemd distros too).
set -euo pipefail

PREFIX=/opt/omen88d2
BIN_LINK=/usr/local/bin/omen88d2
ETC=/etc/omen88d2
ENABLE_SERVICE=0

for a in "$@"; do
  case "$a" in
    --enable-service) ENABLE_SERVICE=1 ;;
    -h|--help) echo "usage: sudo ./install.sh [--enable-service]"; exit 0 ;;
    *) echo "unknown arg: $a"; exit 1 ;;
  esac
done

if [ "$(id -u)" != 0 ]; then echo "run as root: sudo ./install.sh"; exit 1; fi
command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }
python3 -c "import sys; assert sys.version_info >= (3, 11)" \
  || { echo "python >= 3.11 required"; exit 1; }

BOARD=$(cat /sys/class/dmi/id/board_name 2>/dev/null || echo unknown)
echo "DMI board: $BOARD"
[ "$BOARD" = "88D2" ] || echo "WARNING: this tool targets board 88D2 (refuses other boards unless --force-board)."

# 1. ec_sys with write support, loaded now and on every boot.
echo "==> setting up ec_sys (write_support=1)"
modprobe ec_sys write_support=1 || {
  echo "modprobe ec_sys failed. Your kernel may lack CONFIG_ACPI_EC_SYSFS."; exit 1; }
[ -e /sys/kernel/debug/ec/ec0/io ] || {
  echo "/sys/kernel/debug/ec/ec0/io missing (is debugfs mounted?)"; exit 1; }
echo "ec_sys" > /etc/modules-load.d/omen88d2.conf
echo "options ec_sys write_support=1" > /etc/modprobe.d/omen88d2.conf

# 2. Install the program (stdlib-only, no pip/venv needed).
echo "==> installing to $PREFIX"
rm -rf "$PREFIX"
mkdir -p "$PREFIX"
cp -r bin omen88d2 config.example.toml README.md "$PREFIX/"
chmod +x "$PREFIX/bin/omen88d2"
ln -sf "$PREFIX/bin/omen88d2" "$BIN_LINK"

# 3. Config + systemd unit (service NOT enabled by default).
mkdir -p "$ETC"
[ -f "$ETC/config.toml" ] || cp config.example.toml "$ETC/config.toml"
cp omen88d2.service /etc/systemd/system/omen88d2.service
systemctl daemon-reload

echo "==> read-only self check"
"$BIN_LINK" probe | head -25

echo
echo "installed OK. Next steps:"
echo "  1. sudo omen88d2 probe            # full read-only report (paste it back for review)"
echo "  2. sudo omen88d2 calibrate --confirm   # verify the EC map on YOUR machine"
echo "  3. sudo omen88d2 auto             # run the curve daemon in the foreground"
echo "  4. edit $ETC/config.toml to tune curves"
if [ "$ENABLE_SERVICE" = 1 ]; then
  systemctl enable --now omen88d2.service
  echo "service enabled + started."
else
  echo "  5. sudo systemctl enable --now omen88d2.service   # run at boot (only after 1-3 look good)"
fi
