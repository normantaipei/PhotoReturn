# PhotoReturn 返圖站

給攝影師用的自架返圖系統：回家插卡 → 一支腳本跑完：複製暫存 →（拔卡）→ [Prism](https://github.com/normantaipei/Prism) 標籤評分 → 上傳返圖站 + NAS 雙備份。
返圖站架在自己的 Proxmox LXC，照片依 AI 美學星等排序，對方用分享連結觀看、下載 JPG / RAW，也能單張分享。

照片不經過任何雲端服務：AI 標籤與評分在 Mac 本機用 macOS Vision 完成，返圖站與備份都在自己的機器上。

```
Mac mini（ingest/）                                   Proxmox LXC（server/ + web/dist）
┌──────────────────────────────────────────┐        ┌──────────────────────────┐
│ 1 記憶卡 → 暫存區（SHA-256、退卡、通知） │        │ FastAPI + SQLite + Vue 3 │
│ 2 Prism / Vision：標籤 + 美學分數 → 星等 │──PUT──▶│ 原始檔（內容定址）        │
│   JPG 寫 IPTC/XMP，RAW 寫 .xmp sidecar   │        │ 縮圖 webp / 預覽 jpg     │
│ 3 上傳 JPG + RAW（RAW-only 另轉 JPG）    │        │ /a/<分享碼> 依星等排序    │
│ 4 rsync 整本相簿到 NAS + 再比對一次      │        └──────────────────────────┘
└──────────────────┬───────────────────────┘
                   └──rsync──▶ NAS（JPG + RAW + sidecar，含標籤）
```

## 需求

| 位置 | 需求 |
|---|---|
| Mac（處理照片） | macOS 15 以上（美學評分 API）、Apple Silicon 建議、[Homebrew](https://brew.sh)、Node.js 20.19+（只用來 build 前端）。Python 3.10+ 由 `setup.sh` 自動用 Homebrew 安裝（macOS 內建的 3.9 只能裝到有已知漏洞的套件版本） |
| 伺服器 | Debian 12 / Ubuntu 22.04 以上（Python 3.10+）的 LXC、VM 或實體機，root 權限 |
| NAS（選用） | SMB 共用資料夾 |

```bash
# Prism 與 PhotoReturn 放在同一層資料夾（ingest 預設到旁邊找 Prism，也可用 .env 的 PRISM_DIR 指定）
git clone https://github.com/normantaipei/Prism.git
git clone https://github.com/normantaipei/PhotoReturn.git
cd PhotoReturn
```

以下指令都在 `PhotoReturn/` 資料夾內執行。

## 安裝返圖站（後端 + 前端）

兩種方式擇一，裝完都會印出 Mac 端要執行的完整指令（含 token、暫存資料夾、後端位址、NAS 位址）。

| | 安裝法 A：從 Mac 經 SSH | 安裝法 B：在 Proxmox Shell 直接裝 |
|---|---|---|
| 適合 | Mac 能 ssh 到 Proxmox / LXC | Proxmox 沒開 SSH，或想最省事 |
| 在哪裡下指令 | Mac 終端機 | Proxmox 網頁介面 → 節點 → **Shell** |
| 程式從哪來 | Mac 上這份原始碼打包（改過程式也能裝） | 容器直接從 GitHub 抓最新版、在容器裡 build |
| Mac 需要 Node.js | 要（build 前端） | 不用 |

### 安裝法 B：在 Proxmox Shell 一行安裝

在 Proxmox 網頁介面點節點 → **Shell**，貼上：

```bash
curl -fsSL https://raw.githubusercontent.com/normantaipei/PhotoReturn/main/deploy/create-lxc.sh | bash -s -- --install
```

會先詢問 **網站名稱**、**LXC 磁碟大小**、**Mac 暫存資料夾**、**NAS 位址**，接著自動：
建立 Debian 12 LXC → 在容器裡執行 [`bootstrap.sh`](bootstrap.sh)（clone repo、下載官方 Node.js 並驗證 SHA-256、build 前端、安裝服務）→ 印出 Mac 端指令。

- 想先看過腳本再執行：`curl -fsSL https://raw.githubusercontent.com/normantaipei/PhotoReturn/main/deploy/create-lxc.sh -o create-lxc.sh && less create-lxc.sh && bash create-lxc.sh --install`
- 指定分支或 tag：加 `--ref v0.2.0`；LXC 規格參數同下方安裝法 A。
- **不是 Proxmox**（一般 Debian 12 / Ubuntu VM 或實體機）：在那台機器上以 root 跑
  `curl -fsSL https://raw.githubusercontent.com/normantaipei/PhotoReturn/main/bootstrap.sh | bash`
- Mac 端記得 `git clone` 這個 repo（ingest 在裡面），印出的指令要在 `PhotoReturn/` 資料夾裡執行。

### 安裝法 A：從 Mac 經 SSH

1. 建一台 Debian 12 LXC（已經有就跳過）。`<PVE_IP>` 換成 Proxmox 主機 IP；會自動放入你的 SSH 公鑰，完成後印出 LXC 的 IP：

```bash
ssh root@<PVE_IP> "bash -s -- --ssh-key '$(cat ~/.ssh/id_ed25519.pub)'" < deploy/create-lxc.sh
```

   預設：下一個可用編號、unprivileged、2 核 / 1GB RAM / 64GB 磁碟（照片也放這裡）、`vmbr0` DHCP、開機自動啟動。
   可加參數調整，例如照片多就 `--disk 500`；其他：`--id`、`--hostname`、`--cores`、`--memory`、`--storage`、`--bridge`。

2. 在 Mac 上打包並安裝（`<LXC_IP>` 換成上一步印出的 IP）：

```bash
./deploy/build-installer.sh && scp dist/photoreturn-install.sh root@<LXC_IP>:/tmp/ && ssh -t root@<LXC_IP> bash /tmp/photoreturn-install.sh
```

### 安裝時的設定

安裝時會問三件事（安裝法 B 在 Proxmox Shell 一開始就問完，再交給 `bootstrap.sh`），也可以直接帶參數不問：

| 問題 | 參數 | 說明 |
|---|---|---|
| 網站名稱 | `--site-name` | 首頁標題、瀏覽器分頁標題、貼到通訊軟體時的網站名，預設「返圖」 |
| Mac 暫存資料夾 | `--staging` | 預設 `$HOME/PhotoReturnStaging` |
| NAS 位址 | `--nas` | `smb://帳號@NAS_IP/共用資料夾/子資料夾`，留空不備份 |

```bash
# 安裝法 A 的安裝檔；bootstrap.sh 也接受同樣的參數
bash /tmp/photoreturn-install.sh --site-name "小明的返圖" --nas "smb://user@192.168.1.10/photo/返圖備份" --staging '$HOME/PhotoReturnStaging'
```

（其他參數：`--port`、`--upload-nets`、`--public-url`、`--no-prompt`，見安裝檔開頭說明）
忘記 Mac 端指令時，在 LXC 執行 `photoreturn-cmd` 再印一次。
重跑安裝會沿用上次的答案、保留 token 與照片；之後要改網站名稱，重跑時帶 `--site-name`，或改 `/etc/photoreturn.env` 的 `PHOTORETURN_SITE_NAME` 再 `systemctl restart photoreturn`。
每本相簿的名稱與標題則是在 Mac 端用 `--album`、`--title` 設定。

### 更新到新版

重跑即為更新，token、照片、設定都會保留：

- 安裝法 A：`git pull` 後重跑第 2 步那一行。
- 安裝法 B：在 Proxmox Shell（`<CTID>` 換成容器編號）：

```bash
pct exec <CTID> -- bash -c 'curl -fsSL https://raw.githubusercontent.com/normantaipei/PhotoReturn/main/bootstrap.sh | bash -s -- --no-prompt'
```

### 上傳保護

上傳 API（`/api/albums/*`）兩道關卡，觀看端不受影響：

1. **只限區網直連**：來源 IP 必須在 `PHOTORETURN_UPLOAD_NETS`（安裝時自動設成 LXC 所在網段 + 本機）。
   帶有 `X-Forwarded-For` / `X-Real-IP` / `Forwarded` / `CF-Connecting-IP` 的請求一律拒絕，所以就算之後把網站透過反向代理或 Cloudflare Tunnel 開到外網，上傳也進不來。這一關在讀取上傳內容之前就擋掉。
2. **Bearer token**：安裝時產生、存在 `/etc/photoreturn.env`。

VPN 連回家若被分到別的網段，要上傳時把該網段加進 `PHOTORETURN_UPLOAD_NETS`，再 `systemctl restart photoreturn`。

### 開放到外網前請確認

- **反向代理必須送 `X-Forwarded-For`**：上傳 API 靠這個 header 判斷「經過代理 = 不是區網直連」。Nginx Proxy Manager、Caddy、Traefik、Cloudflare Tunnel 預設都會帶；手寫 nginx 設定要加 `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`。沒帶的話上傳仍有 token 保護，但少了區網這一關。
- **用 HTTPS**：分享碼在網址裡，走 http 的話同網路上的人看得到。並把 `PHOTORETURN_PUBLIC_URL` 設成 https 網址。
- **分享連結 = 下載權限**：拿到相簿連結的人能看、能下載整本；拿到單張連結的只能看那一張。原圖保留相機寫入的完整 EXIF（含拍攝時間、相機型號，若相機有開定位也包含 GPS）。
- **token 外洩時**：刪掉 `/etc/photoreturn.env` 裡的 `PHOTORETURN_UPLOAD_TOKEN` 那一行，重跑安裝檔會產生新的，再用新印出的指令更新 Mac。
- 已內建：`Referrer-Policy: no-referrer`（點外部連結不會帶出分享碼）、CSP、禁止被 iframe 嵌入、`noindex`（不被搜尋引擎收錄）、首頁不列出任何相簿。

### LXC 上的位置

- 設定：`/etc/photoreturn.env`；程式：`/opt/photoreturn`（安裝法 B 的原始碼在 `/opt/photoreturn/src`）；資料：`/var/lib/photoreturn`（`originals/`、`derived/`、`photoreturn.db`）
- 服務：`systemctl status photoreturn`、`journalctl -u photoreturn -f`

## Mac 端（ingest）

直接貼上後端安裝完印出的指令即可（第一次會自動建 venv）。指令帶 `--save`，設定會存進 `ingest/.env`，之後只要：

```bash
ingest/ingest.sh                       # 自動找卡（沒插就等），相簿名 = 拍攝日期
ingest/ingest.sh --album 2026-10-25_CWT67 --title "CWT67 Day1"
ingest/ingest.sh --resume 2026-10-25_CWT67   # 中斷後接著跑（不需要卡）
ingest/ingest.sh --list                      # 所有相簿進度與返圖網址
```

| 參數 | 說明 |
|---|---|
| `--server` / `--token` / `--staging` / `--nas` | 連線設定，覆蓋 `.env`；加 `--save` 寫回 `.env` |
| `--card PATH` | 指定記憶卡（預設找 `/Volumes/*` 下有 `DCIM` 的） |
| `--no-eject` | 複製完不自動退出記憶卡 |
| `--rating-overwrite` | 相機內評過的星等也改用 AI 分數（預設相機評分優先） |
| `--retag` | 重新標籤並重新上傳 |
| `--skip-upload` / `--skip-nas` | 略過某一步 |
| `--no-raw` | 這次不把 RAW 傳到返圖站 |
| `--cleanup` | 上傳與 NAS 都成功後刪除暫存區這本相簿 |

說明：

- **拔卡時機**：看到「可以拔卡了」通知就能拔，之後的步驟只讀暫存區。
- **NAS**：如果該共用資料夾（或它的子資料夾）已經被 Finder / Prism 掛載，直接沿用；沒有才用 `mount_smbfs` 掛到 `NAS_MOUNT`。密碼放 `~/Library/Preferences/nsmb.conf`（同 Prism）就不會每次問。
- **重插同一張卡**不會重複複製；每次讀卡放在獨立子資料夾，兩台同型號相機的 `DSC01234.JPG` 不會互相覆蓋。
- **星等**：Prism 的美學分數用 `RATING_THRESHOLDS` 切成 1~5 星，預設 `0,0.4,0.55,0.7`（Prism README 給 cos 的建議值）。
- 標籤直接引用 `PRISM_DIR` 的 `vision_tagger.py`，改 Prism 的 `tag_map.json` 這邊也會跟著生效。

## 分享與下載

- **整本相簿** `/a/<相簿碼>`：右上「下載」可選 JPG / RAW / JPG+RAW（依目前的星等篩選）。
- **單張分享** `/p/<單張碼>`：大圖檢視裡按「分享」，手機開系統分享選單，桌機複製連結。拿到連結的人只看得到這一張，進不去整本相簿。
- **單張下載**：大圖檢視與單張頁都有 JPG、RAW 兩顆按鈕（沒有 RAW 的照片只顯示 JPG）。
- **通訊軟體縮圖**：`/a/`、`/p/` 頁面由後端注入 Open Graph 標籤，預覽圖是 1200px JPG（相簿用星等最高的那張）。
  要讓 LINE / Telegram / Messenger 抓得到縮圖，網站必須能從外網連到，且 `PHOTORETURN_PUBLIC_URL` 要設成對外網址（例如 `https://photos.example.com`）。
- **RAW 上傳**：ingest 預設會把同名 RAW 一起傳到返圖站（`.env` 的 `UPLOAD_RAW=0` 或 `--no-raw` 可關掉；NAS 一律備份 RAW）。

## 後端 API

| Method | Path | 說明 |
|---|---|---|
| POST | `/api/albums` | 建立相簿（token） |
| GET | `/api/albums/{slug}/photo-ids` | 已上傳的 photo_id（token，續傳用） |
| PUT | `/api/albums/{slug}/photos` | 上傳一張 JPG（token，multipart：file + meta JSON） |
| PUT | `/api/albums/{slug}/photos/{photo_id}/raw` | 上傳該張的 RAW（token） |
| GET | `/api/share/{key}` | 相簿內容，已依星等 → 美學分數排序 |
| GET | `/api/share/{key}/zip?min_stars=N&fmt=jpg\|raw\|all` | 串流打包下載 |
| GET | `/api/photo/{pk}` | 單張分享的資料 |
| GET | `/media/{key}/{t\|p\|s\|o\|r}/{sha}` | 縮圖 / 預覽 / 社群預覽 / JPG / RAW |
| GET | `/m/{pk}/{t\|p\|s\|o\|r}` | 單張分享頁用的同上 |

## 前端（web/，Vue 3 + Vite）

```
web/src/
├── App.vue                 /a/<分享碼> → 相簿頁，其他 → 首頁
├── components/AlbumView.vue  星等/標籤篩選、依星等分段、justified 排版
├── components/PhotoTile.vue  縮圖
├── components/Lightbox.vue   大圖、鍵盤 ←/→/Esc、手機左右滑
└── lib/                      api.js（API 與圖片網址）、justify.js（排版演算法）
```

開發：後端跑在 8765，`cd web && npm run dev`，Vite 會把 `/api`、`/media` 轉給後端（可用 `PR_BACKEND` 改位址）。
正式：`npm run build` 產出 `web/dist/`，由 FastAPI 直接提供。

## 本機測試

```bash
cd web && npm install && npm run build && cd ..
cd server && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
PHOTORETURN_DATA=/tmp/pr-data PHOTORETURN_UPLOAD_TOKEN=test PHOTORETURN_UPLOAD_NETS=127.0.0.1/32 .venv/bin/uvicorn app.main:app --port 8765
# ingest 指向別的設定檔：
PR_ENV_FILE=/path/to/test.env ./ingest/ingest.sh --card /path/to/fake_card --no-eject
```

## 回報資安問題

請透過 GitHub 的 [Security Advisories](https://github.com/normantaipei/PhotoReturn/security/advisories/new) 私下回報，不要開公開 issue。

## 授權

MIT
