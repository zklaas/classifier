#!/usr/bin/env bash
# Register Classifier with the desktop so it appears in GNOME's dash and
# "Show Applications" grid with the Classifier icon.
#
# Installs, under ~/.local (no root needed):
#   - PNG icons into  ~/.local/share/icons/hicolor/<size>/apps/classifier.png
#   - ~/.local/share/applications/classifier.desktop
#
# Run install.sh first so the `classifier` launcher exists on your PATH.  Re-run
# this after make_icon.py to refresh the icon.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${1:-$HOME/.local/bin}"
DATA_DIR="$HOME/.local/share"
ICON_ROOT="$DATA_DIR/icons/hicolor"
APPS_DIR="$DATA_DIR/applications"

mkdir -p "$APPS_DIR" "$ICON_ROOT"

# gtk-update-icon-cache refuses to run on a theme dir with no index.theme;
# borrow the system hicolor one so the per-user overlay is a valid theme.
if [ ! -e "$ICON_ROOT/index.theme" ] && [ -f /usr/share/icons/hicolor/index.theme ]; then
  cp /usr/share/icons/hicolor/index.theme "$ICON_ROOT/index.theme"
fi

for size in 512 256 128 64 48; do
  if [ "$size" = 512 ]; then src="$APP_DIR/icons/classifier.png"; else src="$APP_DIR/icons/classifier-$size.png"; fi
  [ -f "$src" ] || continue
  dst="$ICON_ROOT/${size}x${size}/apps"
  mkdir -p "$dst"
  cp "$src" "$dst/classifier.png"
done
mkdir -p "$DATA_DIR/pixmaps"
cp "$APP_DIR/icons/classifier.png" "$DATA_DIR/pixmaps/classifier.png"

DESKTOP="$APPS_DIR/classifier.desktop"
cat > "$DESKTOP" <<EOT
[Desktop Entry]
Type=Application
Version=1.0
Name=Classifier
GenericName=Software Clearance and Licences (Linux developer tool)
Comment=For developers on Linux: sort your software into Development Groups, clear it for an open-source licence, and keep GitHub in line
Keywords=licence;license;provenance;open source;GitHub;clearance;
Exec=$BIN_DIR/classifier
Icon=classifier
Terminal=false
Categories=Development;Utility;
StartupNotify=true
StartupWMClass=classifier
EOT
chmod 644 "$DESKTOP"
echo "Installed: $DESKTOP"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS_DIR" || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$ICON_ROOT" >/dev/null 2>&1 || true
fi

echo "Done. If the icon doesn't appear right away, log out and back in."
