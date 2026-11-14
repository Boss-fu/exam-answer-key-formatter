"""PP-OCRv5 引擎封裝（完全本地、onnxruntime，不呼叫任何外部 API）。

用法：
    from ppocr_engine import ocr_lines, models_available
    lines = ocr_lines(img)   # img: BGR 或灰階 ndarray
    # -> [{"box": [[x,y]*4], "text": "繁體文字", "conf": 0.98}, ...]

模型位置：預設 <repo>/ocr/models/{det.onnx, rec.onnx, ppocrv5_dict.txt}，
可用環境變數 OCR_MODELS_DIR 覆寫。引擎與 OpenCC 皆為模組層級 lazy singleton。

注意兩個刻意的選擇：
  * OpenCC 用 s2tw，不用 s2twp。s2twp 會做「用語」替換（通過→透過、拋出→丟擲、
    高級中學→高階中學、函數→函式、循環→迴圈），在考卷上是錯的。
  * 關閉文字方向分類器（use_cls=False）。考卷掃描都是正向，cls 會把整行誤判成倒置
    而丟掉（實測整題 18 因此消失），關掉也比較快。
"""
import os

import numpy as np
import threading

from ocr_symbols import fix_symbols

_HERE = os.path.dirname(os.path.abspath(__file__))
_FILES = ("det.onnx", "rec.onnx", "ppocrv5_dict.txt")
# rapidocr 預設超過 2000px 的邊會被縮小；整欄影像（300dpi A4 約 3300px 高）縮小後小字會變糊。
# 辨識信心低於此值的文字行才丟棄。預設 0.3（rapidocr 預設 0.5）：公式／分數行信心常只有 0.4~0.5，
# 丟掉等於整行不見；寧可保留，交給 pyexam 的低信心標記（⚠需確認）讓人校對。
TEXT_SCORE = float(os.environ.get("OCR_TEXT_SCORE", "0.3"))
MAX_SIDE_LEN = int(os.environ.get("OCR_MAX_SIDE_LEN", "4000"))

_engine = None
_cc = None
_lock = threading.Lock()


def models_dir():
    return os.environ.get("OCR_MODELS_DIR") or os.path.join(_HERE, "models")


def models_available():
    """模型檔齊全且 rapidocr_onnxruntime 可匯入時回傳 True。"""
    d = models_dir()
    if not all(os.path.isfile(os.path.join(d, f)) for f in _FILES):
        return False
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


def _get_engine():
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                from rapidocr_onnxruntime import RapidOCR
                d = models_dir()
                _engine = RapidOCR(
                    det_model_path=os.path.join(d, "det.onnx"),
                    rec_model_path=os.path.join(d, "rec.onnx"),
                    rec_keys_path=os.path.join(d, "ppocrv5_dict.txt"),
                    use_cls=False,
                    max_side_len=MAX_SIDE_LEN,
                )
    return _engine


def _get_cc():
    global _cc
    if _cc is None:
        try:
            import opencc
            _cc = opencc.OpenCC("s2tw")
        except Exception:
            _cc = False  # 沒裝 OpenCC：原樣輸出
    return _cc


def to_traditional(text):
    """只轉「Big5 字集沒有」的字（真正的簡體，如 发、镜）；本來就合法的繁體字一律不動。
    整句丟給 OpenCC 會把正確的繁體改錯：干擾→幹擾、游離→遊離、准→準。"""
    cc = _get_cc()
    if not cc:
        return fix_symbols(text)
    out = []
    for ch in text:
        if ord(ch) > 0x2E80:
            try:
                ch.encode("cp950")
            except UnicodeEncodeError:
                t = cc.convert(ch)
                try:
                    t.encode("cp950")
                    ch = t
                except UnicodeEncodeError:
                    pass
        out.append(ch)
    return fix_symbols("".join(out))


# ---------------------------------------------------------------- Apple Vision（macOS 內建，用神經網路引擎，幾乎不吃 CPU）
# OCR_ENGINE=vision 時，ocr_lines／det_boxes／rec_only 都改用 Apple Vision（只限 Mac；Windows 主機照用 PP-OCRv5）。
ENGINE = os.environ.get("OCR_ENGINE", "ppocr")


def _vision(img):
    """numpy BGR／灰階 → [(四點框(像素), 文字, 信心)]。Vision 的框是 0~1、原點在左下，要換成像素、原點左上"""
    import cv2
    import Quartz
    import Vision
    from Foundation import NSData
    ok, buf = cv2.imencode(".png", img)
    data = NSData.dataWithBytes_length_(buf.tobytes(), len(buf))
    src = Quartz.CGImageSourceCreateWithData(data, None)
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setRecognitionLanguages_(["zh-Hant", "en-US"])
    req.setUsesLanguageCorrection_(True)
    Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None).performRequests_error_([req], None)
    H, W = img.shape[:2]
    out = []
    for o in req.results() or []:
        c = o.topCandidates_(1)[0]
        b = o.boundingBox()
        x0, x1 = b.origin.x * W, (b.origin.x + b.size.width) * W
        y0, y1 = (1 - b.origin.y - b.size.height) * H, (1 - b.origin.y) * H
        out.append(([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], str(c.string()), float(c.confidence())))
    return out


def det_boxes(img):
    """只跑文字偵測、不辨識（快）：回傳四點框清單 [[[x,y]*4], ...]。給版面分析（找欄界）用。"""
    img = np.ascontiguousarray(img)          # 欄位切片在記憶體裡不連續，直接丟進 onnxruntime 會 segfault（A3 四欄卷重現）
    if ENGINE == "vision":
        return [box for box, _, _ in _vision(img)]
    result, _ = _get_engine()(img, use_det=True, use_cls=False, use_rec=False)
    return result or []


def rec_only(img):
    """不做偵測、整張圖當成一行直接辨識（給已裁好的小區塊，例如只有一個字的表格儲存格）。回傳 (文字, 信心)。"""
    img = np.ascontiguousarray(img)
    if ENGINE == "vision":
        r = _vision(img)
        return (fix_symbols("".join(t for _, t, _ in r)).strip(), max((c for _, _, c in r), default=0.0))
    result, _ = _get_engine()(img, use_det=False, use_cls=False, use_rec=True)
    if not result:
        return "", 0.0
    text, conf = result[0][0], result[0][1]
    return to_traditional(str(text)).strip(), float(conf)


def ocr_lines(img):
    """整張（或整欄）影像 → 文字框清單。text 已轉繁體（s2tw：只轉字形，不做用語替換）。"""
    img = np.ascontiguousarray(img)          # 欄位切片在記憶體裡不連續，直接丟進 onnxruntime 會 segfault（A3 四欄卷重現）
    if ENGINE == "vision":
        result = _vision(img)
    else:
        result, _ = _get_engine()(img, use_cls=False, text_score=TEXT_SCORE)
    if not result:
        return []
    out = []
    for box, text, conf in result:
        text = to_traditional(str(text)).strip()
        if not text:
            continue
        out.append({
            "box": [[float(x), float(y)] for x, y in box],
            "text": text,
            "conf": float(conf),
        })
    return out
