#!/usr/bin/env python3
"""
PhotoReturn ingest — 回家插卡後跑這支：

  1. 複製：記憶卡 → Mac 暫存區（邊複製邊算 SHA-256），完成後自動退出記憶卡、跳通知「可以拔卡」
  2. 標籤：用 Prism（macOS Vision）加中英標籤、美學分數 → 星等，寫進 IPTC/XMP
           JPG 直接寫入；RAW 寫 .xmp sidecar，原始 RAW 不動
  3. 上傳：JPG 上傳到返圖站（只有 RAW 的照片先用 sips 轉一張 JPG 再上傳）
  4. 備份：rsync 整個相簿（JPG + RAW + sidecar）到 NAS，並再跑一次比對確認

每一步的進度都記在暫存區的 state.db，中斷後用 --resume 接著跑。

用法:
  ./ingest.sh                                 # 自動找記憶卡（沒插就等），相簿名預設為拍攝日期
  ./ingest.sh --album 2026-10-25_CWT67 --title "CWT67 Day1"
  ./ingest.sh --resume 2026-10-25_CWT67       # 不讀卡，繼續之前沒做完的步驟
  ./ingest.sh --list                          # 列出暫存區的相簿與進度
"""

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import warnings
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")
import requests  # noqa: E402

HERE = Path(__file__).resolve().parent
VIDEO_SKIP = {".mp4", ".mov", ".mts", ".m2ts", ".avi", ".xml", ".thm", ".lrv", ".bin", ".dat"}


# ---------- 設定 ----------

def load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.startswith("PR_")})
    return env


ENV_FILE = Path(os.environ.get("PR_ENV_FILE", HERE / ".env"))
ENV = load_env(ENV_FILE)


def save_env(updates: dict):
    """把指定的設定寫回 .env（已有的 key 就地更新，沒有的加在最後）。"""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    left = dict(updates)
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0].strip()
        if not line.lstrip().startswith("#") and k in left:
            lines[i] = f"{k}={left.pop(k)}"
    lines += [f"{k}={v}" for k, v in left.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ENV_FILE.chmod(0o600)  # 有 token


def cfg(key, default=""):
    return ENV.get(key, default)


def cfg_path(key, default=""):
    v = cfg(key, default)
    return Path(os.path.expandvars(v)).expanduser() if v else None


def notify(msg: str, sound: str = "Glass"):
    """macOS 通知 + 音效，拔卡時不用盯著終端機。"""
    subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "PhotoReturn"'],
                   capture_output=True)
    snd = Path(f"/System/Library/Sounds/{sound}.aiff")
    if snd.exists():
        subprocess.Popen(["afplay", str(snd)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def log(msg=""):
    print(msg, flush=True)


# ---------- Prism ----------

def load_prism():
    prism_dir = cfg_path("PRISM_DIR") or HERE.parent.parent / "Prism"  # 預設：和 PhotoReturn 同一層
    src = prism_dir / "scripts" / "vision_tagger.py"
    if not src.exists():
        sys.exit(f"找不到 Prism：{src}（請設定 .env 的 PRISM_DIR）")
    spec = importlib.util.spec_from_file_location("prism_tagger", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.raise_fd_limit()
    return mod


# ---------- 狀態 ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS albums (
    name TEXT PRIMARY KEY,
    title TEXT,
    shoot_date TEXT,
    created REAL,
    share_url TEXT,
    copy_done INTEGER DEFAULT 0,
    tag_done INTEGER DEFAULT 0,
    upload_done INTEGER DEFAULT 0,
    nas_done INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    album TEXT NOT NULL,
    rel TEXT NOT NULL,            -- 相對於相簿資料夾
    src_key TEXT NOT NULL,        -- 卡上路徑+大小+時間，重插同一張卡不重複複製
    size INTEGER,
    sha256 TEXT,                  -- 卡上原始檔的 hash（= 返圖站的 photo_id）
    kind TEXT,                    -- img / raw
    tagged INTEGER DEFAULT 0,
    stars INTEGER,
    aesthetic REAL,
    tags TEXT,
    taken_at TEXT,
    web_rel TEXT,                 -- 要上傳的 JPG（RAW-only 時是轉出來的那張）
    uploaded INTEGER DEFAULT 0,
    raw_uploaded INTEGER DEFAULT 0,   -- 對應的 RAW 是否已上傳（記在主檔那一列）
    UNIQUE (album, src_key)
);
"""


def open_db(staging: Path):
    staging.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(staging / "state.db")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    if "raw_uploaded" not in {r["name"] for r in con.execute("PRAGMA table_info(files)")}:
        con.execute("ALTER TABLE files ADD COLUMN raw_uploaded INTEGER DEFAULT 0")  # 記在主檔那一列
    return con


# ---------- 1. 複製 ----------

def find_cards():
    vols = Path("/Volumes")
    return sorted(v for v in vols.iterdir() if (v / "DCIM").is_dir()) if vols.exists() else []


def wait_for_card() -> Path:
    shown = False
    while True:
        cards = find_cards()
        if len(cards) == 1:
            return cards[0]
        if len(cards) > 1:
            sys.exit("偵測到多張記憶卡，請用 --card 指定：\n  " + "\n  ".join(map(str, cards)))
        if not shown:
            log("等待插入記憶卡…（Ctrl-C 取消）")
            shown = True
        time.sleep(2)


def list_card_files(card: Path, prism):
    exts = prism.IMAGE_EXT | prism.RAW_EXT
    out = []
    for root, dirs, files in os.walk(card / "DCIM"):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if name.startswith("."):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in exts:
                p = Path(root) / name
                out.append((p, p.stat()))
    return out


def copy_hash(src: Path, dst: Path) -> str:
    h = hashlib.sha256()
    tmp = dst.with_name(dst.name + ".part")
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        for chunk in iter(lambda: fi.read(4 << 20), b""):
            h.update(chunk)
            fo.write(chunk)
        fo.flush()
        os.fsync(fo.fileno())
    shutil.copystat(src, tmp)   # 保留修改時間（沒有 EXIF 時的拍攝時間依據）
    os.replace(tmp, dst)
    return h.hexdigest()


def step_copy(con, staging: Path, card: Path, album: str, title: str, prism, eject: bool):
    files = list_card_files(card, prism)
    if not files:
        sys.exit(f"{card} 裡沒有照片")
    shoot_date = dt.date.fromtimestamp(min(st.st_mtime for _, st in files)).isoformat()
    album = album or shoot_date
    con.execute("INSERT OR IGNORE INTO albums (name, title, shoot_date, created) VALUES (?,?,?,?)",
                (album, title or album, shoot_date, time.time()))
    if title:
        con.execute("UPDATE albums SET title=? WHERE name=?", (title, album))
    con.commit()

    # 每張卡一個子資料夾：兩台同型號相機都是 DSC01234.JPG，分開放就不會撞名
    run_dir = f"{card.name}_{dt.datetime.now():%Y%m%d-%H%M%S}"
    album_dir = staging / album
    todo = []
    for p, st in files:
        key = f"{p.relative_to(card)}|{st.st_size}|{int(st.st_mtime)}"
        if not con.execute("SELECT 1 FROM files WHERE album=? AND src_key=?", (album, key)).fetchone():
            todo.append((p, st, key))

    total = sum(st.st_size for _, st, _ in todo)
    log(f"\n[1/4 複製] {card} → {album_dir}")
    log(f"  卡上 {len(files)} 個檔案，需複製 {len(todo)} 個（{total / 1e9:.2f} GB）")
    done_bytes, t0, last = 0, time.time(), 0.0
    for i, (p, st, key) in enumerate(todo, 1):
        rel = Path(run_dir) / p.relative_to(card / "DCIM")
        dst = album_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        sha = copy_hash(p, dst)
        if dst.stat().st_size != st.st_size:
            sys.exit(f"複製後大小不符：{p}（請勿拔卡，重新執行）")
        kind = "raw" if p.suffix.lower() in prism.RAW_EXT else "img"
        con.execute("INSERT INTO files (album, rel, src_key, size, sha256, kind) VALUES (?,?,?,?,?,?)",
                    (album, str(rel), key, st.st_size, sha, kind))
        con.commit()
        done_bytes += st.st_size
        if time.time() - last > 1 or i == len(todo):
            speed = done_bytes / max(time.time() - t0, 1e-6) / 1e6
            log(f"  {i}/{len(todo)}  {done_bytes / 1e9:.2f}/{total / 1e9:.2f} GB  {speed:.0f} MB/s")
            last = time.time()
    con.execute("UPDATE albums SET copy_done=1, tag_done=0, upload_done=0, nas_done=0 WHERE name=?", (album,))
    con.commit()

    if eject:
        r = subprocess.run(["diskutil", "eject", str(card)], capture_output=True, text=True)
        ejected = r.returncode == 0
    else:
        ejected = False
    msg = "複製完成，記憶卡已退出，可以拔卡了" if ejected else "複製完成，可以退出並拔卡了"
    log(f"\n  ✅ {msg}\n")
    notify(msg)
    return album


# ---------- 2. 標籤 ----------

def exif_batch(paths):
    """一次讀出所有檔案的拍攝時間與相機內星等（比每張開一次 exiftool 快很多）。"""
    out = {}
    for i in range(0, len(paths), 500):
        chunk = [str(p) for p in paths[i:i + 500]]
        r = subprocess.run(["exiftool", "-json", "-q", "-DateTimeOriginal", "-SubSecTimeOriginal",
                            "-Rating", "-@", "-"], input="\n".join(chunk),
                           capture_output=True, text=True)
        for d in json.loads(r.stdout or "[]"):
            out[d["SourceFile"]] = d
    return out


def taken_at_iso(d: dict):
    v = d.get("DateTimeOriginal")
    if not v or v.startswith("0000"):
        return None
    try:
        t = dt.datetime.strptime(v[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    sub = str(d.get("SubSecTimeOriginal", "") or "")
    return t.isoformat() + (f".{sub[:3].ljust(3, '0')}" if sub.isdigit() else "")


def step_tag(con, staging: Path, album: str, prism, args):
    rows = con.execute("SELECT * FROM files WHERE album=?", (album,)).fetchall()
    album_dir = staging / album

    # 同目錄同檔名：JPG 為主（分析、上傳），RAW 跟著寫 sidecar；只有 RAW 時以 RAW 為主
    groups = {}
    for r in rows:
        stem = str(Path(r["rel"]).with_suffix("")).lower()
        groups.setdefault(stem, []).append(r)
    primaries = []
    for items in groups.values():
        imgs = [r for r in items if r["kind"] == "img"]
        primaries.append((imgs[0] if imgs else items[0], [r for r in items if r["kind"] == "raw"]))
    todo = [(p, raws) for p, raws in primaries if not p["tagged"]]
    log(f"[2/4 標籤] Prism / Vision：{len(todo)} 張待處理（共 {len(primaries)} 張）")
    if not todo:
        con.execute("UPDATE albums SET tag_done=1 WHERE name=?", (album,))
        con.commit()
        return

    tag_map = prism.load_tag_map()
    missing = set()
    thresholds = sorted(float(x) for x in cfg("RATING_THRESHOLDS", "0,0.4,0.55,0.7").split(","))
    lang = cfg("TAG_LANG", "both")
    marker = cfg("TAG_MARKER", "AI_VISION")
    exif = exif_batch([album_dir / p["rel"] for p, _ in todo])

    t0 = time.time()
    for i, (p, raws) in enumerate(todo, 1):
        path = album_dir / p["rel"]
        try:
            labels, score, _ = prism.analyze(path, float(cfg("TAG_MIN_CONF", "0.3")), int(cfg("TAG_TOP", "12")))
        except Exception as e:
            log(f"  ⚠ {p['rel']} 分析失敗：{e}")
            continue
        tags = prism.localize([l for l, _ in labels], tag_map, lang, missing)
        meta = exif.get(str(path), {})
        cam_rating = meta.get("Rating")
        if cam_rating and not args.rating_overwrite:
            stars = int(cam_rating)          # 相機上自己評過的星等優先
        else:
            stars = prism.score_to_stars(score, thresholds)

        if p["kind"] == "img":
            ok = prism.write_tags(path, tags, "sidecar", marker, stars, fill_date=False)
            web_rel = p["rel"]
        else:
            # RAW-only：標籤寫 sidecar，另外轉一張 JPG 給返圖站
            side = path.with_suffix(path.suffix + ".xmp")
            if not side.exists():
                subprocess.run(["exiftool", "-q", "-o", str(side), "-TagsFromFile", str(path), "-xmp:all"],
                               capture_output=True)
            ok = prism.write_tags(side, tags, "none", marker, stars, fill_date=False)
            web = album_dir / ".web" / Path(p["rel"]).with_suffix(".jpg")
            web.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "92",
                                str(path), "--out", str(web)], capture_output=True, text=True)
            if r.returncode != 0:
                log(f"  ⚠ {p['rel']} RAW 轉 JPG 失敗：{r.stderr.strip()}")
                ok = False
            web_rel = str(web.relative_to(album_dir))
        if not ok:
            continue

        con.execute("""UPDATE files SET tagged=1, stars=?, aesthetic=?, tags=?, taken_at=?, web_rel=?
                       WHERE id=?""",
                    (stars, score, json.dumps(tags, ensure_ascii=False), taken_at_iso(meta), web_rel, p["id"]))
        for raw in raws:
            con.execute("UPDATE files SET tagged=1, stars=? WHERE id=?", (stars, raw["id"]))
        con.commit()
        if i % 20 == 0 or i == len(todo):
            log(f"  {i}/{len(todo)}  {(time.time() - t0) / i:.2f} 秒/張")

    left = sum(1 for p, _ in primaries
               if not con.execute("SELECT tagged FROM files WHERE id=?", (p["id"],)).fetchone()[0])
    con.execute("UPDATE albums SET tag_done=? WHERE name=?", (int(left == 0), album))
    con.commit()
    if left:
        log(f"  ⚠ 有 {left} 張標籤失敗，可之後用 --resume 重試")
    if missing and lang != "en":
        log(f"  （{len(missing)} 個標籤沒有中文對照，可補進 Prism 的 tag_map.json）")


# ---------- 3. 上傳 ----------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def step_upload(con, staging: Path, album: str):
    server = cfg("SERVER_URL").rstrip("/")
    token = cfg("UPLOAD_TOKEN")
    if not server or not token:
        log("[3/4 上傳] 略過：.env 沒有設定 SERVER_URL / UPLOAD_TOKEN")
        return
    a = con.execute("SELECT * FROM albums WHERE name=?", (album,)).fetchone()
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"

    r = s.post(f"{server}/api/albums", json={"slug": album, "title": a["title"], "shoot_date": a["shoot_date"]},
               timeout=30)
    if r.status_code in (401, 403):
        try:
            why = r.json().get("detail")
        except ValueError:
            why = r.text
        log(f"[3/4 上傳] ⚠ 後端拒絕（{r.status_code}）：{why}")
        log("  401 = token 錯；403 = 這台 Mac 不在允許上傳的網段（LXC 的 /etc/photoreturn.env 的 PHOTORETURN_UPLOAD_NETS）")
        return
    r.raise_for_status()
    share = r.json()["share_url"]
    if share.startswith("/"):
        share = server + share
    con.execute("UPDATE albums SET share_url=? WHERE name=?", (share, album))
    con.commit()

    remote = s.get(f"{server}/api/albums/{album}/photo-ids", timeout=30).json()
    have, have_raw = set(remote["photo_ids"]), set(remote["with_raw"])
    upload_raw = cfg("UPLOAD_RAW", "1") == "1"

    all_rows = con.execute("SELECT * FROM files WHERE album=?", (album,)).fetchall()
    stem = lambda rel: str(Path(rel).with_suffix("")).lower()
    raw_by_stem = {stem(r["rel"]): r for r in all_rows if r["kind"] == "raw"}

    def raw_of(row):
        """這張照片對應的 RAW：本身就是 RAW（RAW-only），或同目錄同檔名的 RAW。"""
        return row if row["kind"] == "raw" else raw_by_stem.get(stem(row["rel"]))

    rows = [r for r in all_rows if r["tagged"] and r["web_rel"]]
    jobs = []
    for r in rows:
        need_jpg = r["sha256"] not in have or not r["uploaded"]
        raw = raw_of(r) if upload_raw else None
        need_raw = bool(raw) and (r["sha256"] not in have_raw or not r["raw_uploaded"])
        if need_jpg or need_raw:
            jobs.append((r, need_jpg, raw if need_raw else None))
    n_raw = sum(1 for _, _, raw in jobs if raw)
    log(f"[3/4 上傳] {server}：{sum(1 for _, j, _ in jobs if j)} 張 JPG、{n_raw} 個 RAW 待上傳"
        f"（共 {len(rows)} 張）" + ("" if upload_raw else "，RAW 不上傳"))

    def put(url, path: Path, meta: dict, mime: str):
        meta = {**meta, "sha256": sha256_file(path), "filename": path.name}
        for attempt in range(4):
            try:
                with open(path, "rb") as f:
                    resp = s.put(url, files={"file": (path.name, f, mime)},
                                 data={"meta": json.dumps(meta, ensure_ascii=False)}, timeout=900)
                if resp.status_code < 500:
                    resp.raise_for_status()
                    return
            except requests.RequestException as e:
                if attempt == 3 or (getattr(e, "response", None) is not None and e.response.status_code < 500):
                    raise
            time.sleep(2 ** attempt)
        raise RuntimeError(f"{path.name} 上傳失敗")

    def upload(job):
        row, need_jpg, raw = job
        if need_jpg:
            put(f"{server}/api/albums/{album}/photos", staging / album / row["web_rel"],
                {"photo_id": row["sha256"], "stars": row["stars"], "aesthetic": row["aesthetic"],
                 "tags": json.loads(row["tags"] or "[]"), "taken_at": row["taken_at"]}, "image/jpeg")
            con_w.append(("UPDATE files SET uploaded=1 WHERE id=?", row["id"]))
        if raw:
            put(f"{server}/api/albums/{album}/photos/{row['sha256']}/raw", staging / album / raw["rel"],
                {}, "application/octet-stream")
            con_w.append(("UPDATE files SET raw_uploaded=1 WHERE id=?", row["id"]))

    failed = 0
    t0 = time.time()
    con_w = []  # 執行緒裡不碰 SQLite，結果交回主執行緒寫入
    with cf.ThreadPoolExecutor(max_workers=int(cfg("UPLOAD_WORKERS", "3"))) as ex:
        futs = {ex.submit(upload, j): j for j in jobs}
        for i, fut in enumerate(cf.as_completed(futs), 1):
            try:
                fut.result()
            except Exception as e:
                failed += 1
                log(f"  ⚠ {futs[fut][0]['web_rel']}：{e}")
            while con_w:
                sql, rid = con_w.pop()
                con.execute(sql, (rid,))
            con.commit()
            if i % 20 == 0 or i == len(jobs):
                log(f"  {i}/{len(jobs)}  {(time.time() - t0) / i:.2f} 秒/張")
    con.execute("UPDATE albums SET upload_done=? WHERE name=?", (int(failed == 0), album))
    con.commit()
    log(f"  返圖網址：{share}" + (f"（{failed} 張失敗，可 --resume 重試）" if failed else ""))


# ---------- 4. NAS 備份 ----------

def parse_smb(url: str):
    """smb://帳號@主機/共用資料夾/子資料夾 → (帳號, 主機, 共用資料夾, [子資料夾...])"""
    u = urlsplit(url)
    parts = [unquote(p) for p in u.path.split("/") if p]
    if u.scheme != "smb" or not u.hostname or not parts:
        raise RuntimeError(f"NAS 位址格式應為 smb://帳號@NAS_IP/共用資料夾/子資料夾，收到：{url}")
    return u.username, u.hostname, parts[0], parts[1:]


def find_smb_mount(host: str, parts):
    """這個位置已經掛載（Finder 或 Prism 掛過，可能掛的是子資料夾）就直接用，不重複掛。"""
    norm = lambda h: h.lower().removesuffix(".local")
    want = [p.lower() for p in parts]
    for line in subprocess.run(["mount"], capture_output=True, text=True).stdout.splitlines():
        if "(smbfs" not in line:
            continue
        src, _, rest = line.partition(" on ")
        mp = rest.rsplit(" (", 1)[0]
        h, _, path = unquote(src.lstrip("/").rpartition("@")[2]).partition("/")
        have = [p.lower() for p in path.split("/") if p]
        if norm(h) == norm(host) and have and want[:len(have)] == have:
            return Path(mp).joinpath(*parts[len(have):])
    return None


def nas_root():
    """回傳 NAS 備份根目錄（需要時自動 mount_smbfs）；沒設定則回傳 None。
    NAS_SMB 也可以直接寫本機路徑（例如已掛載的 /Volumes/xxx），方便測試。"""
    nas = cfg("NAS_SMB")
    if not nas:
        return None
    if not nas.startswith("smb://"):
        return Path(os.path.expandvars(nas)).expanduser()
    user, host, share, sub = parse_smb(nas)
    found = find_smb_mount(host, [share] + sub)
    if found:
        return found
    mp = cfg_path("NAS_MOUNT", "~/mnt/PhotoReturnNAS")
    mp.mkdir(parents=True, exist_ok=True)
    src = f"//{quote(user) + '@' if user else ''}{host}/{quote(share)}"
    # 密碼從 ~/Library/Preferences/nsmb.conf 或鑰匙圈讀；都沒有時 mount_smbfs 會在終端機問
    r = subprocess.run(["mount_smbfs", src, str(mp)], stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"NAS 掛載失敗（{src}）：{r.stderr.strip()}")
    return mp.joinpath(*sub)


def step_nas(con, staging: Path, album: str):
    try:
        root = nas_root()
    except RuntimeError as e:
        log(f"[4/4 NAS] ⚠ {e}")
        return
    if not root:
        log("[4/4 NAS] 略過：沒有設定 NAS 位址（--nas 或 .env 的 NAS_SMB）")
        return
    src = staging / album
    dst = root / album
    dst.mkdir(parents=True, exist_ok=True)
    log(f"[4/4 NAS] {src} → {dst}")
    # -rt：SMB 上不同步權限/擁有者，只同步內容與修改時間
    base = ["rsync", "-rt", "--exclude", ".web", "--exclude", "*.part", f"{src}/", f"{dst}/"]
    r = subprocess.run(base[:1] + ["--partial"] + base[1:])
    if r.returncode != 0:
        log(f"  ⚠ rsync 失敗（exit {r.returncode}），可 --resume 重試")
        return
    # 驗證：再比一次，大小或時間不同的檔案會被列出來
    chk = subprocess.run(base[:1] + ["-n", "--itemize-changes"] + base[1:], capture_output=True, text=True)
    diff = [l for l in chk.stdout.splitlines() if l.strip() and not l.startswith(("cd", ".d"))]
    if chk.returncode != 0 or diff:
        log(f"  ⚠ 比對後仍有 {len(diff)} 個檔案不一致，可 --resume 重試")
        return
    n = sum(1 for f in src.rglob("*") if f.is_file() and ".web" not in f.parts)
    con.execute("UPDATE albums SET nas_done=1 WHERE name=?", (album,))
    con.commit()
    log(f"  ✅ NAS 備份完成並比對一致（{n} 個檔案）")


# ---------- main ----------

def show_list(con):
    rows = con.execute("SELECT a.*, (SELECT COUNT(*) FROM files f WHERE f.album=a.name) n "
                       "FROM albums a ORDER BY created DESC").fetchall()
    if not rows:
        log("暫存區沒有相簿")
    mark = lambda v: "✅" if v else "⬜"
    for r in rows:
        log(f"{r['name']:<32} {r['n']:>5} 檔  複製{mark(r['copy_done'])} 標籤{mark(r['tag_done'])} "
            f"上傳{mark(r['upload_done'])} NAS{mark(r['nas_done'])}  {r['share_url'] or ''}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--card", help="記憶卡掛載點（預設自動找 /Volumes 下有 DCIM 的）")
    ap.add_argument("--album", help="相簿名稱（英數、中文、. _ -），預設為拍攝日期")
    ap.add_argument("--title", help="返圖頁顯示的標題（預設同相簿名稱）")
    ap.add_argument("--resume", metavar="ALBUM", help="不讀卡，繼續處理暫存區裡的相簿")
    ap.add_argument("--list", action="store_true", help="列出暫存區相簿與進度")
    ap.add_argument("--no-eject", action="store_true", help="複製完不要自動退出記憶卡")
    ap.add_argument("--rating-overwrite", action="store_true", help="相機內已評的星等也用 AI 分數覆蓋")
    ap.add_argument("--retag", action="store_true", help="重新跑標籤（會一併重新上傳）")
    ap.add_argument("--skip-upload", action="store_true")
    ap.add_argument("--no-raw", action="store_true", help="這次不上傳 RAW 到返圖站（NAS 仍會備份 RAW）")
    ap.add_argument("--skip-nas", action="store_true")
    ap.add_argument("--cleanup", action="store_true", help="上傳與 NAS 都成功後刪除暫存區的這本相簿")
    g = ap.add_argument_group("連線設定（覆蓋 .env；後端安裝完會印出整串）")
    g.add_argument("--server", help="返圖站後端網址，例：http://192.168.1.50:8080")
    g.add_argument("--token", help="上傳 token")
    g.add_argument("--staging", help="暫存資料夾")
    g.add_argument("--nas", help="NAS 備份位置，例：smb://帳號@192.168.1.10/photo/返圖備份")
    g.add_argument("--save", action="store_true", help="把上面的連線設定存進 .env，之後直接 ./ingest.sh 即可")
    args = ap.parse_args()

    overrides = {k: v for k, v in (("SERVER_URL", args.server), ("UPLOAD_TOKEN", args.token),
                                   ("STAGING_DIR", args.staging), ("NAS_SMB", args.nas)) if v}
    ENV.update(overrides)
    if args.no_raw:
        ENV["UPLOAD_RAW"] = "0"
    if args.save and overrides:
        save_env(overrides)
        log(f"已存進 {ENV_FILE}：{', '.join(overrides)}")
    if args.nas and args.nas.startswith("smb://"):
        try:
            parse_smb(args.nas)  # 格式錯就在讀卡前先停
        except RuntimeError as e:
            sys.exit(str(e))

    staging = cfg_path("STAGING_DIR", "~/PhotoReturnStaging")
    con = open_db(staging)
    if args.list:
        return show_list(con)

    prism = load_prism()
    if args.resume:
        album = args.resume
        if not con.execute("SELECT 1 FROM albums WHERE name=?", (album,)).fetchone():
            sys.exit(f"暫存區沒有相簿 {album}（用 --list 查看）")
        if args.title:
            con.execute("UPDATE albums SET title=? WHERE name=?", (args.title, album))
            con.commit()
    else:
        card = Path(args.card) if args.card else wait_for_card()
        album = step_copy(con, staging, card, args.album, args.title, prism, eject=not args.no_eject)

    if args.retag:
        con.execute("UPDATE files SET tagged=0, uploaded=0 WHERE album=?", (album,))
        con.commit()

    step_tag(con, staging, album, prism, args)
    if not args.skip_upload:
        try:
            step_upload(con, staging, album)
        except requests.RequestException as e:
            log(f"  ⚠ 上傳失敗：{e}（可 --resume {album} 重試）")
    if not args.skip_nas:
        step_nas(con, staging, album)

    a = con.execute("SELECT * FROM albums WHERE name=?", (album,)).fetchone()
    all_ok = a["tag_done"] and a["upload_done"] and a["nas_done"]
    log("")
    if all_ok:
        notify(f"{album} 全部完成", "Hero")
        if args.cleanup:
            shutil.rmtree(staging / album)
            con.execute("DELETE FROM files WHERE album=?", (album,))
            con.commit()
            log("已清除暫存區")
        log(f"🎉 完成：{a['share_url'] or album}")
    else:
        notify(f"{album} 有步驟未完成，請看終端機", "Basso")
        log(f"⚠ 有步驟未完成，修正後執行：./ingest.sh --resume {album}")


if __name__ == "__main__":
    main()
