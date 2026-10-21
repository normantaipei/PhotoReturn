#!/usr/bin/env bash
# PhotoReturn 一鍵安裝（Debian 12 / Ubuntu LXC，以 root 執行）
#
# 這是範本：在 Mac 上執行 deploy/build-installer.sh，會把後端 + 前端 build 打包進來，
# 產生單一檔案 dist/photoreturn-install.sh。再次執行即為更新，資料、token 與設定都會保留。
#
# 參數（不給就互動詢問；沒有終端機時用預設值）：
#   --site-name NAME   網站名稱（首頁標題、分頁標題、分享預覽）  預設「返圖」
#   --staging PATH     Mac 上的暫存資料夾        預設 $HOME/PhotoReturnStaging
#   --nas URL          NAS 備份位置              smb://帳號@NAS_IP/共用資料夾/子資料夾，留空不備份
#   --port N           返圖站 port               預設 8080
#   --upload-nets CIDR 允許上傳的網段（逗號分隔）  預設 LXC 所在區網
#   --public-url URL   對外網址（反向代理/網域）   預設 http://<LXC IP>:<port>
set -euo pipefail

INGEST_CMD="__INGEST_CMD__"   # build 時填入 Mac 上 ingest.sh 的路徑
APP=/opt/photoreturn
DATA=/var/lib/photoreturn
ENV_FILE=/etc/photoreturn.env

die() { echo "❌ $*" >&2; exit 1; }
say() { echo -e "\033[1;36m▸\033[0m $*"; }

has_tty() { [ -t 1 ] && ( : </dev/tty ) 2>/dev/null; }

# ask 變數名 提示 預設值：變數已有值就不問
ask() {
  local __v="${!1:-}"
  if [ -z "$__v" ] && has_tty; then
    read -r -p "$2 [${3:-留空}]: " __v </dev/tty || true
  fi
  printf -v "$1" '%s' "${__v:-$3}"
}

env_get() { [ -f "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | tail -1 | sed "s/^'\(.*\)'\$/\1/" || true; }

main() {
  local SITE_NAME="" STAGING="" NAS="" PORT="" UPLOAD_NETS="" PUBLIC_URL=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --site-name) SITE_NAME="$2"; shift 2 ;;
      --staging) STAGING="$2"; shift 2 ;;
      --nas) NAS="$2"; shift 2 ;;
      --port) PORT="$2"; shift 2 ;;
      --upload-nets) UPLOAD_NETS="$2"; shift 2 ;;
      --public-url) PUBLIC_URL="$2"; shift 2 ;;
      -h|--help) sed -n '2,15p' "$0" 2>/dev/null || true; exit 0 ;;
      *) die "不認得的參數：$1" ;;
    esac
  done
  [ "$(id -u)" = 0 ] || die "請用 root 執行"
  command -v apt-get >/dev/null || die "只支援 Debian / Ubuntu"

  # 重跑時沿用上次的設定
  PORT=${PORT:-$(env_get PHOTORETURN_PORT)}; PORT=${PORT:-8080}
  SITE_NAME=${SITE_NAME:-$(env_get PHOTORETURN_SITE_NAME)}
  STAGING=${STAGING:-$(env_get PHOTORETURN_HINT_STAGING)}
  NAS=${NAS:-$(env_get PHOTORETURN_HINT_NAS)}

  say "安裝系統套件"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq </dev/null
  apt-get install -y -qq python3 python3-venv iproute2 curl ca-certificates </dev/null >/dev/null
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
    || die "需要 Python 3.10 以上（目前 $(python3 --version)），請用 Debian 12 / Ubuntu 22.04 以上"

  local IP CIDR NET
  IP=$(ip -o -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')
  [ -n "$IP" ] || IP=$(hostname -I | awk '{print $1}')
  CIDR=$(ip -o -4 addr show scope global | awk -v ip="$IP" '{split($4,a,"/"); if (a[1]==ip) print $4}' | head -1)
  NET=$(python3 -c "import ipaddress,sys; print(ipaddress.ip_interface(sys.argv[1]).network)" "${CIDR:-$IP/24}")
  UPLOAD_NETS=${UPLOAD_NETS:-$(env_get PHOTORETURN_UPLOAD_NETS)}
  UPLOAD_NETS=${UPLOAD_NETS:-$NET,127.0.0.1/32}
  PUBLIC_URL=${PUBLIC_URL:-$(env_get PHOTORETURN_PUBLIC_URL)}
  PUBLIC_URL=${PUBLIC_URL:-http://$IP:$PORT}

  echo
  ask SITE_NAME "網站名稱（首頁標題、分頁標題、貼到通訊軟體時顯示）" "返圖"
  SITE_NAME=$(printf '%s' "$SITE_NAME" | tr -d "'\n\r")   # 設定檔用單引號包，不能含單引號
  ask STAGING "Mac 上的暫存資料夾" '$HOME/PhotoReturnStaging'
  ask NAS "NAS 備份位置（smb://帳號@NAS_IP/共用資料夾/子資料夾）" ""
  if [ -n "$NAS" ] && ! [[ "$NAS" =~ ^smb://[^/]+/[^/]+ ]]; then
    die "NAS 位址格式應為 smb://帳號@NAS_IP/共用資料夾/子資料夾"
  fi

  say "解開程式（後端 + 前端）"
  local TMP; TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
  printf '%s' "$PAYLOAD_B64" | base64 -d | tar -xz -C "$TMP"
  id photoreturn &>/dev/null || useradd --system --home "$DATA" --shell /usr/sbin/nologin photoreturn
  mkdir -p "$APP" "$DATA"
  rm -rf "$APP/server/app" "$APP/web"
  mkdir -p "$APP/server" "$APP/web"
  cp -a "$TMP/server/." "$APP/server/"
  cp -a "$TMP/web/." "$APP/web/"
  cp "$TMP/photoreturn.service" /etc/systemd/system/photoreturn.service

  say "建立 Python 環境"
  [ -x "$APP/server/.venv/bin/python" ] || python3 -m venv "$APP/server/.venv"
  "$APP/server/.venv/bin/pip" install -q --upgrade pip </dev/null
  "$APP/server/.venv/bin/pip" install -q -r "$APP/server/requirements.txt" </dev/null
  chown -R photoreturn:photoreturn "$DATA"

  say "寫入設定 $ENV_FILE"
  local TOKEN; TOKEN=$(env_get PHOTORETURN_UPLOAD_TOKEN)
  [ -n "$TOKEN" ] || TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  umask 077
  cat > "$ENV_FILE" <<ENV
# PhotoReturn 設定（重跑安裝會保留 token）
PHOTORETURN_DATA=$DATA
PHOTORETURN_PORT=$PORT
PHOTORETURN_UPLOAD_TOKEN=$TOKEN
# 只有這些網段直連才能上傳（逗號分隔 CIDR），經反向代理轉進來的上傳一律拒絕
PHOTORETURN_UPLOAD_NETS=$UPLOAD_NETS
# 網站名稱（改完 systemctl restart photoreturn）
PHOTORETURN_SITE_NAME='${SITE_NAME}'
# 返圖連結的網址前綴（有網域 / 反向代理時改這裡）
PHOTORETURN_PUBLIC_URL=$PUBLIC_URL
# 以下只用來印出 Mac 端指令
PHOTORETURN_HINT_STAGING='$STAGING'
PHOTORETURN_HINT_NAS='$NAS'
PHOTORETURN_HINT_INGEST='$INGEST_CMD'
PHOTORETURN_HINT_SERVER=http://$IP:$PORT
ENV
  umask 022

  # 之後忘記指令可在 LXC 執行 photoreturn-cmd 再印一次
  cat > /usr/local/bin/photoreturn-cmd <<'CMD'
#!/bin/bash
set -a; source /etc/photoreturn.env; set +a
q() { printf '"%s"' "$1"; }
echo "在 Mac mini 上執行（插卡後；加了 --save，之後只要打 ingest.sh 本身）："
echo
printf '  %s \\\n    --server %s \\\n    --token %s \\\n    --staging %s' \
  "$PHOTORETURN_HINT_INGEST" "$PHOTORETURN_HINT_SERVER" "$PHOTORETURN_UPLOAD_TOKEN" "$(q "$PHOTORETURN_HINT_STAGING")"
[ -n "$PHOTORETURN_HINT_NAS" ] && printf ' \\\n    --nas %s' "$(q "$PHOTORETURN_HINT_NAS")"
printf ' \\\n    --save\n'
CMD
  chmod 700 /usr/local/bin/photoreturn-cmd

  say "啟動服務"
  systemctl daemon-reload
  systemctl enable photoreturn >/dev/null 2>&1
  systemctl restart photoreturn
  local ok=""
  for _ in $(seq 1 20); do
    curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && { ok=1; break; }
    sleep 0.5
  done
  [ -n "$ok" ] || die "服務沒有起來，請看 journalctl -u photoreturn -n 50"

  echo
  echo "════════════════════ ✅ PhotoReturn 安裝完成 ════════════════════"
  echo "  返圖站：${SITE_NAME}　$PUBLIC_URL"
  echo "  上傳限制：token + 只接受 $UPLOAD_NETS 直連"
  echo "  資料：$DATA   設定：$ENV_FILE"
  echo
  /usr/local/bin/photoreturn-cmd
  echo
  [ -n "$NAS" ] && echo "  NAS 密碼放 Mac 的 ~/Library/Preferences/nsmb.conf（同 Prism）就不用每次輸入。"
  echo "  忘記指令：在這台 LXC 執行 photoreturn-cmd"
  echo "═════════════════════════════════════════════════════════════════"
}

# ---- 以下由 build-installer.sh 附加：PAYLOAD_B64=... 與 main "$@" ----
