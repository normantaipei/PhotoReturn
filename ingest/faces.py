"""
人臉分組（在 Mac 本機執行，人臉特徵不會離開這台 Mac）

  1. 偵測：macOS Vision 找臉、五官位置與臉部品質（VNDetectFaceLandmarksRequest / CaptureQuality）
  2. 特徵：依五官做相似變換對齊成 112×112，丟進 ArcFace（InsightFace w600k_r50，ONNX）得到 512 維特徵
  3. 分群：整本相簿的臉做 average-linkage 階層分群（cosine 距離），出現張數太少的群（路人）不算
  4. 補上沒拍到臉的照片（cosplay 常戴墨鏡、側臉、背影、遠景）：
     - 服裝：Vision 找出照片裡的人（VNDetectHumanRectanglesRequest），對每個人算外觀特徵
       （VNGenerateImageFeaturePrintRequest）；臉落在身體範圍裡的當「已知是誰」，
       其他人跟各人物的已知外觀比對，夠像而且明顯比第二像的人物更像才歸過去
     - 時間：前後不久都拍到同一個人、中間的照片（暗場、細節、偵測不到人的）也歸給他

模型授權：InsightFace 預訓練模型僅限非商業用途（https://github.com/deepinsight/insightface#license）。
"""

import bisect
import hashlib
import io
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import objc
import Vision
from Foundation import NSData
from PIL import Image, ImageOps

MODEL_ZIP_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
MODEL_ZIP_SHA = "80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f"
MODEL_NAME = "w600k_r50.onnx"
MODEL_SHA = "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43"

DETECT_EDGE = 2048  # 偵測用的長邊；記憶卡原圖太大，縮小後 Vision 快很多，臉的細節也夠
# 五官點對不上樣板（手遮臉、暗場時 Vision 會給出亂掉的五官）：對齊誤差太大或兩眼距離太窄的臉不採用
MAX_ALIGN_ERROR = 12.0   # 112×112 上五點的平均誤差（px）；正常的臉 < 9
MIN_EYE_RATIO = 0.18     # 兩眼距離 / 臉框寬；正常的臉 > 0.2

# ArcFace 對齊樣板（112×112 上的左眼、右眼、鼻尖、左嘴角、右嘴角）
ARCFACE_DST = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                        [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float64)


# ---------- 模型 ----------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_model(path: Path, log=print) -> Path:
    """模型不在就下載 InsightFace 的 buffalo_l（約 290 MB），只取出辨識用的 w600k_r50.onnx，並核對 SHA-256。"""
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    log(f"  下載人臉辨識模型（約 290 MB，只需一次）→ {path}")
    log("  ※ InsightFace 預訓練模型僅限非商業用途")
    with tempfile.TemporaryDirectory() as tmp:
        z = Path(tmp) / "buffalo_l.zip"
        urllib.request.urlretrieve(MODEL_ZIP_URL, z)
        if _sha256(z) != MODEL_ZIP_SHA:
            raise RuntimeError("模型下載不完整或被竄改（SHA-256 不符）")
        with zipfile.ZipFile(z) as zf, zf.open(MODEL_NAME) as src, open(path.with_suffix(".part"), "wb") as dst:
            shutil.copyfileobj(src, dst)
    part = path.with_suffix(".part")
    if _sha256(part) != MODEL_SHA:
        part.unlink()
        raise RuntimeError("模型檔 SHA-256 不符")
    part.replace(path)
    return path


class Embedder:
    def __init__(self, model: Path):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        # CoreML 在 Apple Silicon 上會用到 GPU / Neural Engine；不支援時自動退回 CPU
        prov = [p for p in ("CoreMLExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]
        self.sess = ort.InferenceSession(str(model), opts, providers=prov)
        self.input = self.sess.get_inputs()[0].name

    def __call__(self, crops: list) -> np.ndarray:
        """crops：112×112 RGB 的 PIL 圖 → (N, 512) 已正規化的特徵。"""
        out = []
        for c in crops:
            x = (np.asarray(c, dtype=np.float32) - 127.5) / 127.5
            x = x.transpose(2, 0, 1)[None]
            # 左右翻轉的特徵一起平均，對側臉、表情比較穩
            e = self.sess.run(None, {self.input: x})[0][0] + \
                self.sess.run(None, {self.input: x[:, :, :, ::-1].copy()})[0][0]
            out.append(e / np.linalg.norm(e))
        return np.array(out, dtype=np.float32).reshape(-1, 512)


# ---------- 偵測與對齊 ----------

def _points(region, size):
    """Vision 的五官點（左下原點）→ 左上原點的 (N, 2) 陣列。"""
    n = region.pointCount()
    pts = region.pointsInImageOfSize_(size)
    return np.array([(pts[i].x, size[1] - pts[i].y) for i in range(n)], dtype=np.float64)


def _five_points(lm, size):
    eyes = sorted([_points(lm.leftEye(), size).mean(0), _points(lm.rightEye(), size).mean(0)], key=lambda p: p[0])
    nose = _points(lm.noseCrest(), size)
    nose_tip = nose[np.argmax(nose[:, 1])]  # 鼻樑最下面那一點 ≈ 鼻尖
    lips = _points(lm.outerLips(), size)
    mouth_l, mouth_r = lips[np.argmin(lips[:, 0])], lips[np.argmax(lips[:, 0])]
    return np.array([eyes[0], eyes[1], nose_tip, mouth_l, mouth_r])


def _similarity(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Umeyama：src → dst 的相似變換（旋轉 + 等比縮放 + 平移），回傳 2×3 矩陣。"""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    s, d = src - mu_s, dst - mu_d
    u, sig, vt = np.linalg.svd(d.T @ s / len(src))
    e = np.diag([1.0, np.sign(np.linalg.det(u @ vt))])
    r = u @ e @ vt
    scale = (sig * np.diag(e)).sum() / s.var(0).sum()
    m = np.zeros((2, 3))
    m[:, :2] = scale * r
    m[:, 2] = mu_d - scale * r @ mu_s
    return m


def _align(img: Image.Image, five: np.ndarray):
    """回傳 (112×112 對齊後的臉, 五點對齊誤差)。"""
    m = _similarity(five, ARCFACE_DST)
    err = float(np.sqrt((((m[:, :2] @ five.T).T + m[:, 2] - ARCFACE_DST) ** 2).sum(1)).mean())
    inv = np.linalg.inv(np.vstack([m, [0, 0, 1]]))[:2]  # PIL 要的是輸出 → 輸入
    return img.transform((112, 112), Image.AFFINE, inv.flatten().tolist(), Image.BILINEAR), err


def _feature_print(handler, box):
    req = Vision.VNGenerateImageFeaturePrintRequest.alloc().init()
    req.setRegionOfInterest_(box)
    if not handler.performRequests_error_([req], None)[0] or not req.results():
        return None
    obs = req.results()[0]
    v = np.frombuffer(bytes(obs.data()), dtype=np.float32 if obs.elementType() == 1 else np.float64)
    v = v.astype(np.float32)
    return v / (np.linalg.norm(v) or 1)


def detect(path: Path, min_px: int):
    """回傳 (faces, bodies)：
    faces  = [(box, quality, aligned_crop)]
    bodies = [(box, feature)]：照片裡的每個人（全身或半身），feature 是 Vision 的外觀特徵
    box = (x, y, w, h)，相對於轉正後整張圖、左上原點、0~1。"""
    with Image.open(path) as im:
        im.draft("RGB", (DETECT_EDGE, DETECT_EDGE))
        img = ImageOps.exif_transpose(im).convert("RGB")
    img.thumbnail((DETECT_EDGE, DETECT_EDGE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    data = buf.getvalue()
    W, H = img.size

    found, bodies = [], []
    to_box = lambda bb: (max(0.0, bb.origin.x), max(0.0, 1 - bb.origin.y - bb.size.height),
                         min(bb.size.width, 1.0), min(bb.size.height, 1.0))
    with objc.autorelease_pool():  # 長迴圈裡 pyobjc 不會自動釋放 ObjC 物件
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
            NSData.dataWithBytes_length_(data, len(data)), None)
        lm_req = Vision.VNDetectFaceLandmarksRequest.alloc().init()
        ok, err = handler.performRequests_error_([lm_req], None)
        if not ok:
            raise RuntimeError(str(err))
        faces = list(lm_req.results() or [])
        quality = [None] * len(faces)
        if faces:
            q_req = Vision.VNDetectFaceCaptureQualityRequest.alloc().init()
            q_req.setInputFaceObservations_(faces)
            if handler.performRequests_error_([q_req], None)[0]:
                for i, q in enumerate(q_req.results() or []):
                    quality[i] = float(q.faceCaptureQuality())
        for f, q in zip(faces, quality):
            bb = f.boundingBox()
            x, y, w, h = bb.origin.x, bb.origin.y, bb.size.width, bb.size.height
            lm = f.landmarks()
            if w * W < min_px or lm is None:
                continue
            try:
                five = _five_points(lm, (W, H))
            except Exception:
                continue
            crop, err = _align(img, five)
            if err > MAX_ALIGN_ERROR or np.linalg.norm(five[1] - five[0]) < MIN_EYE_RATIO * w * W:
                continue
            found.append((to_box(bb), q, crop))

        human = Vision.VNDetectHumanRectanglesRequest.alloc().init()
        human.setUpperBodyOnly_(False)
        if handler.performRequests_error_([human], None)[0]:
            for o in human.results() or []:
                v = _feature_print(handler, o.boundingBox())
                if v is not None:
                    bodies.append((to_box(o.boundingBox()), v))
        del handler, lm_req, faces
    return found, bodies


# ---------- 分群 ----------

def cluster(emb: np.ndarray, threshold: float) -> np.ndarray:
    """average-linkage 階層分群，cosine 相似度 >= threshold 的歸同一群；回傳每張臉的群編號（0 起算）。"""
    n = len(emb)
    if n == 0:
        return np.zeros(0, dtype=int)
    if n == 1:
        return np.zeros(1, dtype=int)
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import pdist
    z = linkage(pdist(emb, "cosine"), method="average")
    return fcluster(z, t=1 - threshold, criterion="distance") - 1


def _face_in_body(face, body) -> bool:
    """臉的中心落在身體框的上 60%。"""
    cx, cy = face[0] + face[2] / 2, face[1] + face[3] / 2
    return body[0] <= cx <= body[0] + body[2] and body[1] <= cy <= body[1] + body[3] * 0.6


def assign(photos: dict, body_threshold: float, body_margin: float, time_window: float) -> dict:
    """把人物從臉擴散到同一場的其他照片。
    photos：{photo: {"t": datetime 或 None, "faces": [(box, 人物或 None)], "bodies": [(box, feature)]}}
    回傳 {photo: {人物: "face" | "body" | "time"}}。"""
    out = {k: {} for k in photos}
    for k, ph in photos.items():
        for _, person in ph["faces"]:
            if person is not None:
                out[k][person] = "face"

    # 服裝：身體框裡有認得的臉 = 已知外觀；其他身體跟各人物的已知外觀比
    anchors, unknown = {}, []
    for k, ph in photos.items():
        for body, feat in ph["bodies"]:
            who = [p for f, p in ph["faces"] if p is not None and _face_in_body(f, body)]
            if who:
                anchors.setdefault(who[0], []).append(feat)
            elif not any(_face_in_body(f, body) for f, _ in ph["faces"]):  # 臉在但認不出的人不猜
                unknown.append((k, feat))
    if anchors and unknown:
        people = list(anchors)
        mats = [np.array(anchors[p]) for p in people]
        for k, feat in unknown:
            # 每個人物取最像的 3 張已知外觀的平均，避免單張巧合
            scores = sorted(((np.sort(m @ feat)[-3:].mean(), p) for m, p in zip(mats, people)), reverse=True)
            best, p = scores[0]
            second = scores[1][0] if len(scores) > 1 else -1.0
            if best >= body_threshold and best - second >= body_margin:
                out[k].setdefault(p, "body")

    # 時間：還沒歸屬的照片，看前後最近一張有人物的照片
    if time_window > 0:
        timed = sorted((ph["t"], k) for k, ph in photos.items() if ph["t"] and out[k])
        times = [t for t, _ in timed]
        for k, ph in photos.items():
            if out[k] or not ph["t"]:
                continue
            i = bisect.bisect_left(times, ph["t"])
            before = timed[i - 1] if i > 0 else None
            after = timed[i] if i < len(timed) else None
            gap = lambda x: abs((x[0] - ph["t"]).total_seconds()) if x else float("inf")
            if before and after and gap(before) <= time_window and gap(after) <= time_window:
                # 前後都拍到同一個人：中間這張也是他
                for p in set(out[before[1]]) & set(out[after[1]]):
                    out[k][p] = "time"
            else:
                # 只有一邊很近（一段的開頭或結尾），而且那張只有一個人
                near = min((x for x in (before, after) if x), key=gap, default=None)
                if near and gap(near) <= time_window / 5 and len(out[near[1]]) == 1:
                    out[k][next(iter(out[near[1]]))] = "time"
    return out


if __name__ == "__main__":  # 簡單測試：python faces.py 模型路徑 照片...
    model = ensure_model(Path(sys.argv[1]))
    emb = Embedder(model)
    for p in sys.argv[2:]:
        found, bodies = detect(Path(p), 40)
        for box, q, crop in found:
            print(p, "face", [round(v, 3) for v in box], q, emb([crop])[0][:4])
        for box, _ in bodies:
            print(p, "body", [round(v, 3) for v in box])
