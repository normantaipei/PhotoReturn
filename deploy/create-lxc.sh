#!/usr/bin/env bash
# 在 Proxmox 主機上建立 PhotoReturn 用的 Debian 12 LXC（以 root 執行）
#
# 安裝法 A：從 Mac 經 SSH（只建 LXC 並放入公鑰，之後在 Mac 上打包、安裝）
#   ssh root@<PVE_IP> "bash -s -- --ssh-key '$(cat ~/.ssh/id_ed25519.pub)'" < deploy/create-lxc.sh
#
# 安裝法 B：直接在 Proxmox 的 Shell（網頁介面 → 節點 → Shell），建 LXC + 裝好返圖站，不需要 SSH
#   curl -fsSL https://raw.githubusercontent.com/normantaipei/PhotoReturn/main/deploy/create-lxc.sh | bash -s -- --install
#
# 參數（都可省略）：
#   --install          建好後直接在容器裡跑 bootstrap.sh：從 GitHub 抓程式、build、安裝返圖站
#   --site-name NAME   網站名稱：整個返圖站的名字（--install 用，沒給會詢問）
#                      暫存資料夾、NAS 備份、相簿名稱都是之後在 Mac 上傳時設定，這裡只架伺服器
#   --public-url URL   對外網址（反向代理 / https 網域），例 https://photos.example.com（--install 用）
#   --ref BRANCH|TAG   要裝哪個分支或 tag              預設 main
#   --id N             容器編號            預設下一個可用編號
#   --hostname NAME    主機名稱            預設 photoreturn
#   --disk GB          根磁碟大小（照片也存這裡）  預設 64（--install 時會詢問）
#   --cores N          CPU 核心            預設 2
#   --memory MB        記憶體              預設 1024
#   --storage NAME     根磁碟放哪個 storage  預設第一個可放容器的（通常是 local-lvm）
#   --bridge NAME      網路橋接            預設 vmbr0（DHCP 取得 IP）
#   --ssh-key "KEY"    root 的 SSH 公鑰
set -euo pipefail

# fork 的人改這裡（或設環境變數）就會從自己的 repo 抓
REPO="${PHOTORETURN_REPO:-normantaipei/PhotoReturn}"

die() { echo "❌ $*" >&2; exit 1; }
say() { echo -e "\033[1;36m▸\033[0m $*"; }

has_tty() { ( : </dev/tty ) 2>/dev/null; }

# ask 變數名 提示 預設值：變數已有值、或沒有終端機（例如經 ssh bash -s）就不問
ask() {
  local __v="${!1:-}"
  if [ -z "$__v" ] && has_tty; then
    read -r -p "  $2 [${3:-留空}]: " __v </dev/tty || true
  fi
  printf -v "$1" '%s' "${__v:-$3}"
}

# 整段包在 main 裡：用 ssh … bash -s 從 stdin 讀腳本時，bash 會先讀完整個函式再執行，
# 中間的指令就不會把剩下的腳本當成自己的輸入吃掉
main() {
CTID="" HOST=photoreturn DISK="" CORES=2 MEM=1024 STORAGE="" BRIDGE=vmbr0 SSH_KEY=""
INSTALL="" SITE_NAME="" PUBLIC_URL="" REF="main"
while [ $# -gt 0 ]; do
  case "$1" in
    --install) INSTALL=1; shift ;;
    --site-name) SITE_NAME="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --public-url) PUBLIC_URL="$2"; shift 2 ;;
    --id) CTID="$2"; shift 2 ;;
    --hostname) HOST="$2"; shift 2 ;;
    --disk) DISK="$2"; shift 2 ;;
    --cores) CORES="$2"; shift 2 ;;
    --memory) MEM="$2"; shift 2 ;;
    --storage) STORAGE="$2"; shift 2 ;;
    --bridge) BRIDGE="$2"; shift 2 ;;
    --ssh-key) SSH_KEY="$2"; shift 2 ;;
    *) die "不認得的參數：$1" ;;
  esac
done

command -v pct >/dev/null || die "請在 Proxmox 主機上執行（找不到 pct）"
[ "$(id -u)" = 0 ] || die "請用 root 執行"

# --install：先把問題問完，接下來就不用守在旁邊
if [ -n "$INSTALL" ]; then
  echo
  echo "PhotoReturn 安裝設定（直接 Enter 用預設值）"
  ask SITE_NAME "網站名稱：整個返圖站的名字，例「小明的返圖」（每本相簿的名稱之後在 Mac 上傳時才設定）" "返圖"
  ask DISK "LXC 磁碟大小 GB（所有相簿的 JPG + RAW 都存這裡，一場活動可能就數十 GB）" "64"
  echo
fi
DISK=${DISK:-64}
[[ "$DISK" =~ ^[0-9]+$ ]] || die "磁碟大小要是數字（GB）：$DISK"

CTID=${CTID:-$(pvesh get /cluster/nextid </dev/null)}
pct status "$CTID" </dev/null &>/dev/null && die "容器編號 $CTID 已被使用，請用 --id 指定其他編號"
ip link show "$BRIDGE" &>/dev/null || die "找不到網路橋接 ${BRIDGE}，請用 --bridge 指定"

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

# 找存放模板的 storage 與放容器根磁碟的 storage（第一個啟用中的）
TPL_STORE=$(pvesm status -content vztmpl </dev/null | awk 'NR>1 && $3=="active" && !f {print $1; f=1}')
[ -n "$TPL_STORE" ] || die "沒有可放 LXC 模板（vztmpl）的 storage"
STORAGE=${STORAGE:-$(pvesm status -content rootdir </dev/null | awk 'NR>1 && $3=="active" && !f {print $1; f=1}')}
[ -n "$STORAGE" ] || die "沒有可放容器（rootdir）的 storage，請用 --storage 指定"

say "更新模板清單"
pveam update </dev/null >/dev/null
TPL=$(pveam available --section system </dev/null | awk '{print $2}' | grep '^debian-12-standard_.*_amd64\.tar\.zst$' | sort -V | tail -1)
[ -n "$TPL" ] || die "找不到 Debian 12 模板"
if ! pveam list "$TPL_STORE" </dev/null | grep -F "$TPL" >/dev/null; then
  say "下載模板 $TPL"
  pveam download "$TPL_STORE" "$TPL" </dev/null >/dev/null
fi

KEY_ARGS=()
if [ -n "$SSH_KEY" ]; then
  printf '%s\n' "$SSH_KEY" > "$TMP/key.pub"
  KEY_ARGS=(--ssh-public-keys "$TMP/key.pub")
fi

say "建立容器 ${CTID}（${HOST}，${CORES} 核 / ${MEM}MB / ${DISK}GB @ ${STORAGE}）"
pct create "$CTID" "$TPL_STORE:vztmpl/$TPL" \
  --hostname "$HOST" \
  --cores "$CORES" --memory "$MEM" --swap 512 \
  --rootfs "$STORAGE:$DISK" \
  --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" \
  --unprivileged 1 --features nesting=1 \
  --onboot 1 --start 1 \
  ${KEY_ARGS[@]+"${KEY_ARGS[@]}"} </dev/null >/dev/null

say "等待取得 IP"
IP=""
for _ in $(seq 1 60); do
  IP=$(pct exec "$CTID" -- ip -4 -o addr show eth0 </dev/null 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1 || true)
  [ -n "$IP" ] && break
  sleep 1
done
[ -n "$IP" ] || die "容器已建立但 60 秒內沒拿到 IP，請檢查 DHCP（pct enter ${CTID}）"

if [ -n "$INSTALL" ]; then
  # DHCP 拿到 IP 後 DNS 可能還沒好，等到能解析再裝
  for _ in $(seq 1 30); do
    pct exec "$CTID" -- getent hosts deb.debian.org </dev/null >/dev/null 2>&1 && break
    sleep 1
  done
  say "在容器 ${CTID} 裡安裝返圖站（從 GitHub 抓 ${REPO}@${REF}）"
  pct exec "$CTID" -- bash -c 'apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null' </dev/null \
    || die "容器裡裝 curl 失敗，請檢查容器網路（pct enter ${CTID}）"
  # 參數用 "$@" 傳進去，網站名稱有空白或中文也不會被拆開
  pct exec "$CTID" -- env PHOTORETURN_REPO="https://github.com/$REPO.git" PHOTORETURN_REF="$REF" \
    bash -c 'curl -fsSL "https://raw.githubusercontent.com/'"$REPO"'/'"$REF"'/bootstrap.sh" | bash -s -- "$@"' _ \
    --no-prompt --site-name "$SITE_NAME" ${PUBLIC_URL:+--public-url "$PUBLIC_URL"} </dev/null \
    || die "安裝失敗，可進容器看看：pct enter ${CTID}"
  echo
  echo "  容器編號：$CTID   主機名稱：$HOST   IP：$IP"
  echo "  之後更新：pct exec $CTID -- bash -c 'curl -fsSL https://raw.githubusercontent.com/$REPO/main/bootstrap.sh | bash -s -- --no-prompt'"
  return
fi

echo
echo "════════════════ ✅ LXC 建立完成 ════════════════"
echo "  容器編號：$CTID   主機名稱：$HOST   IP：$IP"
if [ -n "$SSH_KEY" ]; then
  echo
  echo "  接著在 Mac 的 PhotoReturn 資料夾安裝返圖站："
  echo "    ./deploy/build-installer.sh && scp dist/photoreturn-install.sh root@$IP:/tmp/ && ssh -t root@$IP bash /tmp/photoreturn-install.sh"
else
  echo "  沒有放 SSH 公鑰：可在 Proxmox 用 pct enter $CTID 進入，或加 --install 直接安裝（見 README）"
fi
echo "═════════════════════════════════════════════════"
}

main "$@"
