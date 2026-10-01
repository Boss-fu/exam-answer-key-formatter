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
import threading

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
    cc = _get_cc()
    return cc.convert(text) if cc else text


def ocr_lines(img):
    """整張（或整欄）影像 → 文字框清單。text 已轉繁體（s2tw：只轉字形，不做用語替換）。"""
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
