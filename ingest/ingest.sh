#!/bin/bash
cd "$(dirname "$0")"
# 第一次執行、或 venv 還是舊的 Python 3.9 時，自動安裝 / 重建
.venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null || ./setup.sh
exec .venv/bin/python ingest.py "$@"
