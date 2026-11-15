#!/usr/bin/env bash
# Install an `classifier` launcher on your PATH (~/.local/bin by default).
set -euo pipefail

if [ "$(uname -s)" != "Linux" ]; then
  echo "Classifier is a Linux tool for developers and runs on Linux only." >&2
  exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${1:-$HOME/.local/bin}"
LAUNCHER="$BIN_DIR/classifier"

mkdir -p "$BIN_DIR"
cat > "$LAUNCHER" <<EOT
#!/usr/bin/env bash
exec python3 "$APP_DIR/classifier_app.py" "\$@"
EOT
chmod +x "$LAUNCHER"

echo "Installed: $LAUNCHER"

# Register a dash / "Show Applications" entry with the Classifier icon (best effort).
if [ -x "$APP_DIR/install-desktop.sh" ]; then
  "$APP_DIR/install-desktop.sh" "$BIN_DIR" || true
fi

python3 -c "import PyQt6.QtWidgets" 2>/dev/null || \
  echo "NOTE: Classifier needs PyQt6:  sudo apt install python3-pyqt6"

if ! printf '%s' ":$PATH:" | grep -q ":$BIN_DIR:"; then
  echo "NOTE: $BIN_DIR is not on your PATH."
  echo "  Add this line to ~/.bashrc then restart your shell:"
  echo "    export PATH=\"$BIN_DIR:\$PATH\""
else
  echo "Run it with:  classifier"
fi

# claude-classifier-docker: ZIS's claude-docker under its own name, for "Review with Claude".
if [ -f "$HOME/Claude/ZIS/claude-docker/claude-docker" ]; then
  python3 "$APP_DIR/claude-classifier-docker/make.py" || echo "NOTE: claude-classifier-docker was not installed."
else
  echo "NOTE: ZIS's claude-docker not found; 'Review with Claude' needs claude-classifier-docker:"
  echo "  python3 $APP_DIR/claude-classifier-docker/make.py --zis /path/to/ZIS/claude-docker"
fi
