#!/usr/bin/env bash
# 在 Proxmox 主機上建立 PhotoReturn 用的 Debian 12 LXC（以 root 執行）
#
# 從 Mac 一行執行（自動放入你的 SSH 公鑰，之後安裝檔可直接 ssh 進去）：
#   ssh root@<PVE_IP> "bash -s -- --ssh-key '$(cat ~/.ssh/id_ed25519.pub)'" < deploy/create-lxc.sh
#
# 參數（都可省略）：
#   --id N             容器編號            預設下一個可用編號
#   --hostname NAME    主機名稱            預設 photoreturn
#   --disk GB          根磁碟大小（照片也存這裡）  預設 64
#   --cores N          CPU 核心            預設 2
#   --memory MB        記憶體              預設 1024
#   --storage NAME     根磁碟放哪個 storage  預設第一個可放容器的（通常是 local-lvm）
#   --bridge NAME      網路橋接            預設 vmbr0（DHCP 取得 IP）
#   --ssh-key "KEY"    root 的 SSH 公鑰
set -euo pipefail

die() { echo "❌ $*" >&2; exit 1; }
say() { echo -e "\033[1;36m▸\033[0m $*"; }

# 整段包在 main 裡：用 ssh … bash -s 從 stdin 讀腳本時，bash 會先讀完整個函式再執行，
# 中間的指令就不會把剩下的腳本當成自己的輸入吃掉
main() {
CTID="" HOST=photoreturn DISK=64 CORES=2 MEM=1024 STORAGE="" BRIDGE=vmbr0 SSH_KEY=""
while [ $# -gt 0 ]; do
  case "$1" in
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

CTID=${CTID:-$(pvesh get /cluster/nextid </dev/null)}
pct status "$CTID" </dev/null &>/dev/null && die "容器編號 $CTID 已被使用，請用 --id 指定其他編號"
ip link show "$BRIDGE" &>/dev/null || die "找不到網路橋接 ${BRIDGE}，請用 --bridge 指定"

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
  KEYFILE=$(mktemp); trap 'rm -f "$KEYFILE"' EXIT
  printf '%s\n' "$SSH_KEY" > "$KEYFILE"
  KEY_ARGS=(--ssh-public-keys "$KEYFILE")
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

echo
echo "════════════════ ✅ LXC 建立完成 ════════════════"
echo "  容器編號：$CTID   主機名稱：$HOST   IP：$IP"
if [ -n "$SSH_KEY" ]; then
  echo
  echo "  接著在 Mac 的 PhotoReturn 資料夾安裝返圖站："
  echo "    ./deploy/build-installer.sh && scp dist/photoreturn-install.sh root@$IP:/tmp/ && ssh -t root@$IP bash /tmp/photoreturn-install.sh"
else
  echo "  沒有放 SSH 公鑰：可在 Proxmox 用 pct enter $CTID 進入，或用 pct push 安裝（見 README）"
fi
echo "═════════════════════════════════════════════════"
}

main "$@"
