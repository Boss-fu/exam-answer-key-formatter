"""PP-OCRv5 引擎封裝（完全本地、onnxruntime，不呼叫任何外部 API）。

用法：
    from ppocr_engine import ocr_lines, models_available
    lines = ocr_lines(img)   # img: BGR 或灰階 ndarray
    # -> [{"box": [[x,y]*4], "text": "繁體文字", "conf": 0.98}, ...]

模型位置：預設 <repo>/ocr/models/{det.onnx, rec.onnx, ppocrv5_dict.txt}，
可用環境變數 OCR_MODELS_DIR 覆寫。引擎與 OpenCC 皆為模組層級 lazy singleton。
"""
import os
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_FILES = ("det.onnx", "rec.onnx", "ppocrv5_dict.txt")

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
                )
    return _engine


def _get_cc():
    global _cc
    if _cc is None:
        try:
            import opencc
            _cc = opencc.OpenCC("s2twp")
        except Exception:
            _cc = False  # 沒裝 OpenCC：原樣輸出
    return _cc


def to_traditional(text):
    cc = _get_cc()
    return cc.convert(text) if cc else text


def ocr_lines(img):
    """整張（或整欄）影像 → 文字框清單。text 已轉繁體（s2twp）。"""
    result, _ = _get_engine()(img)
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
