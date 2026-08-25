#!/usr/bin/env bash
set -euo pipefail
PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(cat "$PACKAGE_DIR/VERSION")"
INSTALL_ROOT="${CODEGRAPH_INSTALL_ROOT:-$HOME/.local/share/codegraph}"
BIN_DIR="${CODEGRAPH_BIN_DIR:-$HOME/.local/bin}"
TARGET="$INSTALL_ROOT/parser-python/$VERSION"
mkdir -p "$TARGET" "$BIN_DIR"
cp -R "$PACKAGE_DIR/." "$TARGET/"
ln -sfn "$TARGET/bin/parser-python" "$BIN_DIR/parser-python"
printf 'Installed parser-python to %s\nCommand: %s/parser-python\n' "$TARGET" "$BIN_DIR"
