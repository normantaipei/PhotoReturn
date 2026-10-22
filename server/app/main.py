"""
PhotoReturn 返圖站 — 後端

- 上傳端（Mac 的 ingest 腳本）用 Bearer token、且只能從區網直連，建相簿、上傳 JPG 與 RAW
- 觀看端：
    /a/<相簿分享碼>   整本相簿，依 Prism 星等排序
    /p/<單張分享碼>   只看得到這一張（分享給別人不會曝光整本相簿）
    /f/<人物分享碼>   只看得到某一個人的照片（人臉分組在 Mac 端做，這裡只存分組結果，不存人臉特徵）
  兩種頁面都由後端注入 Open Graph 標籤，貼到 LINE / Telegram / Messenger / Discord 會有縮圖
- 原始檔以內容 SHA-256 存放；縮圖（webp）與預覽（jpg）在上傳時產生，社群預覽圖第一次被抓時產生
"""

import hashlib
import hmac
import html
import io
import ipaddress
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageCms, ImageEnhance, ImageFilter, ImageOps
from pydantic import BaseModel

DATA_DIR = Path(os.environ.get("PHOTORETURN_DATA", "./data")).expanduser().resolve()
UPLOAD_TOKEN = os.environ.get("PHOTORETURN_UPLOAD_TOKEN", "")
PUBLIC_URL = os.environ.get("PHOTORETURN_PUBLIC_URL", "").rstrip("/")
# 網站名稱：首頁標題、分頁標題後綴、通訊軟體預覽的網站名（安裝時設定）
SITE_NAME = os.environ.get("PHOTORETURN_SITE_NAME", "").strip() or "返圖"
# 允許上傳的來源網段（安裝時設成 LXC 所在的區網），預設只開私有網段
UPLOAD_NETS = [ipaddress.ip_network(n.strip(), strict=False) for n in os.environ.get(
    "PHOTORETURN_UPLOAD_NETS", "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128").split(",")
    if n.strip()]
WEB_DIR = Path(os.environ.get("PHOTORETURN_WEB", Path(__file__).resolve().parents[2] / "web" / "dist"))

THUMB_EDGE = 800      # 格狀列表用，長邊
FACE_EDGE = 160       # 人物頭像
PREVIEW_EDGE = 2048   # 燈箱大圖用，長邊
OG_SIZE = (1200, 630)  # 通訊軟體 / 社群預覽卡片：LINE、FB、Discord 都以 1.91:1 顯示，照這個比例做就不會被亂裁
OG_COLLAGE = 3         # 相簿預覽拼幾張（星等最高的前幾張）
OG_VERSION = "1"       # 改卡片樣式時遞增，網址跟著變，通訊軟體才會重抓

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SLUG_RE = re.compile(r"^[\w.\-]{1,100}$")
EXT_RE = re.compile(r"^\.[A-Za-z0-9]{1,5}$")

for sub in ("originals", "derived", "tmp"):
    (DATA_DIR / sub).mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "photoreturn.db"


# ---------- DB ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    shoot_date TEXT,
    share_key TEXT UNIQUE NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY,
    album_id INTEGER NOT NULL REFERENCES albums(id),
    photo_id TEXT NOT NULL,      -- 記憶卡原始檔的 SHA-256（重新標籤後內容會變，身分不變）
    sha256 TEXT NOT NULL,        -- 實際存放的 JPG 的 SHA-256
    filename TEXT NOT NULL,
    stars INTEGER,
    aesthetic REAL,
    tags TEXT NOT NULL DEFAULT '[]',
    taken_at TEXT,
    width INTEGER,
    height INTEGER,
    bytes INTEGER,
    uploaded_at REAL NOT NULL,
    UNIQUE (album_id, photo_id)
);
CREATE INDEX IF NOT EXISTS photos_album ON photos(album_id);
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    album_id INTEGER NOT NULL REFERENCES albums(id),
    share_key TEXT UNIQUE NOT NULL,
    label TEXT,                  -- 之後可命名；NULL 時前端顯示「人物 N」
    ord INTEGER NOT NULL,        -- 1 起算，張數多的在前
    cover_photo INTEGER REFERENCES photos(id),
    cover_box TEXT               -- 代表照上臉的位置 [x, y, w, h]（0~1，左上原點）
);
CREATE TABLE IF NOT EXISTS person_photos (
    person_id INTEGER NOT NULL REFERENCES people(id),
    photo_id INTEGER NOT NULL REFERENCES photos(id),
    PRIMARY KEY (person_id, photo_id)
);
CREATE INDEX IF NOT EXISTS people_album ON people(album_id);
"""

# 後來加的欄位：舊資料庫啟動時自動補上
MIGRATIONS = [
    "ALTER TABLE photos ADD COLUMN share_key TEXT",   # 單張分享碼
    "ALTER TABLE photos ADD COLUMN raw_sha TEXT",
    "ALTER TABLE photos ADD COLUMN raw_name TEXT",
    "ALTER TABLE photos ADD COLUMN raw_bytes INTEGER",
]


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def new_key() -> str:
    return secrets.token_urlsafe(9)


with db() as _con:
    _con.execute("PRAGMA journal_mode=WAL")
    _con.executescript(SCHEMA)
    _cols = {r["name"] for r in _con.execute("PRAGMA table_info(photos)")}
    for _sql in MIGRATIONS:
        if _sql.split()[5] not in _cols:
            _con.execute(_sql)
    _con.execute("CREATE UNIQUE INDEX IF NOT EXISTS photos_share ON photos(share_key)")
    for (_id,) in _con.execute("SELECT id FROM photos WHERE share_key IS NULL").fetchall():
        _con.execute("UPDATE photos SET share_key=? WHERE id=?", (new_key(), _id))


# ---------- 存檔路徑 ----------

def original_path(sha: str) -> Path:
    return DATA_DIR / "originals" / sha[:2] / f"{sha}.jpg"


def raw_path(sha: str, name: str) -> Path:
    return DATA_DIR / "originals" / sha[:2] / f"{sha}{Path(name).suffix.lower()}"


def thumb_path(sha: str) -> Path:
    return DATA_DIR / "derived" / sha[:2] / f"{sha}_t.webp"


def preview_path(sha: str) -> Path:
    return DATA_DIR / "derived" / sha[:2] / f"{sha}_p.jpg"


def make_derivatives(src: Path, sha: str):
    """產生縮圖與預覽，回傳轉正後的原圖寬高。"""
    t, p = thumb_path(sha), preview_path(sha)
    t.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        w, h = im.size
        if im.getexif().get(0x0112, 1) in (5, 6, 7, 8):  # 直拍：EXIF 轉 90°
            w, h = h, w
        if t.exists() and p.exists():
            return w, h
        icc = im.info.get("icc_profile")
        im.draft("RGB", (PREVIEW_EDGE, PREVIEW_EDGE))  # JPEG 直接以縮小尺寸解碼，快很多
        img = ImageOps.exif_transpose(im).convert("RGB")
    img.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE), Image.LANCZOS)
    img.save(p, "JPEG", quality=86, progressive=True, icc_profile=icc)
    img.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.LANCZOS)
    img.save(t, "WEBP", quality=80, icc_profile=icc)
    return w, h


# ---------- 通訊軟體預覽卡片（Open Graph） ----------

_SRGB = ImageCms.createProfile("sRGB")


def load_srgb(path: Path) -> Image.Image:
    """讀預覽圖並轉成 sRGB（相機設 Adobe RGB 時，通訊軟體不認 ICC 會顏色發灰）。"""
    with Image.open(path) as im:
        icc = im.info.get("icc_profile")
        img = im.convert("RGB")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            img = ImageCms.profileToProfile(img, src, _SRGB, outputMode="RGB")
        except (ImageCms.PyCMSError, OSError):
            pass
    return img


def _cover(img: Image.Image, size, centering=(0.5, 0.38)) -> Image.Image:
    """填滿 size 並裁掉多餘部分；垂直方向偏上裁，人像比較不會切到頭。"""
    return ImageOps.fit(img, size, Image.LANCZOS, centering=centering)


def og_card_single(sha: str) -> Image.Image:
    """單張：整張照片完整放中間，左右（或上下）用同一張模糊壓暗當背景，直幅也不會被裁頭。"""
    img = load_srgb(preview_path(sha))
    bg = _cover(img, OG_SIZE).filter(ImageFilter.GaussianBlur(28))
    bg = ImageEnhance.Brightness(bg).enhance(0.45)
    fg = img.copy()
    fg.thumbnail(OG_SIZE, Image.LANCZOS)
    bg.paste(fg, ((OG_SIZE[0] - fg.width) // 2, (OG_SIZE[1] - fg.height) // 2))
    return bg


def og_card_collage(rows) -> Image.Image:
    """相簿：最高分的幾張並排，每格寬度依照片比例分配（直幅窄、橫幅寬），盡量少裁。"""
    gap = 6
    W, H = OG_SIZE
    ars = [(r["width"] / r["height"]) if r["width"] and r["height"] else 1.5 for r in rows]
    usable = W - gap * (len(rows) - 1)
    widths = [round(usable * a / sum(ars)) for a in ars]
    widths[-1] = usable - sum(widths[:-1])
    card = Image.new("RGB", OG_SIZE, (17, 18, 20))
    x = 0
    for r, w in zip(rows, widths):
        card.paste(_cover(load_srgb(preview_path(r["sha256"])), (w, H)), (x, 0))
        x += w + gap
    return card


def og_name(shas) -> str:
    """卡片檔名 = 內容指紋：前幾名換了照片，網址就跟著換。"""
    return hashlib.sha256((OG_VERSION + ":" + ",".join(shas)).encode()).hexdigest()[:20]


def og_file(name: str, build) -> Path:
    path = DATA_DIR / "derived" / "og" / f"{name}.jpg"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        build().save(tmp, "JPEG", quality=85, optimize=True, progressive=True)
        os.replace(tmp, path)
    return path


# ---------- 權限 ----------

# 經過反向代理 / tunnel 轉進來的請求一定帶這些 header，上傳一律拒絕
PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "cf-connecting-ip", "true-client-ip")


def upload_source_error(request: Request) -> Optional[str]:
    """上傳端必須從允許的區網直連；不符合時回傳原因。"""
    try:
        ip = ipaddress.ip_address(request.client.host if request.client else "")
        ip = getattr(ip, "ipv4_mapped", None) or ip
    except ValueError:
        return "無法辨識來源 IP"
    if any(h in request.headers for h in PROXY_HEADERS) or not any(ip in n for n in UPLOAD_NETS):
        return f"上傳只允許從區網直連（來源 {ip}）"
    return None


def require_uploader(request: Request, authorization: Optional[str]):
    err = upload_source_error(request)  # middleware 已擋過，這裡再確認一次
    if err:
        raise HTTPException(403, err)
    if not UPLOAD_TOKEN:
        raise HTTPException(500, "伺服器未設定 PHOTORETURN_UPLOAD_TOKEN")
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given.encode(), UPLOAD_TOKEN.encode()):
        raise HTTPException(401, "token 錯誤")


def share_url(key: str) -> str:
    return f"{PUBLIC_URL}/a/{key}" if PUBLIC_URL else f"/a/{key}"


def album_by_slug(con, slug: str):
    row = con.execute("SELECT * FROM albums WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise HTTPException(404, "相簿不存在")
    return row


def album_by_key(con, key: str):
    row = con.execute("SELECT * FROM albums WHERE share_key=?", (key,)).fetchone()
    if not row:
        raise HTTPException(404, "相簿不存在")
    return row


def receive_file(file: UploadFile, expected_sha: Optional[str]):
    """寫暫存檔、邊寫邊算 hash；回傳 (sha256, 大小, 暫存檔路徑)。"""
    h = hashlib.sha256()
    size = 0
    with tempfile.NamedTemporaryFile(dir=DATA_DIR / "tmp", delete=False) as tmp:
        for chunk in iter(lambda: file.file.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
            tmp.write(chunk)
    tmp_path = Path(tmp.name)
    sha = h.hexdigest()
    if expected_sha and expected_sha != sha:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(400, "檔案傳輸不完整（SHA-256 不符）")
    return sha, size, tmp_path


def store(tmp_path: Path, dest: Path):
    """搬進內容定址的位置；同內容已存在就丟掉暫存檔。"""
    if dest.exists():
        tmp_path.unlink(missing_ok=True)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_path, dest)


def parse_meta(meta: str) -> dict:
    try:
        return json.loads(meta)
    except ValueError:
        raise HTTPException(400, "meta 不是 JSON")


# ---------- 上傳端 API ----------

app = FastAPI(title="PhotoReturn 返圖站", docs_url=None, redoc_url=None)


SECURITY_HEADERS = {
    # 分享碼就在網址裡：點出站連結時不要把網址送給對方
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    # 不讓別的網站把返圖頁嵌進 iframe
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                               "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                               "form-action 'none'; object-src 'none'",
    "X-Robots-Tag": "noindex, nofollow",
}


@app.middleware("http")
async def guard_upload_api(request: Request, call_next):
    """/api/albums/* 是上傳端 API：在讀取上傳內容之前就先擋掉非區網來源。所有回應加上安全標頭。"""
    if request.url.path.startswith("/api/albums"):
        err = upload_source_error(request)
        if err:
            return JSONResponse({"detail": err}, status_code=403)
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response


class AlbumIn(BaseModel):
    slug: str
    title: Optional[str] = None
    shoot_date: Optional[str] = None


@app.post("/api/albums")
def create_album(body: AlbumIn, request: Request, authorization: Optional[str] = Header(None)):
    """建立相簿；已存在就回傳原本的（可重複呼叫）。"""
    require_uploader(request, authorization)
    if not SLUG_RE.match(body.slug):
        raise HTTPException(400, "相簿名稱只能用文字、數字、. _ -")
    with db() as con:
        row = con.execute("SELECT * FROM albums WHERE slug=?", (body.slug,)).fetchone()
        if not row:
            con.execute(
                "INSERT INTO albums (slug, title, shoot_date, share_key, created_at) VALUES (?,?,?,?,?)",
                (body.slug, body.title or body.slug, body.shoot_date, new_key(), time.time()))
            row = con.execute("SELECT * FROM albums WHERE slug=?", (body.slug,)).fetchone()
        elif body.title and body.title != row["title"]:
            con.execute("UPDATE albums SET title=? WHERE id=?", (body.title, row["id"]))
    return {"slug": row["slug"], "share_key": row["share_key"], "share_url": share_url(row["share_key"])}


@app.get("/api/albums/{slug}/photo-ids")
def album_photo_ids(slug: str, request: Request, authorization: Optional[str] = Header(None)):
    """已上傳過的 photo_id（以及其中已有 RAW 的），讓 ingest 續傳時跳過。"""
    require_uploader(request, authorization)
    with db() as con:
        album = album_by_slug(con, slug)
        rows = con.execute("SELECT photo_id, raw_sha FROM photos WHERE album_id=?", (album["id"],)).fetchall()
    return {"photo_ids": [r["photo_id"] for r in rows],
            "with_raw": [r["photo_id"] for r in rows if r["raw_sha"]]}


@app.put("/api/albums/{slug}/photos")
def upload_photo(slug: str, request: Request, file: UploadFile = File(...), meta: str = Form(...),
                 authorization: Optional[str] = Header(None)):
    require_uploader(request, authorization)
    m = parse_meta(meta)
    photo_id = str(m.get("photo_id", ""))
    if not SHA_RE.match(photo_id):
        raise HTTPException(400, "photo_id 必須是 SHA-256")
    with db() as con:
        album = album_by_slug(con, slug)

    sha, size, tmp_path = receive_file(file, m.get("sha256"))
    dest = original_path(sha)
    store(tmp_path, dest)
    try:
        w, hgt = make_derivatives(dest, sha)
    except Exception as e:
        raise HTTPException(400, f"無法讀取圖片：{e}")

    stars = m.get("stars")
    tags = m.get("tags") or []
    with db() as con:
        con.execute(
            """INSERT INTO photos (album_id, photo_id, sha256, filename, stars, aesthetic, tags,
                                   taken_at, width, height, bytes, uploaded_at, share_key)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(album_id, photo_id) DO UPDATE SET
                 sha256=excluded.sha256, filename=excluded.filename, stars=excluded.stars,
                 aesthetic=excluded.aesthetic, tags=excluded.tags, taken_at=excluded.taken_at,
                 width=excluded.width, height=excluded.height, bytes=excluded.bytes,
                 uploaded_at=excluded.uploaded_at""",
            (album["id"], photo_id, sha, str(m.get("filename") or f"{sha[:12]}.jpg"),
             int(stars) if stars is not None else None, m.get("aesthetic"),
             json.dumps(tags, ensure_ascii=False), m.get("taken_at"), w, hgt, size, time.time(), new_key()))
    return {"sha256": sha, "width": w, "height": hgt}


@app.put("/api/albums/{slug}/photos/{photo_id}/raw")
def upload_raw(slug: str, photo_id: str, request: Request, file: UploadFile = File(...),
               meta: str = Form(...), authorization: Optional[str] = Header(None)):
    """上傳某張照片對應的 RAW（要先上傳過 JPG）。"""
    require_uploader(request, authorization)
    m = parse_meta(meta)
    name = Path(str(m.get("filename") or "")).name
    if not EXT_RE.match(Path(name).suffix):
        raise HTTPException(400, "RAW 檔名需要副檔名")
    with db() as con:
        album = album_by_slug(con, slug)
        if not con.execute("SELECT 1 FROM photos WHERE album_id=? AND photo_id=?",
                           (album["id"], photo_id)).fetchone():
            raise HTTPException(404, "請先上傳這張的 JPG")

    sha, size, tmp_path = receive_file(file, m.get("sha256"))
    store(tmp_path, raw_path(sha, name))
    with db() as con:
        con.execute("UPDATE photos SET raw_sha=?, raw_name=?, raw_bytes=? WHERE album_id=? AND photo_id=?",
                    (sha, name, size, album["id"], photo_id))
    return {"sha256": sha, "bytes": size}


class PersonCover(BaseModel):
    photo_id: str
    box: list[float]


class PersonIn(BaseModel):
    photos: list[str]
    cover: Optional[PersonCover] = None


class PeopleIn(BaseModel):
    people: list[PersonIn]


@app.put("/api/albums/{slug}/people")
def upload_people(slug: str, body: PeopleIn, request: Request, authorization: Optional[str] = Header(None)):
    """整本相簿的人物分組（Mac 端每次重新分群後整包送來）。
    分享碼與名稱沿用照片重疊最多的舊分組，已經發出去的人物連結不會因為多插一張卡就失效。"""
    require_uploader(request, authorization)
    with db() as con:
        album = album_by_slug(con, slug)
        ids = {r["photo_id"]: r["id"] for r in con.execute(
            "SELECT id, photo_id FROM photos WHERE album_id=?", (album["id"],))}
        old = {}
        for r in con.execute("SELECT p.id, pp.photo_id FROM people p LEFT JOIN person_photos pp ON pp.person_id=p.id "
                             "WHERE p.album_id=?", (album["id"],)):
            old.setdefault(r["id"], set()).add(r["photo_id"])
        used, kept = set(), []
        for n, person in enumerate(body.people, 1):
            photos = {ids[x] for x in person.photos if x in ids}
            if not photos:
                continue
            best, best_j = None, 0.3  # 重疊比例（Jaccard）至少 0.3 才算同一個人
            for pid, ps in old.items():
                j = len(ps & photos) / len(ps | photos) if ps else 0
                if pid not in used and j >= best_j:
                    best, best_j = pid, j
            cover = person.cover
            cover_photo = ids.get(cover.photo_id) if cover else None
            box = json.dumps([min(max(float(v), 0.0), 1.0) for v in cover.box[:4]]) \
                if cover_photo and len(cover.box) >= 4 else None
            if best is None:
                cur = con.execute("INSERT INTO people (album_id, share_key, ord, cover_photo, cover_box) "
                                  "VALUES (?,?,?,?,?)", (album["id"], new_key(), n, cover_photo, box))
                best = cur.lastrowid
            else:
                con.execute("UPDATE people SET ord=?, cover_photo=?, cover_box=? WHERE id=?",
                            (n, cover_photo, box, best))
            used.add(best)
            kept.append(best)
            con.execute("DELETE FROM person_photos WHERE person_id=?", (best,))
            con.executemany("INSERT INTO person_photos (person_id, photo_id) VALUES (?,?)",
                            [(best, x) for x in photos])
        for pid in set(old) - used:
            con.execute("DELETE FROM person_photos WHERE person_id=?", (pid,))
            con.execute("DELETE FROM people WHERE id=?", (pid,))
        for n, pid in enumerate(kept, 1):  # 跳過空分組後重新編號
            con.execute("UPDATE people SET ord=? WHERE id=?", (n, pid))
    return {"people": len(kept)}


# ---------- 觀看端 API ----------

ORDER = "ORDER BY stars IS NULL, stars DESC, aesthetic IS NULL, aesthetic DESC, taken_at, filename"


def photo_json(r) -> dict:
    return {
        "sha": r["sha256"], "pk": r["share_key"], "name": r["filename"], "stars": r["stars"],
        "aesthetic": r["aesthetic"], "tags": json.loads(r["tags"]),
        "taken_at": r["taken_at"], "w": r["width"], "h": r["height"], "bytes": r["bytes"],
        "raw_name": r["raw_name"] if r["raw_sha"] else None,
        "raw_bytes": r["raw_bytes"] if r["raw_sha"] else None,
    }


def album_photos(con, album_id: int):
    return con.execute(f"SELECT * FROM photos WHERE album_id=? {ORDER}", (album_id,)).fetchall()


def face_name(r) -> str:
    """頭像檔名 = 代表照 + 臉的位置的指紋：換了代表照網址就跟著換。"""
    return hashlib.sha256(f"{r['cover_sha']}:{r['cover_box']}".encode()).hexdigest()[:16]


def person_json(r, count: int) -> dict:
    return {"key": r["share_key"], "n": r["ord"], "label": r["label"], "count": count,
            "face": f"/face/{r['share_key']}/{face_name(r)}.jpg" if r["cover_sha"] else None}


PEOPLE_SQL = """SELECT pe.*, ph.sha256 AS cover_sha FROM people pe
                LEFT JOIN photos ph ON ph.id=pe.cover_photo"""


@app.get("/api/share/{key}")
def share_album(key: str):
    with db() as con:
        album = album_by_key(con, key)
        rows = album_photos(con, album["id"])
        people = con.execute(f"{PEOPLE_SQL} WHERE pe.album_id=? ORDER BY pe.ord", (album["id"],)).fetchall()
        links = con.execute("SELECT pp.person_id, pp.photo_id FROM person_photos pp JOIN people pe "
                            "ON pe.id=pp.person_id WHERE pe.album_id=?", (album["id"],)).fetchall()
    key_of = {p["id"]: p["share_key"] for p in people}
    of_photo, count = {}, {}
    for l in links:
        of_photo.setdefault(l["photo_id"], []).append(key_of[l["person_id"]])
        count[l["person_id"]] = count.get(l["person_id"], 0) + 1
    photos = [{**photo_json(r), "people": of_photo.get(r["id"], [])} for r in rows]
    return {"title": album["title"], "shoot_date": album["shoot_date"], "photos": photos,
            "people": [person_json(p, count.get(p["id"], 0)) for p in people]}


def person_by_key(con, key: str):
    row = con.execute(f"""{PEOPLE_SQL.replace("SELECT pe.*", "SELECT pe.*, a.title AS album_title, a.shoot_date")}
                          JOIN albums a ON a.id=pe.album_id WHERE pe.share_key=?""", (key,)).fetchone()
    if not row:
        raise HTTPException(404, "人物不存在")
    return row


def person_photos(con, person_id: int):
    return con.execute(f"""SELECT p.* FROM photos p JOIN person_photos pp ON pp.photo_id=p.id
                           WHERE pp.person_id=? {ORDER}""",
                       (person_id,)).fetchall()


@app.get("/api/person/{key}")
def share_person(key: str):
    """人物分享：只回這個人的照片，不帶相簿分享碼，也不帶其他人物的分享碼。"""
    with db() as con:
        person = person_by_key(con, key)
        rows = person_photos(con, person["id"])
    return {"title": person["album_title"], "shoot_date": person["shoot_date"],
            "person": person_json(person, len(rows)), "photos": [photo_json(r) for r in rows]}


def photo_by_key(con, pk: str):
    row = con.execute(
        """SELECT p.*, a.title AS album_title, a.shoot_date FROM photos p
           JOIN albums a ON a.id=p.album_id WHERE p.share_key=?""", (pk,)).fetchone()
    if not row:
        raise HTTPException(404, "照片不存在")
    return row


@app.get("/api/photo/{pk}")
def share_photo(pk: str):
    """單張分享：只回這一張，不帶相簿分享碼。"""
    with db() as con:
        r = photo_by_key(con, pk)
    return {**photo_json(r), "album_title": r["album_title"], "shoot_date": r["shoot_date"]}


IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}  # 網址含內容 sha，永不改變
SHORT = {"Cache-Control": "public, max-age=3600"}  # /m/<單張碼>：重新上傳後內容會變


def serve(row, kind: str, cache: dict = IMMUTABLE):
    sha = row["sha256"]
    if kind == "t":
        return FileResponse(thumb_path(sha), media_type="image/webp", headers=cache)
    if kind == "p":
        return FileResponse(preview_path(sha), media_type="image/jpeg", headers=cache)
    if kind == "o":
        return FileResponse(original_path(sha), media_type="image/jpeg", filename=row["filename"],
                            headers=cache)
    if kind == "r" and row["raw_sha"]:
        return FileResponse(raw_path(row["raw_sha"], row["raw_name"]), media_type="application/octet-stream",
                            filename=row["raw_name"], headers=cache)
    raise HTTPException(404)


@app.api_route("/media/{key}/{kind}/{sha}", methods=["GET", "HEAD"])
def album_media(key: str, kind: str, sha: str):
    if not SHA_RE.match(sha):
        raise HTTPException(404)
    with db() as con:
        row = con.execute(
            "SELECT p.* FROM photos p JOIN albums a ON a.id=p.album_id WHERE a.share_key=? AND p.sha256=?",
            (key, sha)).fetchone()
    if not row:
        raise HTTPException(404)
    return serve(row, kind)


@app.api_route("/fm/{key}/{kind}/{sha}", methods=["GET", "HEAD"])
def person_media(key: str, kind: str, sha: str):
    """人物頁的圖片：只給得到屬於這個人的照片。"""
    if not SHA_RE.match(sha):
        raise HTTPException(404)
    with db() as con:
        row = con.execute("""SELECT p.* FROM photos p JOIN person_photos pp ON pp.photo_id=p.id
                             JOIN people pe ON pe.id=pp.person_id WHERE pe.share_key=? AND p.sha256=?""",
                          (key, sha)).fetchone()
    if not row:
        raise HTTPException(404)
    return serve(row, kind)


def face_crop(sha: str, box) -> Image.Image:
    """從預覽圖裁出正方形頭像：以臉為中心、往外留一些頭髮與下巴。"""
    img = load_srgb(preview_path(sha))
    W, H = img.size
    x, y, w, h = box
    cx, cy = (x + w / 2) * W, (y + h / 2) * H
    side = min(max(w * W, h * H) * 1.8, W, H)
    left = min(max(cx - side / 2, 0), W - side)
    top = min(max(cy - side / 2, 0), H - side)
    return img.crop((round(left), round(top), round(left + side), round(top + side))).resize(
        (FACE_EDGE, FACE_EDGE), Image.LANCZOS)


@app.api_route("/face/{key}/{name}.jpg", methods=["GET", "HEAD"])
def person_face(key: str, name: str):
    with db() as con:
        r = person_by_key(con, key)
    if not r["cover_sha"] or name != face_name(r):
        raise HTTPException(404)
    path = DATA_DIR / "derived" / "faces" / f"{name}.jpg"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        face_crop(r["cover_sha"], json.loads(r["cover_box"])).save(tmp, "JPEG", quality=85)
        os.replace(tmp, path)
    return FileResponse(path, media_type="image/jpeg", headers=IMMUTABLE)


@app.api_route("/m/{pk}/{kind}", methods=["GET", "HEAD"])
def photo_media(pk: str, kind: str):
    with db() as con:
        row = photo_by_key(con, pk)
    return serve(row, kind, SHORT)


class _Sink:
    """給 zipfile 寫的不可 seek 串流，寫進來的 bytes 由產生器取走。"""

    def __init__(self):
        self.buf = bytearray()

    def write(self, b):
        self.buf += b
        return len(b)

    def flush(self):
        pass


def _zip_stream(items):
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED) as zf:  # JPEG / RAW 都已壓縮，不再壓
        for arcname, path in items:
            info = zipfile.ZipInfo.from_file(path, arcname)  # 保留檔案時間
            with open(path, "rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    dst.write(chunk)
                    if len(sink.buf) >= (1 << 20):
                        yield bytes(sink.buf)
                        sink.buf.clear()
    yield bytes(sink.buf)


def zip_response(title: str, rows, min_stars: int, fmt: str):
    """fmt：jpg / raw / all（JPG + RAW）。沒有 RAW 的照片在 raw 模式下略過。"""
    if fmt not in ("jpg", "raw", "all"):
        raise HTTPException(400, "fmt 只能是 jpg / raw / all")
    rows = [r for r in rows if (r["stars"] or 0) >= min_stars]

    used, items = set(), []

    def add(name, path, sha):
        if name in used:  # 兩台同型號相機可能撞名
            stem, dot, ext = name.rpartition(".")
            name = f"{stem}_{sha[:6]}.{ext}" if dot else f"{name}_{sha[:6]}"
        used.add(name)
        items.append((name, path))

    for r in rows:
        if fmt in ("jpg", "all"):
            add(r["filename"], original_path(r["sha256"]), r["sha256"])
        if fmt in ("raw", "all") and r["raw_sha"]:
            add(r["raw_name"], raw_path(r["raw_sha"], r["raw_name"]), r["raw_sha"])
    if not items:
        raise HTTPException(404, "沒有符合條件的檔案")
    label = {"jpg": "", "raw": "_RAW", "all": "_JPG+RAW"}[fmt]
    zipname = title + (f"_{min_stars}star" if min_stars else "") + label + ".zip"
    return StreamingResponse(
        _zip_stream(items), media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(zipname)}"})


@app.get("/api/share/{key}/zip")
def share_zip(key: str, min_stars: int = 0, fmt: str = "jpg"):
    with db() as con:
        album = album_by_key(con, key)
        rows = album_photos(con, album["id"])
    return zip_response(album["title"], rows, min_stars, fmt)


@app.get("/api/person/{key}/zip")
def person_zip(key: str, min_stars: int = 0, fmt: str = "jpg"):
    with db() as con:
        person = person_by_key(con, key)
        rows = person_photos(con, person["id"])
    return zip_response(f"{person['album_title']}_{person['label'] or person['ord']}", rows, min_stars, fmt)


# ---------- 前端（web/ 的 Vue build 產物）＋ Open Graph ----------
# 頁面與圖片路由都接受 HEAD：有些通訊軟體的爬蟲會先送 HEAD 探路，回 405 就直接放棄抓預覽

HOST_RE = re.compile(r"^[A-Za-z0-9.\-]{1,253}(:\d{1,5})?$")


def base_url(request: Request) -> str:
    """og:image 必須是外面連得到的絕對網址。
    有設定 PHOTORETURN_PUBLIC_URL 就用它；沒有就用這次請求的網域：經反向代理時取
    X-Forwarded-Host / X-Forwarded-Proto（NPM、Caddy、Cloudflare Tunnel 預設都會帶），
    所以 https 網域不用另外設定。只影響這次回應裡的網址，不涉及權限判斷。"""
    if PUBLIC_URL:
        return PUBLIC_URL
    h = request.headers
    host = (h.get("x-forwarded-host") or h.get("host") or "").split(",")[0].strip()
    proto = (h.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if proto not in ("http", "https"):
        proto = request.url.scheme
    if not HOST_RE.match(host):
        return str(request.base_url).rstrip("/")
    return f"{proto}://{host}"


# 分享卡片與 <html lang> 的語言：?lang= → Accept-Language → 中文（前端之後還會依使用者選擇切換）
OG_TEXT = {
    "zh": {"html": "zh-Hant", "album_nf": "找不到相簿", "photo_nf": "找不到照片",
           "count": "{n} 張照片", "single": "單張照片", "person_nf": "找不到人物"},
    "en": {"html": "en", "album_nf": "Album not found", "photo_nf": "Photo not found",
           "count": "{n} photos", "single": "Single photo", "person_nf": "Person not found"},
    "ja": {"html": "ja", "album_nf": "アルバムが見つかりません", "photo_nf": "写真が見つかりません",
           "count": "{n} 枚の写真", "single": "写真 1 枚", "person_nf": "人物が見つかりません"},
}


def req_lang(request: Request) -> str:
    q = (request.query_params.get("lang") or "").lower()[:2]
    if q in OG_TEXT:
        return q
    for part in request.headers.get("accept-language", "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in OG_TEXT:
            return code
    return "zh"


def spa(request: Request, title: Optional[str] = None, description: str = "", image: Optional[str] = None,
        size=None, path: str = ""):
    index = WEB_DIR / "index.html"
    if not index.exists():
        raise HTTPException(500, f"找不到前端：{WEB_DIR}（先在 web/ 執行 npm run build）")
    base = base_url(request)
    e = lambda s: html.escape(s, quote=True)
    title = title or SITE_NAME
    tags = [
        f'<meta property="og:site_name" content="{e(SITE_NAME)}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:title" content="{e(title)}">',
        f'<meta property="og:url" content="{e(base + path)}">',
    ]
    if description:
        tags += [f'<meta property="og:description" content="{e(description)}">',
                 f'<meta name="description" content="{e(description)}">']
    if image:
        tags += [f'<meta property="og:image" content="{e(base + image)}">',
                 '<meta property="og:image:type" content="image/jpeg">',
                 '<meta name="twitter:card" content="summary_large_image">',
                 f'<meta name="twitter:image" content="{e(base + image)}">']
        if size:
            tags += [f'<meta property="og:image:width" content="{size[0]}">',
                     f'<meta property="og:image:height" content="{size[1]}">']
    page = (index.read_text(encoding="utf-8")
            .replace('<html lang="zh-Hant">', f'<html lang="{OG_TEXT[req_lang(request)]["html"]}">', 1)
            .replace('<meta name="site-name" content="返圖">', f'<meta name="site-name" content="{e(SITE_NAME)}">', 1)
            .replace("<title>返圖</title>", f"<title>{e(title)}</title>\n  " + "\n  ".join(tags), 1))
    return HTMLResponse(page, headers={"Cache-Control": "no-cache", "Vary": "Accept-Language"})


def date_text(d: Optional[str]) -> str:
    return d.replace("-", "/") if d else ""


def album_top(con, album_id: int):
    return album_photos(con, album_id)[:OG_COLLAGE]


@app.api_route("/og/a/{key}/{name}.jpg", methods=["GET", "HEAD"])
def album_og(key: str, name: str):
    with db() as con:
        album = album_by_key(con, key)
        top = album_top(con, album["id"])
    if not top or name != og_name([r["sha256"] for r in top]):
        raise HTTPException(404)
    return FileResponse(og_file(name, lambda: og_card_collage(top)), media_type="image/jpeg", headers=IMMUTABLE)


@app.api_route("/og/p/{pk}/{name}.jpg", methods=["GET", "HEAD"])
def photo_og(pk: str, name: str):
    with db() as con:
        r = photo_by_key(con, pk)
    if name != og_name([r["sha256"]]):
        raise HTTPException(404)
    return FileResponse(og_file(name, lambda: og_card_single(r["sha256"])), media_type="image/jpeg",
                        headers=IMMUTABLE)


@app.api_route("/a/{key}", methods=["GET", "HEAD"])
def album_page(key: str, request: Request):
    with db() as con:
        album = con.execute("SELECT * FROM albums WHERE share_key=?", (key,)).fetchone()
        top = album_top(con, album["id"]) if album else []
        count = con.execute("SELECT COUNT(*) FROM photos WHERE album_id=?", (album["id"],)).fetchone()[0] \
            if album else 0
    tx = OG_TEXT[req_lang(request)]
    if not album:
        return spa(request, tx["album_nf"], path=f"/a/{key}")
    desc = " · ".join(x for x in (date_text(album["shoot_date"]), tx["count"].format(n=count)) if x)
    img = f"/og/a/{key}/{og_name([r['sha256'] for r in top])}.jpg" if top else None
    return spa(request, album["title"], desc, img, OG_SIZE if top else None, f"/a/{key}")


@app.api_route("/og/f/{key}/{name}.jpg", methods=["GET", "HEAD"])
def person_og(key: str, name: str):
    with db() as con:
        top = person_photos(con, person_by_key(con, key)["id"])[:OG_COLLAGE]
    if not top or name != og_name([r["sha256"] for r in top]):
        raise HTTPException(404)
    return FileResponse(og_file(name, lambda: og_card_collage(top)), media_type="image/jpeg", headers=IMMUTABLE)


@app.api_route("/f/{key}", methods=["GET", "HEAD"])
def person_page(key: str, request: Request):
    tx = OG_TEXT[req_lang(request)]
    with db() as con:
        try:
            person = person_by_key(con, key)
        except HTTPException:
            return spa(request, tx["person_nf"], path=f"/f/{key}")
        rows = person_photos(con, person["id"])
    top = rows[:OG_COLLAGE]
    desc = " · ".join(x for x in (date_text(person["shoot_date"]), tx["count"].format(n=len(rows))) if x)
    img = f"/og/f/{key}/{og_name([r['sha256'] for r in top])}.jpg" if top else None
    return spa(request, person["album_title"], desc, img, OG_SIZE if top else None, f"/f/{key}")


@app.api_route("/p/{pk}", methods=["GET", "HEAD"])
def photo_page(pk: str, request: Request):
    tx = OG_TEXT[req_lang(request)]
    with db() as con:
        try:
            r = photo_by_key(con, pk)
        except HTTPException:
            return spa(request, tx["photo_nf"], path=f"/p/{pk}")
    stars = "★" * r["stars"] if r["stars"] else ""
    desc = " · ".join(x for x in (date_text(r["shoot_date"]), tx["single"], stars) if x)
    return spa(request, r["album_title"], desc, f"/og/p/{pk}/{og_name([r['sha256']])}.jpg", OG_SIZE, f"/p/{pk}")


@app.api_route("/", methods=["GET", "HEAD"])
def home(request: Request):
    return spa(request)


@app.api_route("/favicon.svg", methods=["GET", "HEAD"])
def favicon():
    return FileResponse(WEB_DIR / "favicon.svg")


@app.api_route("/healthz", methods=["GET", "HEAD"])
def healthz():
    return {"ok": True, "free_gb": round(shutil.disk_usage(DATA_DIR).free / 1e9, 1)}


if (WEB_DIR / "assets").is_dir():
    # vite 產出的檔名含 hash，可長期快取
    app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")
