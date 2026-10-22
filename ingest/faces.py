"""
人臉分組（在 Mac 本機執行，人臉特徵不會離開這台 Mac）

  1. 偵測：macOS Vision 找臉、五官位置與臉部品質（VNDetectFaceLandmarksRequest / CaptureQuality）
  2. 特徵：依五官做相似變換對齊成 112×112，丟進 ArcFace（InsightFace w600k_r50，ONNX）得到 512 維特徵
  3. 分群：整本相簿的臉做 average-linkage 階層分群（cosine 距離），出現張數太少的群（路人）不算

模型授權：InsightFace 預訓練模型僅限非商業用途（https://github.com/deepinsight/insightface#license）。
"""

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


def _align(img: Image.Image, five: np.ndarray) -> Image.Image:
    m = _similarity(five, ARCFACE_DST)
    inv = np.linalg.inv(np.vstack([m, [0, 0, 1]]))[:2]  # PIL 要的是輸出 → 輸入
    return img.transform((112, 112), Image.AFFINE, inv.flatten().tolist(), Image.BILINEAR)


def detect(path: Path, min_px: int):
    """回傳 [(box, quality, aligned_crop)]；box = (x, y, w, h)，相對於轉正後整張圖、左上原點、0~1。"""
    with Image.open(path) as im:
        im.draft("RGB", (DETECT_EDGE, DETECT_EDGE))
        img = ImageOps.exif_transpose(im).convert("RGB")
    img.thumbnail((DETECT_EDGE, DETECT_EDGE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    data = buf.getvalue()
    W, H = img.size

    found = []
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
            box = (max(0.0, x), max(0.0, 1 - y - h), min(w, 1.0), min(h, 1.0))
            found.append((box, q, _align(img, five)))
        del handler, lm_req, faces
    return found


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


if __name__ == "__main__":  # 簡單測試：python faces.py 模型路徑 照片...
    model = ensure_model(Path(sys.argv[1]))
    emb = Embedder(model)
    for p in sys.argv[2:]:
        for box, q, crop in detect(Path(p), 40):
            print(p, [round(v, 3) for v in box], q, emb([crop])[0][:4])
