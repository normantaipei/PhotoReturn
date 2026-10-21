#!/bin/bash
# 在 Mac 上執行：build 前端，把後端 + 前端打包成單一安裝檔 dist/photoreturn-install.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/dist/photoreturn-install.sh"

echo "▸ build 前端"
cd "$ROOT/web"
[ -d node_modules ] || npm install --silent
npm run build --silent >/dev/null

echo "▸ 打包"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/server" "$TMP/web"
rsync -a --exclude .venv --exclude data --exclude __pycache__ "$ROOT/server/" "$TMP/server/"
cp -R "$ROOT/web/dist" "$TMP/web/dist"
cp "$ROOT/deploy/photoreturn.service" "$TMP/"
# macOS（bsdtar）：不要把 ._ 檔與 xattr 包進去（Debian 的 tar 會抱怨）；Linux（GNU tar，GitHub Actions）不用
if tar --version 2>/dev/null | grep bsdtar >/dev/null; then TAR_OPTS=(--no-mac-metadata --no-xattrs)
else TAR_OPTS=(--owner=0 --group=0); fi
PAYLOAD=$(COPYFILE_DISABLE=1 tar -C "$TMP" "${TAR_OPTS[@]}" -cz . | base64 | tr -d '\n' | fold -w 76)

# Mac 上 ingest.sh 的路徑（家目錄寫成 ~，印出的指令比較短）
# GitHub Actions 打包時傳 INGEST_CMD=ingest/ingest.sh（不知道使用者 clone 在哪）
INGEST_CMD="${INGEST_CMD:-$ROOT/ingest/ingest.sh}"
INGEST_CMD="${INGEST_CMD/#$HOME/~}"

mkdir -p "$ROOT/dist"
sed "s|__INGEST_CMD__|$INGEST_CMD|" "$ROOT/deploy/install.sh" > "$OUT"
{
  echo "PAYLOAD_B64=\$(cat <<'__PHOTORETURN_PAYLOAD__'"
  echo "$PAYLOAD"
  echo "__PHOTORETURN_PAYLOAD__"
  echo ")"
  echo 'main "$@"'
} >> "$OUT"
chmod +x "$OUT"

echo "✅ ${OUT}（$(du -h "$OUT" | cut -f1 | tr -d ' ')）"
echo
echo "安裝 / 更新到 LXC（把 <LXC_IP> 換成 LXC 的 IP，一行）："
echo "  scp \"$OUT\" root@<LXC_IP>:/tmp/ && ssh -t root@<LXC_IP> bash /tmp/photoreturn-install.sh"
echo
echo "LXC 還沒開 root SSH 的話，改經 Proxmox 主機（<PVE_IP> 主機 IP、<CTID> 容器編號）："
echo "  scp \"${OUT}\" root@<PVE_IP>:/tmp/ && ssh -t root@<PVE_IP> 'pct push <CTID> /tmp/photoreturn-install.sh /tmp/photoreturn-install.sh && pct exec <CTID> -- bash /tmp/photoreturn-install.sh'"
