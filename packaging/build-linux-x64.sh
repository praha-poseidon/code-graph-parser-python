#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-dev}"
EXTRACTOR_DIR="${STATIC_EXTRACT_PYTHON_DIR:-$ROOT_DIR/../static-extract-python}"
NAME="parser-python-${VERSION}-linux-x64"
STAGE="$ROOT_DIR/target/release/$NAME"
DIST="$ROOT_DIR/dist"

test -f "$EXTRACTOR_DIR/pyproject.toml" || { echo "static-extract-python sibling is required" >&2; exit 1; }
python -m pip install --disable-pip-version-check "$EXTRACTOR_DIR" "$ROOT_DIR" 'pyinstaller>=6,<7'
rm -rf "$STAGE" "$ROOT_DIR/target/pyinstaller"
mkdir -p "$STAGE/bin" "$DIST" "$ROOT_DIR/target/pyinstaller"
pyinstaller --noconfirm --clean --onefile --name parser-python \
  --collect-data static_extract_python \
  --distpath "$STAGE/bin" --workpath "$ROOT_DIR/target/pyinstaller/work" \
  --specpath "$ROOT_DIR/target/pyinstaller" "$ROOT_DIR/packaging/parser_python_entry.py"
install -m 0755 "$ROOT_DIR/packaging/install.sh" "$STAGE/install.sh"
printf '%s\n' "$VERSION" > "$STAGE/VERSION"
python --version > "$STAGE/RUNTIME-VERSIONS" 2>&1
tar -C "$ROOT_DIR/target/release" -czf "$DIST/$NAME.tar.gz" "$NAME"
sha256sum "$DIST/$NAME.tar.gz" > "$DIST/$NAME.tar.gz.sha256"
printf '%s\n' "$DIST/$NAME.tar.gz"
