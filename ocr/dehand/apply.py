"""去筆跡（推論）：pyexam 在 OCR 之前呼叫 erase(bgr, dpi)，把筆跡像素塗白。

DEHAND_MODE=model|rule|both|img2img（預設 both）。img2img 用整張轉換網路（俊瑋 v10 或我們微調版，
DEHAND_IMG2IMG 指定 onnx）在 150dpi 推論；DEHAND_I2I_OUT=mask（預設）只在「原本是墨、輸出變白」的地方擦，印刷保持原解析度，full 則整頁換成模型輸出。rule 是 300dpi 的淡鉛筆規則；模型仍在
150dpi 推論。沒有模型時 rule 仍可獨立使用；DEHAND=0 時什麼都不做。
DEHAND_PROTECT_QNUM=1（預設）會辨識行首題號並設為禁擦區；設 0 可關閉。
在 150dpi 灰階上推論（跟訓練一致），遮罩放大回原解析度、外擴 1px 把筆畫邊緣一起清。
只塗「本來就是墨跡」的像素，不會在空白處亂畫；回傳 (清過的影像, 被清掉的像素比例)。
"""
import json
import os
import re
import sys

import cv2
import numpy as np

MODELS = os.environ.get("DEHAND_MODEL_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")   # 候選模型驗收時可指向 runs/<rN>
_SESS = None

# 題號有時前面帶作答括號「( )12.」，OCR 又可能把全／半形標點混用。
_RE_QNUM = re.compile(r"^\s*(?:[（(]\s*[）)]\s*)?(?:[（(]\s*)?\d{1,2}\s*[\.、．)]")
_RE_QNUM_BARE = re.compile(r"^\s*\d{1,2}\s*$")


def _qnum_protect_mask(bgr):
    """回傳印刷題號的禁擦區（題號框四周外擴 3px）。

    偵測一次整頁，再只辨識看起來是行首短框的候選；短框右方必須接同一行
    的文字框，避免表格數字和圖中刻度被當成題號。
    """
    try:
        from ppocr_engine import det_boxes, rec_only
    except ImportError:  # 供以 ``ocr.dehand.apply`` 匯入的工具使用
        from ocr.ppocr_engine import det_boxes, rec_only

    H, W = bgr.shape[:2]
    boxes = []
    for box in det_boxes(bgr):
        a = np.asarray(box, dtype=np.float32)
        x0, y0 = a.min(axis=0)
        x1, y1 = a.max(axis=0)
        if x1 > x0 and y1 > y0:
            boxes.append((int(x0), int(y0), int(x1) + 1, int(y1) + 1))

    protected = np.zeros((H, W), np.uint8)
    for x0, y0, x1, y1 in boxes:
        # 題號自身很短，且其右方同一行應有題幹；這就是欄位左側的行首。
        if x1 - x0 > W * .12 or y1 - y0 > H * .08:
            continue
        has_following_text = any(
            nx0 >= x1 - 3 and nx0 - x1 < W * .22 and ny0 < y1 and ny1 > y0
            for nx0, ny0, nx1, ny1 in boxes
        )
        if not has_following_text:
            continue
        pad = 3
        crop = bgr[max(0, y0 - pad):min(H, y1 + pad), max(0, x0 - pad):min(W, x1 + pad)]
        text, _ = rec_only(crop)
        # 多數試卷會辨成「12.」；這份士林卷的題號框卻只含「12」、
        # 標點在框外。後者只在已確認為行首短框時接受，仍不會碰表格數字。
        if _RE_QNUM.match(text) or _RE_QNUM_BARE.match(text):
            cv2.rectangle(protected, (max(0, x0 - 3), max(0, y0 - 3)),
                          (min(W - 1, x1 + 2), min(H - 1, y1 + 2)), 1, -1)
    return protected


def _providers(ort):
    """優先用本機可用的加速器；無論哪台主機都保留 CPU 安全退路。

    Mac 的 onnxruntime 套件含 CoreML provider，但以前被固定成 CPU，去筆跡
    每頁都白白不用 Neural Engine／GPU。DEHAND_PROVIDER=cpu 可在驗收時強制
    舊行為；任何 CoreML 初始化失敗則回退 CPU，不影響 Windows 主機。
    """
    wanted = os.environ.get("DEHAND_PROVIDER", "auto").lower()
    available = set(ort.get_available_providers())
    if wanted == "cpu":
        return ["CPUExecutionProvider"]
    if wanted == "coreml":
        return ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    if sys.platform == "darwin" and "CoreMLExecutionProvider" in available:
        return ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def _load():
    global _SESS
    if _SESS is None:
        path = os.path.join(MODELS, "dehand.onnx")
        if os.environ.get("DEHAND", "1") == "0" or not os.path.exists(path):
            _SESS = False
        else:
            import onnxruntime as ort
            info = json.load(open(os.path.join(MODELS, "dehand.json"), encoding="utf-8"))
            try:
                session = ort.InferenceSession(path, providers=_providers(ort))
            except Exception:
                # 有些舊 ONNX 圖或企業版 macOS 禁用 CoreML；仍要能在 CPU 正常跑。
                session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
            _SESS = (session, float(os.environ.get("DEHAND_TH", info["threshold"])), int(info.get("dpi", 150)))
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


_I2I = {}


def _img2img_erased(gray, dpi):
    """回傳原解析度的遮罩：模型判定該擦掉的墨跡。"""
    import onnxruntime as ort
    path = os.environ.get("DEHAND_IMG2IMG") or os.path.join(MODELS, "jw", "handwriting-eraser-v10.onnx")
    if path not in _I2I:
        _I2I[path] = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    sess = _I2I[path]; H, W = gray.shape
    small = cv2.resize(gray, (1240, max(16, round(H * 1240 / W))), interpolation=cv2.INTER_AREA)
    h, w = small.shape; T, O = 512, 48
    x = np.repeat((small.astype(np.float32) / 255)[None], 3, 0)
    Hp, Wp = max(h, T), max(w, T); x = np.pad(x, ((0, 0), (0, Hp - h), (0, Wp - w)), mode="edge")
    yy, xx = np.mgrid[0:T, 0:T]
    wt = np.minimum(np.minimum(np.minimum(yy + 1, T - yy), np.minimum(xx + 1, T - xx)), O).astype(np.float32)
    starts = lambda n: (lambda s: s if s[-1] == n - T else s + [n - T])(list(range(0, n - T + 1, T - O)) or [0])
    acc = np.zeros((Hp, Wp), np.float32); ws = np.zeros((Hp, Wp), np.float32)
    for y0 in starts(Hp):
        for x0 in starts(Wp):
            o = sess.run(None, {sess.get_inputs()[0].name: np.ascontiguousarray(x[None, :, y0:y0 + T, x0:x0 + T])})[0][0, 0]
            acc[y0:y0 + T, x0:x0 + T] += o * wt; ws[y0:y0 + T, x0:x0 + T] += wt
    clean = np.clip(((acc / ws)[:h, :w] - .25) / .63, 0, 1)
    erased = ((small < 200) & (clean > .85)).astype(np.uint8)          # 原本有墨、輸出接近白＝被擦掉
    return cv2.resize(erased, (W, H), interpolation=cv2.INTER_NEAREST), cv2.resize((clean * 255).astype(np.uint8), (W, H), interpolation=cv2.INTER_CUBIC)


def erase(bgr, dpi=300):
    if os.environ.get("DEHAND", "1") == "0":
        return bgr, 0.0
    H, W = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    mode = os.environ.get("DEHAND_MODE", "both").lower()
    if mode not in ("model", "rule", "both", "img2img"):
        raise ValueError("DEHAND_MODE 必須是 model、rule、both 或 img2img")
    mask = np.zeros((H, W), np.uint8)
    if mode == "img2img":
        erased, clean = _img2img_erased(gray, dpi)
        out_mode = os.environ.get("DEHAND_I2I_OUT", "mask").lower()
        if out_mode not in ("mask", "full"):
            raise ValueError("DEHAND_I2I_OUT 必須是 mask 或 full")
        if out_mode == "full":
            # full：直接用模型輸出（像俊瑋網頁的打印版）；13 份測試卷上漏字較多，所以預設 mask
            out = cv2.cvtColor(clean, cv2.COLOR_GRAY2BGR)
            if os.environ.get("DEHAND_PROTECT_QNUM", "1") != "0":
                keep = _qnum_protect_mask(bgr) > 0
                out[keep] = bgr[keep]
            return out, float(erased.mean())
        mask |= erased
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
    if os.environ.get("DEHAND_PROTECT_QNUM", "1") != "0":
        # 寧可留下題號上的可疑筆跡，也不能擦掉印刷題號而讓後段切題失敗。
        mask[_qnum_protect_mask(bgr) > 0] = 0
    _, ink = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink = cv2.dilate(ink, np.ones((3, 3), np.uint8))           # 含灰色筆畫邊緣
    hit = (mask > 0) & (ink > 0)
    out = bgr.copy()
    out[hit] = 255
    return out, float(hit.mean())
