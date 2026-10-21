#!/bin/bash
# 第一次使用：建 venv、裝依賴
# 需要 Python 3.10 以上：macOS 內建的 3.9 只能裝到有已知漏洞的套件版本，所以改用 Homebrew 的 Python
set -e
cd "$(dirname "$0")"
command -v brew >/dev/null || { echo "需要 Homebrew：https://brew.sh"; exit 1; }
command -v exiftool >/dev/null || brew install exiftool

new_enough() { "$1" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; }
pick_python() {
  local b; b="$(brew --prefix)/bin"
  # Homebrew 不一定有 python3 這個捷徑，依序找有版本號的
  for p in "$b/python3" "$b"/python3.1[0-9] python3; do
    command -v "$p" >/dev/null && new_enough "$p" && { command -v "$p"; return 0; }
  done
  return 0
}
PY=$(pick_python)
[ -n "$PY" ] || { brew install python; PY=$(pick_python); }

# 舊版 venv（用系統 3.9 建的）就重建
[ -x .venv/bin/python ] && ! new_enough .venv/bin/python && rm -rf .venv
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
[ -f .env ] || { cp .env.example .env; chmod 600 .env; }
echo "✅ 安裝完成（$(.venv/bin/python --version)）。用後端安裝完印出的指令執行即可"
