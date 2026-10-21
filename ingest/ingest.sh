#!/bin/bash
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || ./setup.sh   # 第一次執行自動安裝
exec .venv/bin/python ingest.py "$@"
