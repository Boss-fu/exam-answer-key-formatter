"""去筆跡（推論）：pyexam 在 OCR 之前呼叫 erase(bgr, dpi)，把筆跡像素塗白。

DEHAND_MODE=model|rule|both（預設 both）。rule 是 300dpi 的淡鉛筆規則；模型仍在
150dpi 推論。沒有模型時 rule 仍可獨立使用；DEHAND=0 時什麼都不做。
在 150dpi 灰階上推論（跟訓練一致），遮罩放大回原解析度、外擴 1px 把筆畫邊緣一起清。
只塗「本來就是墨跡」的像素，不會在空白處亂畫；回傳 (清過的影像, 被清掉的像素比例)。
"""
import json
import os

import cv2
import numpy as np

MODELS = os.environ.get("DEHAND_MODEL_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")   # 候選模型驗收時可指向 runs/<rN>
_SESS = None


def _load():
    global _SESS
    if _SESS is None:
        path = os.path.join(MODELS, "dehand.onnx")
        if os.environ.get("DEHAND", "1") == "0" or not os.path.exists(path):
            _SESS = False
        else:
            import onnxruntime as ort
            info = json.load(open(os.path.join(MODELS, "dehand.json"), encoding="utf-8"))
            _SESS = (ort.InferenceSession(path, providers=["CPUExecutionProvider"]),
                     float(os.environ.get("DEHAND_TH", info["threshold"])), int(info.get("dpi", 150)))
    return _SESS


def predict(gray150, sess, tile=1024, pad=32):
    H, W = gray150.shape
    out = np.zeros((H, W), np.float32)
    x = gray150.astype(np.float32) / 255.0
    for y0 in range(0, H, tile):
        for x0 in range(0, W, tile):
            ya, xa = max(0, y0 - pad), max(0, x0 - pad)
            yb, xb = min(H, y0 + tile + pad), min(W, x0 + tile + pad)
            t = x[ya:yb, xa:xb]
            h, w = t.shape
            t = np.pad(t, ((0, (16 - h % 16) % 16), (0, (16 - w % 16) % 16)), constant_values=1.0)
            p = 1 / (1 + np.exp(-sess.run(None, {"x": np.ascontiguousarray(t[None, None])})[0][0, 0, :h, :w]))
            hh, ww = min(tile, H - y0), min(tile, W - x0)
            out[y0:y0 + hh, x0:x0 + ww] = p[y0 - ya:y0 - ya + hh, x0 - xa:x0 - xa + ww]
    return out


def erase(bgr, dpi=300):
    if os.environ.get("DEHAND", "1") == "0":
        return bgr, 0.0
    H, W = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    mode = os.environ.get("DEHAND_MODE", "both").lower()
    if mode not in ("model", "rule", "both"):
        raise ValueError("DEHAND_MODE 必須是 model、rule 或 both")
    mask = np.zeros((H, W), np.uint8)
    if mode in ("rule", "both"):
        try:
            from .pencil_rule import detect
        except ImportError:                 # 直接執行 dehand 工具時沒有 package context
            from pencil_rule import detect
        # 規則以 300dpi 為設計基準；非 300dpi 先等比換算再放回。
        if dpi == 300:
            mask |= (detect(gray) > 0).astype(np.uint8)
        else:
            g300 = cv2.resize(gray, (max(1, round(W * 300 / dpi)), max(1, round(H * 300 / dpi))), interpolation=cv2.INTER_CUBIC)
            mask |= cv2.resize((detect(g300) > 0).astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST)
    if mode in ("model", "both"):
        s = _load()
        if s:
            sess, th, mdpi = s
            small = cv2.resize(gray, (max(16, round(W * mdpi / dpi)), max(16, round(H * mdpi / dpi))), interpolation=cv2.INTER_AREA)
            prob = predict(small, sess)
            mask |= cv2.resize((prob >= th).astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST)
    if not mask.any():
        return bgr, 0.0
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))
    _, ink = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink = cv2.dilate(ink, np.ones((3, 3), np.uint8))           # 含灰色筆畫邊緣
    hit = (mask > 0) & (ink > 0)
    out = bgr.copy()
    out[hit] = 255
    return out, float(hit.mean())
