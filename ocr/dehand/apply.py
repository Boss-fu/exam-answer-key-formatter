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
import time

import cv2
import numpy as np

MODELS = os.environ.get("DEHAND_MODEL_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")   # 候選模型驗收時可指向 runs/<rN>
_SESS = None
LAST_TIMINGS = {"model": 0.0, "qnum": 0.0}
LAST_HAND = None            # 最近一次 img2img 的逐像素筆跡機率（原解析度），hand 輸出模式用

# 題號有時前面帶作答括號「( )12.」，OCR 又可能把全／半形標點混用。
_RE_QNUM = re.compile(r"^\s*(?:[（(]\s*[）)]\s*)?(?:[（(]\s*)?\d{1,2}\s*[\.、．)]")
_RE_QNUM_BARE = re.compile(r"^\s*\d{1,2}\s*$")


def _box_rect(box):
    """PP-OCR 四點框（或既有的 xyxy）正規化為影像座標。"""
    a = np.asarray(box, dtype=np.float32)
    if a.shape == (4,):
        x0, y0, x1, y1 = a
    else:
        a = a.reshape(-1, 2)
        x0, y0 = a.min(axis=0)
        x1, y1 = a.max(axis=0)
    return int(x0), int(y0), int(x1) + 1, int(y1) + 1


def _qnum_protect_mask(bgr, detected_boxes=None, erased_mask=None):
    """回傳印刷題號的禁擦區（題號框四周外擴 3px）。

    ``detected_boxes`` 可直接傳入呼叫端已取得的 PP-OCR 偵測框，避免重跑偵測。
    img2img 的 mask 輸出可傳 ``erased_mask``：只辨識擦除區附近的行首短框，
    因為不會被擦到的題號無需保護。沒有傳遮罩時保留原本的全頁行為（測試與
    非 img2img 模式相容）。
    """
    try:
        from ppocr_engine import det_boxes, rec_only
    except ImportError:  # 供以 ``ocr.dehand.apply`` 匯入的工具使用
        from ocr.ppocr_engine import det_boxes, rec_only

    H, W = bgr.shape[:2]
    boxes = []
    for box in (detected_boxes if detected_boxes is not None else det_boxes(bgr)):
        x0, y0, x1, y1 = _box_rect(box)
        if x1 > x0 and y1 > y0:
            boxes.append((x0, y0, x1, y1))

    nearby = None
    if erased_mask is not None:
        # 題號的印刷筆畫常與模型要擦的手寫筆不直接相交；以約 0.18 吋的
        # 範圍找鄰近框，仍遠小於整頁逐框 rec。
        nearby = cv2.dilate((erased_mask > 0).astype(np.uint8),
                             np.ones((max(3, int(round(W / 46))) | 1,) * 2, np.uint8))
    protected = np.zeros((H, W), np.uint8)
    cols, parens = [], []
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
        if nearby is not None and not nearby[max(0, y0 - 3):min(H, y1 + 3),
                                              max(0, x0 - 3):min(W, x1 + 3)].any():
            continue
        pad = 3
        crop = bgr[max(0, y0 - pad):min(H, y1 + pad), max(0, x0 - pad):min(W, x1 + pad)]
        text, _ = rec_only(crop)
        # 多數試卷會辨成「12.」；這份士林卷的題號框卻只含「12」、
        # 標點在框外。後者只在已確認為行首短框時接受，仍不會碰表格數字。
        if text in ("(", "（") and x1 - x0 < W * .03:
            parens.append((x0, x1))          # 作答括號的左半「(」：也排成一欄，下面同法護住
            continue
        if _RE_QNUM.match(text) or _RE_QNUM_BARE.match(text):
            # 只保護「數字＋點」那一段：框常連作答括號一起（「2.( C」「( A )12.」），
            # 整框保護會把括號裡的手寫答案也護住。依字寬比例推算數字的水平位置。
            a, b = _digit_span(text, x0, x1)
            cv2.rectangle(protected, (max(0, a - 3), max(0, y0 - 3)),
                          (min(W - 1, b + 2), min(H - 1, y1 + 2)), 1, -1)
            cols.append((a, b))
    # 題號被大圈圈住時 OCR 連框都偵測不到（114 大安 3.、4.）：同一頁題號排在同一欄，
    # 拿認得出的題號欄位，把「每一行行首」那一小段都護住
    qcols = _merge_cols(cols)
    for a, b in qcols + [c for c in _merge_cols(parens, need=1) if any(qa < c[0] < qb + W * .06 for qa, qb in qcols)]:
        for x0, y0, x1, y1 in boxes:
            if b < x0 < b + W * .09 and y1 - y0 < H * .04 and \
                    not protected[max(0, y0):y1, max(0, a - 3):b + 3].any():
                cv2.rectangle(protected, (max(0, a - 3), max(0, y0 - 3)), (min(W - 1, b + 2), min(H - 1, y1 + 2)), 1, -1)
    return protected


def _merge_cols(cols, tol=12, need=2):
    """水平範圍分群：同一欄（左緣差 < tol px）至少 need 個才算數。"""
    out = []
    for a, b in sorted(cols):
        if out and abs(a - out[-1][0]) < tol:
            out[-1] = [min(out[-1][0], a), max(out[-1][1], b), out[-1][2] + 1]
        else:
            out.append([a, b, 1])
    return [(a, b) for a, b, n in out if n >= need]


def _digit_span(text, x0, x1):
    """框內文字中題號（數字＋點）所在的 x 範圍；全形／中文字寬算 1，半形算 0.55。"""
    m = re.search(r"\d{1,2}\s*[\.、．)]?", text)
    if not m or not text:
        return x0, x1
    wid = [1.0 if ord(c) > 0x2000 else .55 for c in text]
    tot = sum(wid) or 1
    a = x0 + (x1 - x0) * sum(wid[:m.start()]) / tot
    b = x0 + (x1 - x0) * sum(wid[:m.end()]) / tot
    pad = (x1 - x0) * .25 / tot          # 字寬估計有誤差，左右各多留約半個半形字
    return int(max(x0, a - pad)), int(min(x1, b + pad))


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
    # 預設俊瑋 v10。我們微調的 r7c 像素指標較好，但實卷上會把圖的細曲線當筆跡擦掉（111 石牌 pH 曲線全消失），
    # 重訓（把圖列為保留區）並通過圖片驗收前不當預設；要比較時用 DEHAND_IMG2IMG 指定
    path = os.environ.get("DEHAND_IMG2IMG") or os.path.join(MODELS, "jw", "handwriting-eraser-v10.onnx")
    if path not in _I2I:
        try:
            sess = ort.InferenceSession(path, providers=_providers(ort))
        except Exception:
            sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        _I2I[path] = {"session": sess, "batch": None}
    state = _I2I[path]
    # 相容於先前在互動環境中已快取 session 的情況。
    if not isinstance(state, dict):
        state = _I2I[path] = {"session": state, "batch": None}
    sess = state["session"]; H, W = gray.shape
    small = cv2.resize(gray, (1240, max(16, round(H * 1240 / W))), interpolation=cv2.INTER_AREA)
    h, w = small.shape; T, O = 512, 48
    x = np.repeat((small.astype(np.float32) / 255)[None], 3, 0)
    Hp, Wp = max(h, T), max(w, T); x = np.pad(x, ((0, 0), (0, Hp - h), (0, Wp - w)), mode="edge")
    yy, xx = np.mgrid[0:T, 0:T]
    wt = np.minimum(np.minimum(np.minimum(yy + 1, T - yy), np.minimum(xx + 1, T - xx)), O).astype(np.float32)
    starts = lambda n: (lambda s: s if s[-1] == n - T else s + [n - T])(list(range(0, n - T + 1, T - O)) or [0])
    acc = np.zeros((Hp, Wp), np.float32); ws = np.zeros((Hp, Wp), np.float32)
    coords = [(y0, x0) for y0 in starts(Hp) for x0 in starts(Wp)]
    tiles = np.ascontiguousarray(np.stack([x[:, y0:y0 + T, x0:x0 + T] for y0, x0 in coords]))
    input_name = sess.get_inputs()[0].name
    outputs = None
    if state["batch"] is not False:
        try:
            outputs = sess.run(None, {input_name: tiles})[0]
            state["batch"] = True
        except Exception:
            # CoreML 有些版本只接受 batch=1；記住能力，後續不再付例外成本。
            state["batch"] = False
    if outputs is None:
        outputs = [sess.run(None, {input_name: tile[None]})[0][0] for tile in tiles]
    # 俊瑋 v10 拆解：輸出 2 通道，0＝清稿灰階、1＝「筆跡」logit（sigmoid 後就是逐像素筆跡機率）
    hand = np.zeros((Hp, Wp), np.float32)
    for (y0, x0), o in zip(coords, outputs):
        acc[y0:y0 + T, x0:x0 + T] += o[0] * wt; ws[y0:y0 + T, x0:x0 + T] += wt
        if o.shape[0] > 1:
            hand[y0:y0 + T, x0:x0 + T] += 1 / (1 + np.exp(-np.clip(o[1], -30, 30))) * wt
    clean = np.clip(((acc / ws)[:h, :w] - .25) / .63, 0, 1)
    global LAST_HAND
    LAST_HAND = cv2.resize((hand / ws)[:h, :w], (W, H), interpolation=cv2.INTER_LINEAR)
    th = float(os.environ.get("DEHAND_I2I_TH", ".75"))
    erased = ((small < 200) & (clean > th)).astype(np.uint8)          # 原本有墨、輸出接近白（> th）＝被擦掉
    return cv2.resize(erased, (W, H), interpolation=cv2.INTER_NEAREST), cv2.resize((clean * 255).astype(np.uint8), (W, H), interpolation=cv2.INTER_CUBIC)


def erase(bgr, dpi=300, detected_boxes=None):
    if os.environ.get("DEHAND", "1") == "0":
        return bgr, 0.0
    LAST_TIMINGS.update(model=0.0, qnum=0.0)
    H, W = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    # 預設 img2img（13 份測試卷 CER 20.8%，舊的 both 22.2%）；沒有 img2img 模型時退回 both
    mode = os.environ.get("DEHAND_MODE") or ("img2img" if os.path.exists(os.path.join(MODELS, "jw")) else "both")
    mode = mode.lower()
    if mode not in ("model", "rule", "both", "img2img"):
        raise ValueError("DEHAND_MODE 必須是 model、rule、both 或 img2img")
    mask = np.zeros((H, W), np.uint8)
    if mode == "img2img":
        t_model = time.perf_counter()
        erased, clean = _img2img_erased(gray, dpi)
        LAST_TIMINGS["model"] = time.perf_counter() - t_model
        out_mode = os.environ.get("DEHAND_I2I_OUT", "mask").lower()
        if out_mode not in ("mask", "full", "hand"):
            raise ValueError("DEHAND_I2I_OUT 必須是 mask、full 或 hand")
        if out_mode == "hand" and LAST_HAND is not None:
            # hand：直接用模型的筆跡機率圖擦，印刷保留原圖；圖的區塊內要更有把握（v10 會把細曲線也標成筆跡）
            p = LAST_HAND
            th = np.full(p.shape, float(os.environ.get("DEHAND_HAND_TH", ".5")), np.float32)
            try:
                from pyexam import figure_candidates
            except ImportError:
                from ocr.pyexam import figure_candidates
            m = int(.04 * W)                 # 圖下方／旁邊的 (A)(B) 標籤、軸標也算圖的一部分
            for fx0, fy0, fx1, fy1 in figure_candidates(bgr, dpi=dpi):
                th[max(0, fy0 - m):fy1 + m, max(0, fx0 - m):fx1 + m] = float(os.environ.get("DEHAND_HAND_FIG_TH", ".97"))
            k = int(os.environ.get("DEHAND_HAND_DILATE", "5"))
            hit = cv2.dilate((p > th).astype(np.uint8), np.ones((k, k), np.uint8)) > 0
            if os.environ.get("DEHAND_PROTECT_QNUM", "1") != "0":
                t_qnum = time.perf_counter()
                hit[_qnum_protect_mask(bgr, detected_boxes=detected_boxes, erased_mask=hit.astype(np.uint8)) > 0] = False
                LAST_TIMINGS["qnum"] = time.perf_counter() - t_qnum
            out = bgr.copy(); out[hit] = 255
            if os.environ.get("DEHAND_HAND_PRINT", "1") != "0":
                # 打印版色階（同俊瑋 v10 輸出的 (g-.25)/.63）：掃描的灰字拉成飽滿的黑、底色拉白
                lut = np.clip((np.arange(256) / 255 - .25) / .63 * 255, 0, 255).astype(np.uint8)
                out = cv2.LUT(out, lut)
            return out, float(hit.mean())
        if out_mode == "full":
            # full：直接用模型輸出（像俊瑋網頁的打印版）；13 份測試卷上漏字較多，所以預設 mask
            out = cv2.cvtColor(clean, cv2.COLOR_GRAY2BGR)
            if os.environ.get("DEHAND_PROTECT_QNUM", "1") != "0":
                t_qnum = time.perf_counter()
                keep = _qnum_protect_mask(bgr, detected_boxes=detected_boxes) > 0
                LAST_TIMINGS["qnum"] = time.perf_counter() - t_qnum
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
        t_qnum = time.perf_counter()
        qnum_mask = erased if mode == "img2img" else None
        mask[_qnum_protect_mask(bgr, detected_boxes=detected_boxes, erased_mask=qnum_mask) > 0] = 0
        LAST_TIMINGS["qnum"] = time.perf_counter() - t_qnum
    _, ink = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink = cv2.dilate(ink, np.ones((3, 3), np.uint8))           # 含灰色筆畫邊緣
    hit = (mask > 0) & (ink > 0)
    out = bgr.copy()
    out[hit] = 255
    return out, float(hit.mean())
