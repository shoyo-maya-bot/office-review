#!/usr/bin/env bash
# セットアップ（macOS / Linux / Git Bash）: .venv を作って harness と fieldcheck、doc2md（harness convert 用）を入れる
#   ./setup.sh               # すべて（[dev] と [convert]）
#   ./setup.sh --no-convert  # Office の変換（harness convert）を使わないとき
set -euo pipefail
cd "$(dirname "$0")"
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
  echo "Python 3.12 以上が必要です（python3 は $(python3 --version 2>&1 || echo '見つかりません')）" >&2
  exit 1
fi
extras="dev,convert"
[ "${1:-}" = "--no-convert" ] && extras="dev"
python3 -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip || true
c=""; [ -f constraints.txt ] && c="-c constraints.txt"
.venv/bin/python -m pip install -q $c -e ".[$extras]"
echo "完了: source .venv/bin/activate のあと harness --help"
