#!/usr/bin/env python3
"""純 Python 考卷前處理管線（無 Claude、零 token）。

用我們討論的這一系列 Python 工具串起來：
  PyMuPDF  讀 PDF／判斷有無文字層／把掃描頁 render 成高解析圖、裁圖
  OpenCV + numpy  影像前處理（放大、去雜訊、Otsu 二值化、估傾斜）
  PP-OCRv5 (rapidocr_onnxruntime + OpenCC s2twp)  掃描頁 OCR（預設，完全本地）
  Tesseract (pytesseract, chi_tra+eng)  備援：ocr/models/ 缺模型時自動退回
  python-docx  把抽出的文字重排成乾淨 Word

策略（關鍵）：
  * 數位 PDF（有文字層）→ 直接用 PyMuPDF 取字，近乎 100% 且免費免 OCR。
  * 掃描 PDF（無文字層）→ render 300dpi → 雙欄切割 → PP-OCRv5（無模型則 Tesseract）。
  圖片一律不重畫：偵測到圖就裁成 *_p{n}_fig{k}.png，並在文字裡留〔含圖 k〕佔位。

PP-OCRv5 路徑的後處理（見 ocr_page_ppocr_records）：
  * 圖區（連通域偵測）內／旁的刻度、軸名、(A)(B) 字母不併入正文；半壓到圖的文字框遮掉圖後重辨
  * 題號括號正規化成 `( )N.`（)4. (.)9. 021. （)13. (C)6. → ( )N.）；吞掉括號的 `N.` 依序補回
  * Unicode 上下標 → v_{1}、m/s^{2}；同行黏住的選項 (B)(C)(D)(E) 前補空白
  * 行信心 < LOW_CONF(0.8) 的行尾加「　⚠需確認」，公式碎片先靠它標出來（真正修在階段 2）

用法：
    python3 ocr/pyexam.py 某考卷.pdf -o out/            # 產 .txt + .docx + 裁圖
    python3 ocr/pyexam.py 某考卷.pdf --dpi 400 --page 1  # 只試第1頁、提高解析度
"""
import argparse
import os
import re
import sys
import time

import numpy as np
import pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import ppocr_engine
except Exception:
    ppocr_engine = None

try:
    import cv2
except Exception:
    cv2 = None
try:
    import pytesseract
except Exception:
    pytesseract = None


# ---------- 影像前處理（OpenCV）----------
def preprocess(img_bgr, upscale=1.5):
    """灰階 → 放大 → 去雜訊 → Otsu 二值化。回傳適合 OCR 的單通道圖。"""
    if cv2 is None:
        return img_bgr
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    if upscale and upscale != 1.0:
        gray = cv2.resize(gray, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.fastNlMeansDenoising(gray, h=10)
    _, binimg = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binimg


def page_to_bgr(page, dpi):
    pix = page.get_pixmap(dpi=dpi)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR) if cv2 is not None else arr[:, :, :3]
    elif pix.n == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR) if cv2 is not None else arr
    return arr


# ---------- 圖片偵測（OpenCV 輪廓）----------
def detect_figures(binimg, page_h, min_area_ratio=0.02, max_area_ratio=0.45):
    """找出像「圖／表」的矩形區塊（大片連通、非整行文字）。回傳 bbox 清單（原圖座標比例）。"""
    if cv2 is None:
        return []
    inv = 255 - binimg
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
    closed = cv2.morphologyEx(inv, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    H, W = binimg.shape[:2]
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = (w * h) / float(W * H)
        aspect = w / float(h + 1)
        # 太小=雜點；太扁太寬=整段文字；取中等塊且近方形者視為圖
        if min_area_ratio <= area <= max_area_ratio and 0.25 <= aspect <= 4.0 and h > 0.05 * H:
            boxes.append((x / W, y / H, (x + w) / W, (y + h) / H))
    boxes.sort(key=lambda b: b[1])
    return boxes


# ---------- 欄位偵測（numpy 直向投影找白色間隔）----------
def find_column_split(binimg):
    """考卷常見雙欄。用每一欄(column)的墨點量找中間的白色間隔帶；
    找到就回傳切割 x 座標，否則回傳 None（單欄）。"""
    H, W = binimg.shape[:2]
    ink = (binimg < 128).sum(axis=0).astype(float)  # 每個 x 的黑點數
    # 只在中央 40%~60% 找間隔，避免切到頁邊
    lo, hi = int(W * 0.38), int(W * 0.62)
    band = ink[lo:hi]
    if band.size == 0:
        return None
    # 間隔帶：墨點量遠低於整頁中位數
    thresh = max(1.0, np.median(ink) * 0.15)
    gutter = np.where(band < thresh)[0]
    if gutter.size < (hi - lo) * 0.15:  # 中央幾乎沒有夠長的留白 → 視為單欄
        return None
    return lo + int(np.median(gutter))


# ---------- OCR 一頁（自動雙欄切割）----------
LOW_CONF = 0.8                  # 行信心低於此值 → 行尾標「需確認」
LOW_CONF_MARK = "　⚠需確認"
RE_CJK = re.compile(r"[一-鿿]")


def ppocr_available():
    return ppocr_engine is not None and ppocr_engine.models_available()


def resolve_engine(engine="auto"):
    """auto：模型在就用 PP-OCRv5，否則 Tesseract。回傳 'ppocr' 或 'tesseract'。"""
    if engine == "tesseract":
        return "tesseract"
    if engine == "ppocr":
        if not ppocr_available():
            raise RuntimeError("找不到 PP-OCRv5 模型或 rapidocr_onnxruntime（見 ocr/models/、OCR_MODELS_DIR）")
        return "ppocr"
    return "ppocr" if ppocr_available() else "tesseract"


# ---------- 圖區偵測（連通域）＋圖內文字過濾 ----------
def figure_candidates(bgr, dpi=300, merge_gap=60):
    """用墨跡連通域找「圖」候選（座標圖、示意圖、尺／紙帶照片…）。
    文字筆畫的連通域都很小；圖的線條彼此相連，bbox 大。回傳 [(x0,y0,x1,y1)] 像素座標。
    表格框線／整行底線會因為太細（短邊 < 0.33 吋）被排除；表格本身靠後面的「框內中文字數」濾掉。"""
    if cv2 is None:
        return []
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    H, W = ink.shape[:2]
    n, _, st, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    min_short, min_long = 0.33 * dpi, 0.43 * dpi
    boxes = []
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if min(w, h) < min_short or max(w, h) < min_long:
            continue
        if w * h > 0.4 * W * H or w / float(h) > 6 or h / float(w) > 6:
            continue
        boxes.append([x, y, x + w, y + h])
    # 距離近的合併（例如 (A)~(E) 五張小圖併成一塊）
    merged = True
    while merged:
        merged = False
        out = []
        for b in boxes:
            for o in out:
                if (b[0] - merge_gap < o[2] and o[0] - merge_gap < b[2] and
                        b[1] - merge_gap < o[3] and o[1] - merge_gap < b[3]):
                    o[0], o[1], o[2], o[3] = min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])
                    merged = True
                    break
            else:
                out.append(list(b))
        boxes = out
    boxes.sort(key=lambda b: (b[1], b[0]))
    return [tuple(int(v) for v in b) for b in boxes]


def _center(it):
    xs = [p[0] for p in it["box"]]
    ys = [p[1] for p in it["box"]]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(xs), min(ys), max(xs), max(ys)


def _inter_ratio(it, fig):
    _, _, x0, y0, x1, y1 = _center(it)
    iw = min(x1, fig[2]) - max(x0, fig[0])
    ih = min(y1, fig[3]) - max(y0, fig[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    return iw * ih / float(max(1.0, (x1 - x0) * (y1 - y0)))


def split_figure_text(items, cands, pad=70, max_cjk_in_figure=6):
    """依 OCR 框決定哪些候選才是真的圖（框內中文字 ≤ max_cjk_in_figure；表格／標題框內中文多，不是圖），
    並把圖內文字框分出來。回傳 (figs, body_items, partial_items)：
      body_items    不受圖影響的文字框
      partial_items [(item, fig)] 只有一部分壓到圖，要遮掉圖再重辨
    圖內文字 = 中心在圖框內，或在圖框外擴 pad px 內且為短的非中文標註（刻度、軸名、(A)(B) 選項字母）。"""
    figs = []
    for c in cands:
        inside = [it for it in items if c[0] <= _center(it)[0] <= c[2] and c[1] <= _center(it)[1] <= c[3]]
        if sum(len(RE_CJK.findall(it["text"])) for it in inside) <= max_cjk_in_figure:
            figs.append(c)
    body, partial = [], []
    for it in items:
        cx, cy, *_ = _center(it)
        text = it["text"]
        short = len(RE_CJK.findall(text)) <= 2 and len(text) <= 16
        drop, part = False, None
        for f in figs:
            r = _inter_ratio(it, f)
            if r >= 0.6 or (f[0] <= cx <= f[2] and f[1] <= cy <= f[3]):
                drop = True
            elif short and (f[0] - pad <= cx <= f[2] + pad and f[1] - pad <= cy <= f[3] + pad):
                drop = True
            elif r >= 0.12:
                part = f
        if drop:
            continue
        if part is not None:
            partial.append((it, part))
        else:
            body.append(it)
    return figs, body, partial


def _reocr_masked(bgr, it, fig):
    """文字框只有一部分壓到圖：把圖區塗白後，只對這個框附近重新辨識。"""
    _, _, x0, y0, x1, y1 = _center(it)
    H, W = bgr.shape[:2]
    px = 8
    cx0, cy0 = max(0, int(x0) - px), max(0, int(y0) - px)
    cx1, cy1 = min(W, int(x1) + px), min(H, int(y1) + px)
    crop = bgr[cy0:cy1, cx0:cx1].copy()
    fx0, fy0 = max(0, fig[0] - cx0), max(0, fig[1] - cy0)
    fx1, fy1 = max(0, fig[2] - cx0), max(0, fig[3] - cy0)
    crop[fy0:fy1, fx0:fx1] = 255
    out = []
    for r in ppocr_engine.ocr_lines(crop):
        r["box"] = [[x + cx0, y + cy0] for x, y in r["box"]]
        out.append(r)
    return out


# ---------- 行重組與後處理 ----------
def group_into_lines(items, min_conf=0.0):
    """把 PP-OCR 的文字框依「垂直重疊」併成視覺行（表格同列的格子併成一行），
    行內由左到右、行間由上到下。回傳 [{text, conf, y0, y1, x0, x1}]；conf 以字數加權平均。"""
    boxes = []
    for it in items:
        if it["conf"] < min_conf:
            continue
        xs = [p[0] for p in it["box"]]
        ys = [p[1] for p in it["box"]]
        boxes.append({"x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys),
                      "text": it["text"], "conf": it["conf"]})
    boxes.sort(key=lambda b: (b["y0"] + b["y1"]) / 2)
    rows = []
    for b in boxes:
        cy = (b["y0"] + b["y1"]) / 2
        h = b["y1"] - b["y0"]
        if rows:
            r = rows[-1]
            rcy = sum((m["y0"] + m["y1"]) / 2 for m in r) / len(r)
            rh = sum(m["y1"] - m["y0"] for m in r) / len(r)
            if abs(cy - rcy) < 0.5 * min(h, rh):
                r.append(b)
                continue
        rows.append([b])
    lines = []
    for r in rows:
        r.sort(key=lambda m: m["x0"])
        hmed = sorted(m["y1"] - m["y0"] for m in r)[len(r) // 2]
        text = r[0]["text"]
        for prev, cur in zip(r, r[1:]):
            gap = cur["x0"] - prev["x1"]
            text += (" " if gap > 0.6 * hmed else "") + cur["text"]
        wsum = sum(max(1, len(m["text"])) for m in r)
        lines.append({"text": text,
                      "conf": sum(m["conf"] * max(1, len(m["text"])) for m in r) / wsum,
                      "y0": min(m["y0"] for m in r), "y1": max(m["y1"] for m in r),
                      "x0": r[0]["x0"], "x1": max(m["x1"] for m in r)})
    return lines


# 題號：行首 ( )N. 被讀成 )4. / (.)9. / 021. / （)13.，甚至括號裡夾了手寫選項字母 (C)6.
# → 一律還原成乾淨的「( )N.」。只有「真的帶括號殘影」的行才動，沒括號的 18. 維持原樣。
_RE_QNO_BAD = re.compile(
    r"^\s*(?:[\(（\[]\s*[A-Ea-e.·,，\s]{0,3}\s*[\)）\]]?|[\)）]|[0Oo](?=\d))"
    r"\s*(\d{1,2})\s*[\.．、](?=\s*[^\d\s])")
_RE_QNO_CANON = re.compile(r"^\( \)\d{1,2}\.")
_RE_QNO_BARE = re.compile(r"^\s*(\d{1,2})\s*[\.．、](?=\s*[^\d\s])")
_RE_OPT_GLUE = re.compile(r"(?<=\S)(?=[\(（][B-E][\)）])")

_SUP = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7",
        "⁸": "8", "⁹": "9", "⁺": "+", "⁻": "-", "ⁿ": "n", "ⁱ": "i"}
_SUB = {"₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5", "₆": "6", "₇": "7",
        "₈": "8", "₉": "9", "₊": "+", "₋": "-", "ₐ": "a", "ₑ": "e", "ₒ": "o", "ₓ": "x",
        "ₙ": "n", "ₘ": "m", "ᵢ": "i", "ⱼ": "j"}
_RE_SUP = re.compile("[" + "".join(_SUP) + "]+")
_RE_SUB = re.compile("[" + "".join(_SUB) + "]+")


def normalize_qno(text):
    if _RE_QNO_CANON.match(text):
        return text
    m = _RE_QNO_BAD.match(text)
    if not m:
        return text
    return "( )" + m.group(1) + "." + text[m.end():]


def qno_of_line(text):
    """行首題號（已正規化的 ( )N. 或裸 N.）→ int，否則 None。"""
    m = _RE_QNO_CANON.match(text) or _RE_QNO_BARE.match(text)
    return int(re.search(r"\d+", m.group(0)).group(0)) if m else None


def restore_bare_qnos(texts, prev_qno=None):
    """OCR 把整個「( )」吞掉、只剩 `21.有關…` 時：若題號剛好是上一題 +1，補回 ( )。
    texts 為依閱讀序的行文字（可含佔位／分隔行）；回傳 (新文字, 最後一個題號)。"""
    out, last = [], prev_qno
    for t in texts:
        q = qno_of_line(t)
        if q is not None:
            if last is not None and q == last + 1 and not _RE_QNO_CANON.match(t):
                m = _RE_QNO_BARE.match(t)
                t = "( )" + str(q) + "." + t[m.end():]
            last = q
        out.append(t)
    return out, last


def unicode_scripts_to_tex(text):
    """v₁ → v_{1}、m/s² → m/s^{2}（連續的上/下標字併成一組）。"""
    text = _RE_SUB.sub(lambda m: "_{" + "".join(_SUB[c] for c in m.group(0)) + "}", text)
    return _RE_SUP.sub(lambda m: "^{" + "".join(_SUP[c] for c in m.group(0)) + "}", text)


def split_options(text):
    """同一行黏在一起的 (A)…(B)…(C)… → 在 (B)(C)(D)(E) 前補一個空白。"""
    return _RE_OPT_GLUE.sub(" ", text)


def postprocess_line(text, conf=1.0, low_conf=LOW_CONF):
    text = normalize_qno(text)
    text = unicode_scripts_to_tex(text)
    text = split_options(text)
    if conf < low_conf:
        text += LOW_CONF_MARK
    return text


def ocr_page_ppocr_records(bgr, split_x=None, dpi=300, low_conf=LOW_CONF, prev_qno=None):
    """PP-OCRv5 一頁：整欄辨識 → 圖內文字過濾 → 重組成行 → 後處理。
    回傳 (records, figs)。record = {text, conf, kind('text'|'fig'|'sep'), x0,y0,x1,y1}，
    已依閱讀順序排好（雙欄：先左欄、再右欄，中間一筆 sep）；fig 是「〔含圖 k〕」佔位行。
    figs = [(x0,y0,x1,y1)] 像素座標，編號 k 即為清單順序（由上到下）。"""
    H, W = bgr.shape[:2]
    items = []
    spans = [(0, split_x), (split_x, W)] if split_x else [(0, W)]
    for x0, x1 in spans:
        for it in ppocr_engine.ocr_lines(bgr[:, x0:x1]):
            it["box"] = [[x + x0, y] for x, y in it["box"]]
            items.append(it)
    cands = figure_candidates(bgr, dpi=dpi)
    figs, body, partial = split_figure_text(items, cands, pad=int(0.23 * dpi))
    for it, fig in partial:
        body.extend(_reocr_masked(bgr, it, fig))

    def render(col_items, col_figs):
        lines = group_into_lines(col_items)
        for l in lines:
            l["kind"] = "text"
        for k, f in col_figs:
            lines.append({"text": f"〔含圖 {k}〕", "conf": 1.0, "kind": "fig",
                          "y0": f[1], "y1": f[1], "x0": f[0], "x1": f[2]})
        lines.sort(key=lambda l: (l["y0"] + l["y1"]) / 2)
        for l in lines:
            if l["kind"] == "text":
                l["text"] = postprocess_line(l["text"], l["conf"], low_conf)
        return lines

    numbered = list(enumerate(figs, 1))
    recs = []
    if split_x:
        for side, (x0, x1) in enumerate(spans):
            col_items = [it for it in body if x0 <= _center(it)[0] < x1]
            col_figs = [(k, f) for k, f in numbered if x0 <= (f[0] + f[2]) / 2 < x1]
            col = render(col_items, col_figs)
            if side == 1 and col and recs:
                recs.append({"text": "─── (右欄) ───", "conf": 1.0, "kind": "sep", "x0": 0, "y0": 0, "x1": 0, "y1": 0})
            recs.extend(col)
    else:
        recs = render(body, numbered)
    # 補回被吞掉括號的題號（需看上一題號，故在整頁排好序後處理）
    idx = [i for i, r in enumerate(recs) if r["kind"] == "text"]
    new_t, _ = restore_bare_qnos([recs[i]["text"] for i in idx], prev_qno)
    for i, t in zip(idx, new_t):
        recs[i]["text"] = t
    return recs, figs


def ocr_page_ppocr_lines(bgr, split_x=None, dpi=300, low_conf=LOW_CONF, prev_qno=None):
    """同上，但回傳 (行文字清單, 圖框清單)；欄分隔線前後各留一個空行。"""
    recs, figs = ocr_page_ppocr_records(bgr, split_x, dpi, low_conf, prev_qno)
    lines = []
    for r in recs:
        if r["kind"] == "sep":
            lines += ["", r["text"], ""]
        else:
            lines.append(r["text"])
    return lines, figs


def ocr_page_ppocr(bgr, split_x=None, dpi=300, low_conf=LOW_CONF):
    """PP-OCRv5 吃原圖（BGR，不二值化）。split_x 為原圖座標的欄界。回傳純文字。"""
    lines, _ = ocr_page_ppocr_lines(bgr, split_x, dpi, low_conf)
    return "\n".join(lines)


def ocr_page_tesseract(binimg, lang="chi_tra+eng", split_columns=True):
    """舊版 Tesseract 路徑（備援）。"""
    if pytesseract is None:
        return "[未安裝 pytesseract]"
    cfg = "--oem 1 --psm 6"
    split = find_column_split(binimg) if split_columns else None
    if split:
        left = binimg[:, :split]
        right = binimg[:, split:]
        lt = pytesseract.image_to_string(left, lang=lang, config=cfg)
        rt = pytesseract.image_to_string(right, lang=lang, config=cfg)
        return lt.rstrip() + "\n\n─── (右欄) ───\n\n" + rt
    return pytesseract.image_to_string(binimg, lang=lang, config=cfg)


def ocr_page(binimg, lang="chi_tra+eng", split_columns=True, bgr=None, upscale=1.5, engine="auto", dpi=300):
    """單頁 OCR 入口。engine=auto 時優先 PP-OCRv5（需傳 bgr 原圖），否則 Tesseract。
    binimg 是 preprocess() 的結果（放大 upscale 倍），欄界在其座標系找出再換算回 bgr。"""
    if bgr is not None and resolve_engine(engine) == "ppocr":
        split = find_column_split(binimg) if split_columns else None
        split_x = int(round(split / upscale)) if split else None
        return ocr_page_ppocr(bgr, split_x, dpi)
    return ocr_page_tesseract(binimg, lang=lang, split_columns=split_columns)


# ---------- 文字清理 ----------
def tidy_text(txt):
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in txt.splitlines()]
    out, blank = [], 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank <= 1:
                out.append("")
            continue
        blank = 0
        out.append(ln)
    return "\n".join(out).strip()


# ---------- 主流程 ----------
def process(pdf_path, out_dir, dpi=300, only_page=None, upscale=1.5, lang="chi_tra+eng", engine="auto"):
    os.makedirs(out_dir, exist_ok=True)
    doc = pymupdf.open(pdf_path)
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    all_text, report = [], []
    eng_name = resolve_engine(engine)
    prev_q = None
    for i, page in enumerate(doc):
        n = i + 1
        if only_page and n != only_page:
            continue
        native = page.get_text("text").strip()
        if len(native) > 40:  # 有真文字層：直接用，免 OCR
            all_text.append(f"\n【第 {n} 頁（數位文字層）】\n{native}")
            report.append(f"p{n}: 文字層 {len(native)} 字（免 OCR）")
            continue
        # 掃描頁：render → (PP-OCR：圖區偵測＋圖內文字過濾｜Tesseract：舊版輪廓裁圖) → OCR
        bgr = page_to_bgr(page, dpi)
        binimg = preprocess(bgr, upscale=upscale)
        H, W = bgr.shape[:2]
        t0 = time.time()
        if eng_name == "ppocr":
            split = find_column_split(binimg)
            split_x = int(round(split / upscale)) if split else None
            lines, fig_px = ocr_page_ppocr_lines(bgr, split_x, dpi, prev_qno=prev_q)
            prev_q = next((q for q in (qno_of_line(l) for l in reversed(lines)) if q is not None), prev_q)
            txt = tidy_text("\n".join(lines))
            pad = int(0.17 * dpi)
            figs = [(max(0, x0 - pad) / W, max(0, y0 - pad) / H, min(W, x1 + pad) / W, min(H, y1 + pad) / H)
                    for x0, y0, x1, y1 in fig_px]
        else:
            figs = detect_figures(binimg, page.rect.height)
            txt = tidy_text(ocr_page(binimg, lang=lang, bgr=bgr, upscale=upscale, engine="tesseract"))
        dt = time.time() - t0
        for k, (x0, y0, x1, y1) in enumerate(figs, 1):
            crop = bgr[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)]
            if cv2 is not None and crop.size:
                cv2.imwrite(os.path.join(out_dir, f"{base}_p{n}_fig{k}.png"), crop)
        fig_note = f"（本頁偵測到 {len(figs)} 個圖塊，已裁為 *_p{n}_fig*.png；文中以〔含圖〕標示）" if figs else ""
        all_text.append(f"\n【第 {n} 頁（掃描→OCR）{fig_note}】\n{txt}")
        report.append(f"p{n}: 掃描OCR[{eng_name}]，{len(txt)} 字，{len(figs)} 圖，OCR {dt:.1f}s")
    full = "\n".join(all_text).strip()
    txt_path = os.path.join(out_dir, f"{base}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(full)
    docx_path = write_docx(full, os.path.join(out_dir, f"{base}.docx"))
    return {"txt": txt_path, "docx": docx_path, "report": report}


def write_docx(text, path):
    from docx import Document
    from docx.shared import Pt
    d = Document()
    style = d.styles["Normal"]
    style.font.name = "DFPYuanMedium-B5"
    style.font.size = Pt(12)
    for para in text.split("\n"):
        d.add_paragraph(para)
    d.save(path)
    return path


def main():
    ap = argparse.ArgumentParser(description="純 Python 考卷前處理（PyMuPDF+OpenCV+PP-OCRv5(Tesseract備援)+docx）")
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", default="out", help="輸出資料夾")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--page", type=int, default=None, help="只處理某一頁（除錯用）")
    ap.add_argument("--upscale", type=float, default=1.5)
    ap.add_argument("--lang", default="chi_tra+eng", help="僅 Tesseract 備援使用")
    ap.add_argument("--engine", choices=["auto", "ppocr", "tesseract"], default="auto",
                    help="auto=有 ocr/models 就用 PP-OCRv5，否則 Tesseract")
    args = ap.parse_args()
    r = process(args.pdf, args.out, dpi=args.dpi, only_page=args.page,
                upscale=args.upscale, lang=args.lang, engine=args.engine)
    print("✅ 完成")
    for line in r["report"]:
        print("  ", line)
    print("  TXT :", r["txt"])
    print("  DOCX:", r["docx"])


if __name__ == "__main__":
    sys.exit(main())
