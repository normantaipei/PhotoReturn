#!/usr/bin/env bash
# PhotoReturn 返圖站 — 一鍵架設（給全新的 Debian 12 / Ubuntu 22.04+ LXC 或 VM，以 root 執行）
#
#   curl -fsSL https://raw.githubusercontent.com/normantaipei/PhotoReturn/main/bootstrap.sh | bash
#
# 在 Proxmox 上連 LXC 一起建：用 deploy/create-lxc.sh --install（見 README 安裝法 B），它會在新容器裡跑這支。
#
# 這支腳本會（缺什麼裝什麼，可重複執行 = 更新）：
#   1. 裝 git / curl / xz
#   2. 把 repo clone 到 /opt/photoreturn/src（已存在就 git pull）
#   3. 沒有 Node.js 20.19+ 就下載 nodejs.org 官方版（驗證 SHA-256），只用來 build 前端
#   4. build 前端（npm ci --ignore-scripts，不執行套件的安裝腳本）
#   5. 執行 deploy/install.sh：建 Python 環境、產生 token、systemd 服務，最後印出 Mac 端指令
#
# 參數會原封不動交給 install.sh，例：
#   curl -fsSL …/bootstrap.sh | bash -s -- --site-name "小明的返圖" --nas "smb://user@192.168.1.10/photo/返圖"
#
# 可用環境變數覆寫：
#   PHOTORETURN_REPO   repo 網址（預設官方 GitHub；fork 的人改這個）
#   PHOTORETURN_REF    分支或 tag（預設 main）
#   PHOTORETURN_DIR    原始碼位置（預設 /opt/photoreturn/src）
set -euo pipefail

REPO="${PHOTORETURN_REPO:-https://github.com/normantaipei/PhotoReturn.git}"
REF="${PHOTORETURN_REF:-main}"
DIR="${PHOTORETURN_DIR:-/opt/photoreturn/src}"
NODE_DIR=/opt/photoreturn/node
NODE_LINE=22   # 下載的 Node.js LTS 主版本

die() { printf '\n\033[1;31m✗ %b\033[0m\n' "$*" >&2; exit 1; }
say() { echo -e "\033[1;36m▸\033[0m $*"; }

# 包在 main 裡：curl … | bash 時 bash 會先讀完整支腳本，中間的指令不會把剩下的腳本當輸入吃掉
main() {
  [ "$(id -u)" = 0 ] || die "請用 root 執行"
  command -v apt-get >/dev/null || die "只支援 Debian / Ubuntu"

  # ── 1. 基本工具 ─────────────────────────────────────────────
  say "安裝 git / curl / xz"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq </dev/null
  apt-get install -y -qq git curl ca-certificates xz-utils </dev/null >/dev/null

  # ── 2. 取得程式碼 ───────────────────────────────────────────
  if [ -d "$DIR/.git" ]; then
    say "更新程式碼（${DIR}）"
    git -C "$DIR" fetch -q --depth 1 origin "$REF" </dev/null
    git -C "$DIR" checkout -q -f FETCH_HEAD </dev/null
  else
    say "下載程式碼 ${REPO}（${REF}）→ $DIR"
    mkdir -p "$(dirname "$DIR")"
    git clone -q --depth 1 --branch "$REF" "$REPO" "$DIR" </dev/null
  fi
  say "版本：$(git -C "$DIR" log -1 --format='%h %s' </dev/null)"

  # ── 3. Node.js（只用來 build 前端）──────────────────────────
  node_ok() {  # Vite 需要 Node 20.19+
    command -v node >/dev/null && node -e '
      const [a, b] = process.versions.node.split(".").map(Number);
      process.exit(a > 20 || (a === 20 && b >= 19) ? 0 : 1)' 2>/dev/null
  }
  export PATH="$NODE_DIR/bin:$PATH"
  if ! node_ok; then
    local arch base file sum
    case "$(uname -m)" in
      x86_64|amd64) arch=x64 ;;
      aarch64|arm64) arch=arm64 ;;
      *) die "不支援的 CPU 架構：$(uname -m)" ;;
    esac
    base="https://nodejs.org/dist/latest-v${NODE_LINE}.x"
    curl -fsSL "$base/SHASUMS256.txt" -o /tmp/node-sums.txt </dev/null || die "無法下載 Node.js 版本清單"
    file=$(awk -v want="linux-${arch}.tar.xz" '$2 ~ want"$" && !f {print $2; f=1}' /tmp/node-sums.txt)
    sum=$(awk -v n="$file" '$2 == n {print $1}' /tmp/node-sums.txt)
    [ -n "$file" ] && [ -n "$sum" ] || die "找不到對應的 Node.js 安裝檔"
    say "下載 Node.js（${file}）"
    curl -fsSL "$base/$file" -o "/tmp/$file" </dev/null || die "Node.js 下載失敗"
    [ "$(sha256sum "/tmp/$file" | cut -d' ' -f1)" = "$sum" ] || die "Node.js 安裝檔 SHA-256 不符，已中止"
    rm -rf "$NODE_DIR" && mkdir -p "$NODE_DIR"
    tar -xJf "/tmp/$file" -C "$NODE_DIR" --strip-components=1
    rm -f "/tmp/$file" /tmp/node-sums.txt
    node_ok || die "Node.js 安裝後仍無法使用"
  fi

  # ── 4. build 前端 ───────────────────────────────────────────
  say "build 前端（Node $(node --version)）"
  # --ignore-scripts：以 root 跑，不讓任何套件執行安裝腳本
  (cd "$DIR/web" && npm ci --ignore-scripts --no-audit --no-fund --loglevel=error </dev/null >/dev/null \
                 && npm run build --silent </dev/null >/dev/null) || die "前端 build 失敗"

  # ── 5. 安裝 / 更新返圖站 ────────────────────────────────────
  PHOTORETURN_SRC="$DIR" bash "$DIR/deploy/install.sh" "$@"
}

main "$@"
