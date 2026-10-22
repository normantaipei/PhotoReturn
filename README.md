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

這一步**只架伺服器**；兩種方式擇一，裝完會印出 Mac 端要執行的指令（含後端位址與上傳 token）。
暫存資料夾、NAS 備份、每本相簿的名稱，都是之後在 Mac 上傳時才設定（見〔Mac 端〕）。

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

會先詢問 **網站名稱**（整個返圖站的名字）與 **LXC 磁碟大小**，接著自動：
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

伺服器安裝只問一件事（安裝法 B 另外問 LXC 磁碟大小），也可以用參數直接給：

| 問題 | 參數 | 說明 |
|---|---|---|
| 網站名稱 | `--site-name` | **整個返圖站**的名字：首頁標題、瀏覽器分頁標題、貼到通訊軟體時的網站名，預設「返圖」。不是單本相簿的名稱 |

（其他參數：`--port`、`--upload-nets`、`--public-url`、`--no-prompt`，見 `deploy/install.sh` 開頭說明）
忘記 Mac 端指令時，在 LXC 執行 `photoreturn-cmd` 再印一次。
重跑安裝會沿用上次的設定、保留 token 與照片；要改網站名稱，重跑時帶 `--site-name`，或改 `/etc/photoreturn.env` 的 `PHOTORETURN_SITE_NAME` 再 `systemctl restart photoreturn`。
LXC 磁碟不夠時可在 Proxmox 加大（只能加大）：`pct resize <CTID> rootfs 200G`。

### 更新到新版

重跑即為更新。**已上傳的照片、縮圖、資料庫與分享連結都不會動**：更新只替換 `/opt/photoreturn` 裡的程式，資料在 `/var/lib/photoreturn`（或你設定的 `PHOTORETURN_DATA`）完全不碰；資料庫只會加欄位、不刪資料；token、網站名稱、資料位置與你自己加進 `/etc/photoreturn.env` 的設定都會保留。

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

直接貼上後端安裝完印出的指令即可（第一次會自動建 venv）。指令帶 `--save`，設定會存進 `ingest/.env`。
第一次可以順便加上 Mac 端的設定，`--save` 一起記住：

| 參數 | 說明 |
|---|---|
| `--staging <資料夾>` | 暫存資料夾，預設 `~/PhotoReturnStaging` |
| `--nas smb://帳號@NAS_IP/共用資料夾/子資料夾` | NAS 備份位置，不設就不備份 |

沒給 `--album` 時，**相簿名稱自動用拍攝日期**（如 `2026_10_23`），不會詢問；有給的話空白會自動換成底線。之後只要：

```bash
ingest/ingest.sh                       # 自動找卡（沒插就等），相簿名 = 拍攝日期（如 2026_10_23）
ingest/ingest.sh --album 2026_10_25_CWT67 --title "CWT67 Day1"
ingest/ingest.sh --resume 2026_10_25_CWT67   # 中斷後接著跑（不需要卡）
ingest/ingest.sh --list                      # 所有相簿進度與返圖網址
```

### 多台伺服器（不同場次用不同返圖站）

每台伺服器存成一個「伺服器設定」（`ingest/servers/<名稱>.env`，權限 600、不進 git），上傳時用 `--profile` 指定：

```bash
ingest/ingest.sh --profile cwt67 --server http://<伺服器IP>:8080 --token <token> --save-only   # 新增
ingest/ingest.sh --servers                     # 列出全部與連線狀態（不顯示 token）
ingest/ingest.sh --profile cwt67               # 插卡，上傳到 cwt67
ingest/ingest.sh --resume <相簿>               # 自動沿用這本相簿上次的伺服器
```

每本相簿會記住自己傳到哪台；`--resume` 時指定別台會被擋下，確定要換才加 `--force-profile`。
只有一台的話也可以照舊把 `--server` / `--token` 存在 `.env`（不指定 `--profile` 時使用）。

| 參數 | 說明 |
|---|---|
| `--profile` / `--servers` | 選擇 / 列出伺服器設定 |
| `--server` / `--token` / `--nas` | 連線設定；搭配 `--profile … --save-only` 存成伺服器設定 |
| `--staging` | 暫存資料夾（這台 Mac 共用，存在 `.env`） |
| `--card PATH` | 指定記憶卡（預設找 `/Volumes/*` 下有 `DCIM` 的） |
| `--no-eject` | 複製完不自動退出記憶卡 |
| `--rating-overwrite` | 相機內評過的星等也改用 AI 分數（預設相機評分優先） |
| `--retag` | 重新標籤並重新上傳 |
| `--skip-upload` / `--skip-nas` | 略過某一步 |
| `--no-raw` | 這次不把 RAW 傳到返圖站 |
| `--cleanup` | 上傳與 NAS 都成功後刪除暫存區這本相簿 |

說明：

- **上傳出現 `No route to host`（errno 65）**：macOS 15 以後的「本機網路」權限擋住了。到「系統設定 → 隱私權與安全性 → 本機網路」打開你用來執行的 App（終端機、iTerm、VS Code…）；清單沒有的話，從該 App 再跑一次會跳出詢問。之後用 `--resume <相簿>` 接著上傳即可，不用重跑複製與標籤。
- **拔卡時機**：看到「可以拔卡了」通知就能拔，之後的步驟只讀暫存區。
- **NAS**：如果該共用資料夾（或它的子資料夾）已經被 Finder / Prism 掛載，直接沿用；沒有才用 `mount_smbfs` 掛到 `NAS_MOUNT`。密碼放 `~/Library/Preferences/nsmb.conf`（同 Prism）就不會每次問。
- **重插同一張卡**不會重複複製；每次讀卡放在獨立子資料夾，兩台同型號相機的 `DSC01234.JPG` 不會互相覆蓋。
- **星等**：Prism 的美學分數用 `RATING_THRESHOLDS` 切成 1~5 星，預設 `0,0.4,0.55,0.7`（Prism README 給 cos 的建議值）。
- 標籤直接引用 `PRISM_DIR` 的 `vision_tagger.py`，改 Prism 的 `tag_map.json` 這邊也會跟著生效。

### 人臉分組（實驗中）

`--faces`（或 `.env` 的 `FACES=1`）會在標籤之後把整本相簿的人臉分群，返圖頁多一排人物頭像可以篩選，每個人也有自己的分享連結 `/f/<人物碼>`（只看得到有自己的照片）。

- **流程**：macOS Vision 找臉與五官 → 對齊成 112×112 → ArcFace（InsightFace `w600k_r50`，ONNX，Apple Silicon 走 CoreML）算 512 維特徵 → average-linkage 分群。約 0.2 秒/張。
- **隱私**：人臉特徵只存在這台 Mac 暫存區的 `state.db`；返圖站只收到「哪幾張照片是同一個人」與代表照上臉的位置。
- **第一次使用**會自動安裝 `requirements-faces.txt` 的套件，並下載 InsightFace 的 buffalo_l（約 290 MB，核對 SHA-256 後只留 `ingest/models/w600k_r50.onnx`）。**InsightFace 預訓練模型僅限非商業用途。**
- **調整**：`FACE_THRESHOLD`（預設 0.4，越高分得越細）、`FACE_MIN_PHOTOS`（預設 3，少於這個張數的視為路人）、`FACE_MIN_PX`。
- **重新分群**：每次執行都會整本重新分群（新插的卡會一起算）；人物連結會沿用照片重疊最多的舊分組，已經發出去的連結不會失效。
- **限制**：只認得到臉。戴墨鏡、側臉、背影、遠景小人物常常偵測不到，這些照片不會出現在任何人物底下（實測一場單人外拍約 1/3 的照片偵測得到臉）。

## Claude Code skill

`skills/photoreturn-ingest/` 是給 Claude Code 用的 skill：跟 Claude 說「插卡了，幫我傳到返圖站」，它會先問這次要傳到哪台伺服器（或幫你新增），再帶好參數執行 ingest、回報進度與返圖網址。skill 裡不含任何伺服器位址或 token，這些只存在你 Mac 上的伺服器設定。

安裝（連結過去，`git pull` 就會更新）：

```bash
ln -s "$PWD/skills/photoreturn-ingest" ~/.claude/skills/photoreturn-ingest
```

## 分享與下載

- **整本相簿** `/a/<相簿碼>`：右上「下載」可選 JPG / RAW / JPG+RAW（依目前的星等篩選）。
- **人物分享** `/f/<人物碼>`（需開啟人臉分組）：相簿頁選一個人物頭像，按「分享這個人的照片」。拿到連結的人只看得到這個人的照片，可整批下載。
- **單張分享** `/p/<單張碼>`：大圖檢視裡按「分享」，手機開系統分享選單，桌機複製連結。拿到連結的人只看得到這一張，進不去整本相簿。
- **單張下載**：大圖檢視與單張頁都有 JPG、RAW 兩顆按鈕（沒有 RAW 的照片只顯示 JPG）。
- **通訊軟體縮圖**：`/a/`、`/p/` 頁面由後端注入 Open Graph 標籤，預覽卡片固定 1200×630（LINE、FB、Discord 的顯示比例，不會被亂裁）：
  - 相簿：星等最高的前 3 張並排拼圖，每格寬度依照片比例分配、偏上裁切保住臉。
  - 單張：整張照片完整置中，背景用同一張模糊壓暗，直幅也不會被裁頭。
  - 卡片網址含內容指紋，前幾名換了照片網址就換，通訊軟體會重抓新縮圖。
  要讓 LINE / Telegram / Messenger 抓得到縮圖，網站必須能從外網連到，且 `PHOTORETURN_PUBLIC_URL` 要設成對外網址（例如 `https://photos.example.com`）。
- **RAW 上傳**：ingest 預設會把同名 RAW 一起傳到返圖站（`.env` 的 `UPLOAD_RAW=0` 或 `--no-raw` 可關掉；NAS 一律備份 RAW）。

## 後端 API

| Method | Path | 說明 |
|---|---|---|
| POST | `/api/albums` | 建立相簿（token） |
| GET | `/api/albums/{slug}/photo-ids` | 已上傳的 photo_id（token，續傳用） |
| PUT | `/api/albums/{slug}/photos` | 上傳一張 JPG（token，multipart：file + meta JSON） |
| PUT | `/api/albums/{slug}/photos/{photo_id}/raw` | 上傳該張的 RAW（token） |
| PUT | `/api/albums/{slug}/people` | 整本相簿的人物分組（token，JSON：每人的 photo_id 與代表臉位置） |
| GET | `/api/share/{key}` | 相簿內容，已依星等 → 美學分數排序（含人物清單） |
| GET | `/api/person/{key}`、`/api/person/{key}/zip` | 某個人物的照片與打包下載 |
| GET | `/fm/{key}/{t\|p\|o\|r}/{sha}`、`/face/{key}/{指紋}.jpg` | 人物頁的圖片、人物頭像 |
| GET | `/api/share/{key}/zip?min_stars=N&fmt=jpg\|raw\|all` | 串流打包下載 |
| GET | `/api/photo/{pk}` | 單張分享的資料 |
| GET | `/media/{key}/{t\|p\|o\|r}/{sha}` | 縮圖 / 預覽 / JPG / RAW |
| GET | `/m/{pk}/{t\|p\|o\|r}` | 單張分享頁用的同上 |
| GET | `/og/a/{key}/{指紋}.jpg`、`/og/p/{pk}/{指紋}.jpg` | 通訊軟體預覽卡片（1200×630） |

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
