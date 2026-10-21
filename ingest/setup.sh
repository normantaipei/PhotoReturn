#!/bin/bash
# 第一次使用：建 venv、裝依賴
set -e
cd "$(dirname "$0")"
command -v exiftool >/dev/null || brew install exiftool
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
echo "✅ 安裝完成。編輯 ingest/.env 填入 SERVER_URL / UPLOAD_TOKEN / NAS 設定"
