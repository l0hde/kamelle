#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/kamelle"

printf '🍬 Creating local Kamelle venv at %s\n' "$VENV"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip setuptools wheel >/dev/null
"$VENV/bin/python" -m pip install -e "$ROOT" >/dev/null

mkdir -p "$BIN_DIR"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
exec "$VENV/bin/python" -m kamelle "\$@"
EOF
chmod +x "$LAUNCHER"

printf '✨ Kamelle installed locally.\n'
printf 'Run it via: %s\n' "$LAUNCHER"
printf 'If ~/.local/bin is on your PATH, you can just run: kamelle\n'
