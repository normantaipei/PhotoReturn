---
name: photoreturn-ingest
description: PhotoReturn 返圖上傳——在 Mac 上把記憶卡（或含 DCIM 的資料夾）的照片複製到暫存區、用 Prism / macOS Vision 自動加標籤與 AI 星等，上傳到指定的返圖站伺服器，並可備份到 NAS。每當使用者說「插卡了 / 上傳返圖 / 把這張卡傳上去 / 跑 ingest / 新增或切換返圖伺服器 / 這場要傳到哪台 / 補傳 RAW / 上傳失敗了接著傳 / 看相簿上傳進度」時使用；即使只說「這批照片幫我傳到返圖站」或給了一個拍攝資料夾，也用這個 skill。
---

# PhotoReturn 返圖上傳（Mac 端）

把一場拍攝的照片送上返圖站：**複製 → 標籤評分 → 上傳 → NAS 備份**，全部由 repo 裡的
`ingest/ingest.sh` 完成。這個 skill 負責的是「幫使用者把這支腳本跑對」：選對伺服器、帶對參數、
盯進度、處理錯誤、回報結果。

不同場次 / 活動可能用不同的返圖站伺服器，所以**伺服器位址與 token 從不寫在這個 skill 或 repo 裡**，
而是存在這台 Mac 的「伺服器設定」（`ingest/servers/<名稱>.env`，權限 600、不進 git），
每個對話開始上傳前由使用者決定這次要傳到哪一台。

## 0. 找到 repo

先找 PhotoReturn 資料夾（裡面要有 `ingest/ingest.sh`），常見在 `~/SideProject/PhotoReturn`：

```bash
ls ~/SideProject/PhotoReturn/ingest/ingest.sh 2>/dev/null || find ~ -maxdepth 4 -path '*PhotoReturn/ingest/ingest.sh' 2>/dev/null | head -1
```

找不到就請使用者告訴你位置，或 `git clone https://github.com/normantaipei/PhotoReturn`
（Prism 也要 clone 在旁邊：`https://github.com/normantaipei/Prism`）。以下指令都在 PhotoReturn 資料夾內執行。
第一次跑 `ingest/ingest.sh` 會自動建 Python 環境，要等一兩分鐘。

## 1. 這次要傳到哪台伺服器（每個對話第一次上傳前都要確認）

不要沿用上一個對話的選擇——使用者可能換了場次。先列出已設定的伺服器：

```bash
ingest/ingest.sh --servers
```

會顯示每個設定的名稱、網址、token 是否已設定、連線狀態與伺服器剩餘空間（不會顯示 token）。
把清單整理給使用者，請他選一台；這個對話之後的上傳都用這台（記在心裡，每次都帶 `--profile <名稱>`）。
使用者一開口就指定了要新增的伺服器時，先存（見下）再 `--servers` 確認連線與剩餘空間即可，不必先列再問。

**要新增伺服器時**，向使用者要三樣東西：

- 名稱：自己認得的就好，例如 `cwt67`、`home-studio`（文字、數字、`. _ -`）
- 後端網址：例如 `http://<伺服器的區網IP>:8080`（伺服器安裝完印出的 `--server` 那串；之後在 LXC 執行 `photoreturn-cmd` 可再印一次）
- 上傳 token：同上，`--token` 那串

然後存起來（`--save-only`：只存設定，不讀卡也不上傳）：

```bash
ingest/ingest.sh --profile <名稱> --server <網址> --token <token> --save-only
```

選填：這台伺服器對應的 NAS 備份位置，加 `--nas "smb://帳號@NAS_IP/共用資料夾/子資料夾"`。

token 是上傳權限：存完之後的回覆裡不要再把它寫出來，也不要寫進任何檔案或 commit。
`.env` 裡如果已經有舊的單一伺服器設定，可以用 `ingest/ingest.sh --profile <名稱> --save-only` 轉存成具名設定。

## 2. 確認來源與這次的選項

問清楚再開始，因為複製途中改不了：

| 項目 | 怎麼決定 | 參數 |
|---|---|---|
| 來源 | 不給就自動找 `/Volumes` 下有 `DCIM` 的記憶卡（沒插會一直等）；指定資料夾時，它底下要有 `DCIM` | `--card <路徑>` |
| 複製完退出磁碟 | 真的記憶卡預設會退出；來源是 NAS、硬碟上的一般資料夾時不要退 | `--no-eject` |
| 相簿名稱 | 不帶就自動用拍攝日期（如 `2026_10_23`）；要自訂的話建議「日期_活動」，日期一律用底線，如 `2026_11_08_台北外拍`；只能用文字、數字、`. _ -`（空白換底線） | `--album <名稱>`，頁面標題不同時加 `--title` |
| RAW 要不要上傳 | 預設上傳。先估大小：比對 `--servers` 顯示的剩餘空間，不夠就建議只傳 JPG 或請使用者先擴容 | `--no-raw` |
| 要不要人臉分組 | 預設看 `.env` 的 `FACES`。多人場次（團拍、展場）才有意義；第一次會下載約 290 MB 模型，InsightFace 模型僅限非商業用途 | `--faces` / `--no-faces` |
| NAS 備份 | 伺服器設定或 `.env` 有設 NAS 才會備份；來源本身就在 NAS 上時要跳過，免得重複 | `--skip-nas` |
| 星等 | 新照片用 AI 美學分數算（預設門檻 `0,0.4,0.55,0.7`）；已有星等的照片沿用，要重算加參數 | `--rating-overwrite` |

估大小的方法（來源路徑換成實際的）：

```bash
for e in JPG HEIC HIF ARW CR2 CR3 NEF RAF ORF RW2 DNG; do
  find "<來源>/DCIM" -type f -iname "*.$e" -exec stat -f %z {} + 2>/dev/null \
    | awk -v e=$e '{s+=$1} END {if (NR) printf "%s %d 張 %.1f GB\n", e, NR, s/1e9}'
done
```

## 3. 執行（放背景，邊跑邊回報）

整個流程可能要幾十分鐘（從 NAS 複製特別慢），用背景執行並把輸出寫到 log：

```bash
ingest/ingest.sh --profile <名稱> --album <相簿名稱> [其他參數] > ~/PhotoReturnStaging/ingest-<相簿名稱>.log 2>&1
```

log 放暫存資料夾就好（`ingest/.env` 的 `STAGING_DIR`，預設 `~/PhotoReturnStaging`；這也是照片暫存的位置）。
執行中讀 log 回報進度。四個階段：`[1/4 複製]` → `[2/4 標籤]` → `[3/4 上傳]` → `[4/4 NAS]`；
加了 `--skip-nas` 或沒設 NAS 時不會有第 4 段。

- log 出現「**複製完成**」（後面接「可以…拔卡了」）要馬上告訴使用者——之後的步驟都不需要記憶卡，他可以先拔卡。
- 進度行（`123/976 …`）很多，回報時只講百分比與預估時間就好。

## 4. 出問題時

腳本每一步的進度都記在暫存區，修好後用 `--resume` 接著跑，**不會重新複製或重跑標籤**；
`--resume` 會自動沿用這本相簿上次的伺服器（要改傳到別台才需要 `--force-profile`）。

```bash
ingest/ingest.sh --resume <相簿名稱> [--no-raw] [--skip-nas]
```

| 訊息 | 原因與處理 |
|---|---|
| `No route to host` / `Errno 65` | macOS 15+ 的「本機網路」權限擋住了 Python 連區網。請使用者到「系統設定 → 隱私權與安全性 → 本機網路」打開他用來執行的 App（終端機、iTerm、VS Code、Claude…），再 `--resume`。若是你（Claude）的執行環境沒權限又無法開，請使用者在自己的終端機貼上 `--resume` 指令執行 |
| `後端拒絕（401）` | token 錯：重新跟使用者要 token，`--profile <名稱> --token <新token> --save-only` 更新 |
| `後端拒絕（403）` | 這台 Mac 不在伺服器允許上傳的網段（伺服器 `/etc/photoreturn.env` 的 `PHOTORETURN_UPLOAD_NETS`），或經過反向代理連線——上傳必須用區網位址直連，不能用對外的 https 網域 |
| 上傳到一半失敗、伺服器空間不足 | `--servers` 看剩餘空間；請使用者擴容（Proxmox：`pct resize <CTID> rootfs <大小>G`）或改 `--no-raw` 再 `--resume` |
| `NAS 掛載失敗` | NAS 密碼放 `~/Library/Preferences/nsmb.conf` 就不用互動輸入；或這次先 `--skip-nas` |
| 「⚠ 有步驟未完成」但只差 NAS | 刻意 `--skip-nas` 或沒設 NAS 時都會出現，這是正常的，不用處理 |

## 5. 完成後回報

- 返圖網址：上傳階段結尾一定有一行 `返圖網址：`（全部步驟都成功時最後還會有 `🎉 完成：`）。
  這是伺服器設定的網址，常是區網位址，只有在同一個網路連得到。伺服器若有對外的 https 網域，
  分享給 coser 時把網址前面換成網域（路徑 `/a/<分享碼>` 不變）；沒有對外網域的話要提醒使用者，
  這個連結離開這個網路就打不開。
- 張數與星等分布——從伺服器取得（網址最後一段是相簿分享碼）：

  ```bash
  curl -s <伺服器網址>/api/share/<相簿分享碼> | python3 -c "import json,sys,collections; p=json.load(sys.stdin)['photos']; print(len(p),'張', sorted(collections.Counter(x['stars'] for x in p).items()))"
  ```
- 有哪些步驟刻意跳過（例如沒傳 RAW、沒備份 NAS），以及之後要補做時的 `--resume` 指令。
- 暫存區（預設 `~/PhotoReturnStaging/<相簿>`）會保留含 RAW 的完整副本，之後補傳 RAW 或重算星等都用得到；確定不需要了再請使用者自己刪，或下次加 `--cleanup`（上傳與 NAS 都成功才會刪）。

`ingest/ingest.sh --list` 可以看暫存區所有相簿的進度與各自傳到哪台伺服器。
