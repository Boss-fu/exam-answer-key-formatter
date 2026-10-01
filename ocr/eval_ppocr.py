#!/usr/bin/env python3
"""本地 OCR 評測迴圈：PP-OCRv5 vs Tesseract（無人工 GT 的量化指標）。

完全本地，不呼叫任何外部 API。沿用 run_eval.py「數字驅動改進」的精神，但：
  * 沒有 ground truth 時，改看「可自動量測的代理指標」（信心、題號/選項/大題偵測、
    題號序列缺號、公式碎片、上下標被拆、簡體殘留、數字黏連、雙欄交錯……）。
  * 同一頁同時跑 PP-OCRv5（rapidocr + ocr/models）與 Tesseract(chi_tra+eng) 對照。
  * 順手用 PP-OCRv5 輸出半自動產一份 .gt.json 「草稿」（needsReview=true，未經人工校正）。

用法：
    python3 ocr/eval_ppocr.py                       # 評測 ocr/testset/*.pdf、*.png
    python3 ocr/eval_ppocr.py --dpi 300             # 換解析度
    python3 ocr/eval_ppocr.py --use-cache           # 重用上次 OCR 原始輸出（只重算指標，開發用）
    python3 ocr/eval_ppocr.py --no-gt-draft         # 不產 GT 草稿
    python3 ocr/eval_ppocr.py --no-tesseract        # 只跑 PP-OCRv5

輸出：ocr/results/ppocr_eval.json、ocr/results/ppocr_eval.md
GT 草稿：ocr/testset/<名稱>.gt.json（已存在且不是自動草稿時，絕不覆蓋，避免毀掉人工校正）
"""
import argparse
import glob
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")
TESTSET = os.path.join(HERE, "testset")
RESULTS = os.path.join(HERE, "results")
CACHE = os.path.join(RESULTS, ".cache")
LOW_CONF = 0.8

# ---------------------------------------------------------------- regex 規則
# 題號：行首 ( )N. 。容忍 OCR 把括號讀成 ) / (.) / 0，以及括號內被手寫填了選項字母 (C)6.
_QNO_PREFIX = (r"(?:[\(（\[]\s*[A-Ea-e.·\s]?\s*[\)）\]]|[\(（\)）]|[0Oo](?=\d))?")
RE_QNO = re.compile(r"^\s*" + _QNO_PREFIX + r"\s*(\d{1,2})\s*[\.．、](?=\s*[^\d\s])")
RE_QNO_CANON = re.compile(r"^\s*[\(（]\s*[\)）]\s*\d{1,2}\s*[\.．、]")          # 乾淨的 ()N.
RE_QNO_HANDWRITTEN = re.compile(r"^\s*[\(（]\s*[A-Ea-e]\s*[\)）]\s*\d{1,2}\s*[\.．、]")  # (C)6.
RE_OPT = re.compile(r"[\(（]\s*([A-E])\s*[\)）]")
# 選項字母被讀成小寫/數字/相近字：(a) (v) (0) (Cc)；排除 (mm) (X)= 之類的單位與符號
RE_OPT_BAD = re.compile(r"(?<![A-Za-z0-9一-鿿])[\(（]\s*(?:[a-eoOvVlI0-9]|[A-E][a-e])\s*[\)）](?!\s*[=＝])")
RE_SECTION = re.compile(r"^\s*[一二三四五六七八九十]+\s*[、．.]\s*.{0,10}?[題题]")
RE_GROUP = re.compile(r"(?:第\s*)?(\d{1,2})\s*[-~～－–—至到]\s*(\d{1,2})\s*[題题](?:組|组)?")
RE_POINTS = re.compile(r"每[題题]\s*(\d{1,3})\s*分")
RE_FORMULA_FRAG = re.compile(r"\$|\\[A-Za-z]+|[{}]")
RE_SUBSUP_UNI = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉ₐₑₒₓ]")
RE_NUM_ONLY = re.compile(r"^[\d\s\.\-+−·]+$")
RE_GLUED = re.compile(r"(?<![\d.])\d{6,}(?![\d.])")   # 無小數點的 6 位以上整數 ＝ 多個刻度黏在一起
RE_CJK = re.compile(r"[一-鿿]")
RE_HANDWRITE_HINT = RE_QNO_HANDWRITTEN

KEYWORDS = ["速度", "加速度", "位移", "公尺", "公分", "下列", "何者", "正確", "選擇", "質點", "時間",
            "距離", "重力", "物體", "運動", "測量", "不確定度", "斜面", "拋", "試求", "下圖", "如圖",
            "學年度", "段考", "物理", "初速", "等速", "直線"]

_ALLOWED = re.compile(r"[一-鿿A-Za-z0-9Ͱ-Ͽ\s.,;:!?()\[\]{}+\-=<>/%°±×÷²³·、。，；：？！（）「」『』《》…～~—－_'\"$\\√∝≠≤≥∼→←↑↓∆Δ⁰¹⁴-₉]")


# ---------------------------------------------------------------- OCR 引擎
def render_pages(path, dpi):
    """PDF/影像 → 灰階 ndarray 清單。"""
    import cv2
    if path.lower().endswith(".pdf"):
        import pymupdf
        doc = pymupdf.open(path)
        pages = []
        for p in doc:
            pix = p.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
            pages.append(np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w).copy())
        return pages
    img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE)
    return [img]


def _box_xyxy(box):
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))]


class PPOCR:
    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        self.eng = RapidOCR(det_model_path=os.path.join(MODELS, "det.onnx"),
                            rec_model_path=os.path.join(MODELS, "rec.onnx"),
                            rec_keys_path=os.path.join(MODELS, "ppocrv5_dict.txt"))
        self.warmup_s = None

    def warmup(self):
        t = time.time()
        self.eng(np.full((200, 600, 3), 255, np.uint8))
        self.warmup_s = time.time() - t

    def run(self, gray):
        import cv2
        t = time.time()
        res, _ = self.eng(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
        dt = time.time() - t
        lines = [{"raw": str(txt), "conf": float(c), "bbox": _box_xyxy(b)} for b, txt, c in (res or [])]
        return lines, dt


def run_tesseract(gray):
    import pytesseract
    t = time.time()
    d = pytesseract.image_to_data(gray, lang="chi_tra+eng", config="--psm 3",
                                  output_type=pytesseract.Output.DICT)
    dt = time.time() - t
    groups = defaultdict(list)
    for i, txt in enumerate(d["text"]):
        if not str(txt).strip() or float(d["conf"][i]) < 0:
            continue
        groups[(d["block_num"][i], d["par_num"][i], d["line_num"][i])].append(i)
    lines = []
    for idxs in groups.values():
        idxs.sort(key=lambda i: d["left"][i])
        txt = " ".join(str(d["text"][i]) for i in idxs)
        txt = re.sub(r"(?<=[一-鿿＀-￯，。、：；（）]) +(?=[一-鿿＀-￯，。、：；（）])", "", txt)
        txt = re.sub(r"(?<=[一-鿿]) +(?=[一-鿿])", "", txt)
        confs = [float(d["conf"][i]) / 100.0 for i in idxs]
        x0 = min(d["left"][i] for i in idxs)
        y0 = min(d["top"][i] for i in idxs)
        x1 = max(d["left"][i] + d["width"][i] for i in idxs)
        y1 = max(d["top"][i] + d["height"][i] for i in idxs)
        lines.append({"raw": txt, "conf": sum(confs) / len(confs), "bbox": [float(x0), float(y0), float(x1), float(y1)]})
    # tesseract 已是版面順序（block/par/line），保持原樣
    return lines, dt


_cc = None


def to_trad(s):
    global _cc
    if _cc is None:
        try:
            import opencc
            _cc = opencc.OpenCC("s2twp")
        except Exception:
            _cc = False
    return _cc.convert(s) if _cc else s


_cc_s2t = None


def residual_simplified(s):
    """已轉繁後仍會被 s2t 改動的字（殘留簡體；s2twp 之外的簡體字）。"""
    global _cc_s2t
    if _cc_s2t is None:
        import opencc
        _cc_s2t = opencc.OpenCC("s2t")
    return [c for c in s if RE_CJK.match(c) and _cc_s2t.convert(c) != c]


_cc_tw = None


def to_trad_tw(s):
    """只做字形轉換、不做台灣用語替換（s2tw）；用於 GT 草稿，避免 s2twp 把『通過→透過、拋→丟擲』這類用語改掉。"""
    global _cc_tw
    if _cc_tw is None:
        import opencc
        _cc_tw = opencc.OpenCC("s2tw")
    return _cc_tw.convert(s)


def overreach_diffs(tw, twp):
    """s2tw 與 s2twp 的差異片段（s2twp 的詞彙替換）。"""
    import difflib
    out = []
    sm = difflib.SequenceMatcher(None, tw, twp, autojunk=False)
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op != "equal":
            out.append(f"{tw[a0:a1]}→{twp[b0:b1]}")
    return out


def finalize(lines):
    for ln in lines:
        ln["text"] = to_trad(ln["raw"]).strip()
        ln["text_tw"] = to_trad_tw(ln["raw"]).strip()
        ln["was_simplified"] = ln["text"] != ln["raw"].strip()
    return [l for l in lines if l["text"]]


# ---------------------------------------------------------------- 版面（雙欄）
def column_split(lines, width):
    """回傳雙欄分隔 x；單欄回傳 None。找中央區帶內「沒有任何框覆蓋」的最寬空隙。"""
    cov = np.zeros(int(width) + 1, bool)
    for ln in lines:
        x0, _, x1, _ = ln["bbox"]
        if (x1 - x0) > 0.6 * width:
            continue
        cov[int(max(0, x0)):int(min(width, x1)) + 1] = True
    lo, hi = int(width * 0.35), int(width * 0.65)
    best, cur_start, best_rng = 0, None, None
    for x in range(lo, hi + 1):
        if not cov[x]:
            cur_start = x if cur_start is None else cur_start
            if x - cur_start + 1 > best:
                best, best_rng = x - cur_start + 1, (cur_start, x)
        else:
            cur_start = None
    if best < 0.008 * width or not best_rng:
        return None
    return (best_rng[0] + best_rng[1]) / 2.0


def side_of(ln, mid, width):
    x0, _, x1, _ = ln["bbox"]
    if x0 < mid < x1 and (x1 - x0) > 0.35 * width:
        return "S"  # 跨欄（標題等）
    return "L" if (x0 + x1) / 2 < mid else "R"


def order_two_column(lines, width):
    """單趟閱讀序（OCR 預設是上到下，雙欄會左右交錯）→ 先上方跨欄標題，再左欄，再右欄。"""
    mid = column_split(lines, width)
    if mid is None:
        return sorted(lines, key=lambda l: (l["bbox"][1], l["bbox"][0])), None
    sides = {id(l): side_of(l, mid, width) for l in lines}
    cols = [l for l in lines if sides[id(l)] != "S"]
    first_col_y = min((l["bbox"][1] for l in cols), default=1e9)
    head = [l for l in lines if sides[id(l)] == "S" and l["bbox"][1] <= first_col_y + 5]
    rest_span = [l for l in lines if sides[id(l)] == "S" and l not in head]
    out = sorted(head, key=lambda l: l["bbox"][1])
    for s in ("L", "R"):
        col = [l for l in cols + rest_span if (sides[id(l)] == s or (sides[id(l)] == "S" and s == "L"))]
        hs = [l["bbox"][3] - l["bbox"][1] for l in col] or [20]
        tol = 0.5 * statistics.median(hs)
        col.sort(key=lambda l: l["bbox"][1])
        rows, cur = [], []
        for l in col:
            cy = (l["bbox"][1] + l["bbox"][3]) / 2
            if cur and abs(cy - cur_cy) > tol:
                rows.append(cur)
                cur = []
            if not cur:
                cur_cy = cy
            cur.append(l)
        if cur:
            rows.append(cur)
        for r in rows:
            out.extend(sorted(r, key=lambda l: l["bbox"][0]))
    return out, mid


def alternations(lines, width):
    """原始輸出順序中，左右欄來回切換的次數（理想單趟閱讀序為 1）。"""
    mid = column_split(lines, width)
    if mid is None:
        return None
    seq = [side_of(l, mid, width) for l in lines]
    seq = [s for s in seq if s != "S"]
    return sum(1 for a, b in zip(seq, seq[1:]) if a != b)


# ---------------------------------------------------------------- 指標
def qno_of(text):
    m = RE_QNO.match(text)
    return int(m.group(1)) if m else None


def readable_stats(lines):
    txt = "".join(l["text"] for l in lines)
    ns = re.sub(r"\s", "", txt)
    n = max(1, len(ns))
    cjk = len(RE_CJK.findall(ns))
    ok = len(_ALLOWED.findall(ns))
    kw = sum(txt.count(k) for k in KEYWORDS)
    kw_distinct = sum(1 for k in KEYWORDS if k in txt)
    return {"chars": len(ns), "cjk_chars": cjk, "cjk_ratio": round(cjk / n, 4),
            "readable_ratio": round(ok / n, 4), "keyword_hits": kw, "keyword_distinct": kw_distinct}


def structure_stats(lines):
    qnos = [(qno_of(l["text"]), l) for l in lines]
    qnos = [(n, l) for n, l in qnos if n is not None]
    nums = [n for n, _ in qnos]
    opt_counts = Counter()
    for l in lines:
        for m in RE_OPT.finditer(l["text"]):
            opt_counts[m.group(1)] += 1
    sections = [l["text"] for l in lines if RE_SECTION.match(l["text"])]
    groups = []
    for l in lines:
        m = RE_GROUP.search(l["text"])
        if m and re.match(r"^\s*(?:第\s*)?\d", l["text"]) and ("題" in l["text"] or "题" in l["text"]):
            groups.append(f"{m.group(1)}-{m.group(2)}")
    points = []
    for l in lines:
        m = RE_POINTS.search(l["text"])
        if m:
            points.append(int(m.group(1)))
    inv = sum(1 for a, b in zip(nums, nums[1:]) if b < a)
    return {"qno_count": len(nums), "qnos": nums, "qno_order_inversions": inv,
            "option_markers": dict(sorted(opt_counts.items())), "option_marker_total": sum(opt_counts.values()),
            "sections": sections, "groups": groups, "points_markers": points}


def detect_patterns(lines, width):
    """回傳 {pattern: [examples...]}（每個 example 為 dict：text, conf）。"""
    pats = defaultdict(list)
    hs = [l["bbox"][3] - l["bbox"][1] for l in lines if l["bbox"][3] > l["bbox"][1]]
    med_h = statistics.median(hs) if hs else 20

    def add(k, l, note=None):
        e = {"text": l["text"][:80], "conf": round(l["conf"], 2)}
        if note:
            e["note"] = note
        pats[k].append(e)

    for l in lines:
        t = l["text"]
        if RE_FORMULA_FRAG.search(t):
            add("formula_fragment", l)
        if RE_SUBSUP_UNI.search(t):
            add("subsup_unicode", l)
        if l.get("was_simplified"):
            add("simplified_raw", l, "模型輸出簡體(已由 OpenCC 轉繁)")
        if l.get("text_tw") and l["text_tw"] != t:
            add("opencc_s2twp_overreach", l, ", ".join(overreach_diffs(l["text_tw"], t))[:60])
        res = residual_simplified(t)
        if res:
            add("simplified_residual", l, "".join(sorted(set(res))))
        if RE_GLUED.search(t) or (RE_NUM_ONLY.match(t) and len(t.split()) >= 3):
            add("digit_glue_or_axis_run", l)
        n = qno_of(t)
        if n is not None and not RE_QNO_CANON.match(t):
            add("handwriting_in_paren" if RE_QNO_HANDWRITTEN.match(t) else "qno_bracket_noise", l)
        if RE_OPT_BAD.search(t):
            add("option_letter_misread", l)
        if l["conf"] < LOW_CONF:
            add("low_conf", l)
    # 上下標/分式被拆：小框（高 < 0.65×中位高）緊貼較大框，且垂直中心明顯偏移
    big = [l for l in lines if (l["bbox"][3] - l["bbox"][1]) >= 0.8 * med_h]
    for l in lines:
        x0, y0, x1, y1 = l["bbox"]
        h = y1 - y0
        if h <= 0 or h >= 0.65 * med_h or len(l["text"]) > 6 or not re.fullmatch(r"[A-Za-z0-9]+", l["text"]):
            continue
        cy = (y0 + y1) / 2
        for b in big:
            bx0, by0, bx1, by1 = b["bbox"]
            bh = by1 - by0
            gap = max(bx0 - x1, x0 - bx1, 0)
            overlap_y = min(y1, by1) - max(y0, by0)
            if gap <= 0.6 * bh and overlap_y > 0 and abs(cy - (by0 + by1) / 2) > 0.2 * bh and b is not l:
                add("subsup_or_fraction_split", l, f"鄰框: {b['text'][:20]}")
                break
    return pats


def page_metrics(lines, width, dt, ordered=None):
    confs = [l["conf"] for l in lines]
    low = [l for l in lines if l["conf"] < LOW_CONF]
    st = structure_stats(lines)
    out = {
        "lines": len(lines),
        "conf_mean": round(statistics.mean(confs), 4) if confs else None,
        "conf_median": round(statistics.median(confs), 4) if confs else None,
        "low_conf_lines": len(low),
        "low_conf_samples": [{"text": l["text"][:70], "conf": round(l["conf"], 2)}
                             for l in sorted(low, key=lambda x: x["conf"])[:40]],
        "seconds": round(dt, 2),
        "col_alternations_raw_order": alternations(lines, width),
    }
    out.update(readable_stats(lines))
    out.update(st)
    if ordered is not None:
        out["qno_order_inversions_after_column_split"] = structure_stats(ordered)["qno_order_inversions"]
    return out


def sequence_gaps(nums, relative=False):
    """缺號。relative=True 時只看該頁 min..max 之間（單頁不可能含 1..N 全部）。"""
    if not nums:
        return {"max": None, "missing": [], "duplicates": []}
    mx = max(nums)
    lo = min(nums) if relative else 1
    miss = [n for n in range(lo, mx + 1) if n not in set(nums)]
    dup = sorted(n for n, c in Counter(nums).items() if c > 1)
    return {"max": mx, "missing": miss, "duplicates": dup}


# ---------------------------------------------------------------- GT 草稿
def join_text(parts):
    out = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and re.search(r"[A-Za-z0-9.]$", out) and re.match(r"[A-Za-z0-9]", p):
            out += " "
        out += p
    return out


def parse_meta(first_lines):
    meta = {"school": None, "year": None, "term": None, "grade": None, "subject": None, "examName": None}
    txt = " ".join(l["text"] for l in first_lines[:4])
    m = re.search(r"([一-鿿]{2,12}?(?:高級中學|高級中學|高中|國民中學|國中|中學))", txt)
    if m:
        meta["school"] = m.group(1)
    m = re.search(r"(\d{2,3})\s*學年度", txt)
    if m:
        meta["year"] = m.group(1)
    m = re.search(r"第\s*([一二])\s*學期", txt)
    if m:
        meta["term"] = {"一": "上", "二": "下"}[m.group(1)]
    m = re.search(r"(高[一二三]|國[一二三]|[七八九]年級)", txt)
    if m:
        meta["grade"] = m.group(1)
    m = re.search(r"(第[一二三四五六七八九十]+次(?:段考|期中考|期末考|月考)|期中考|期末考|段考)", txt)
    if m:
        meta["examName"] = m.group(1)
    m = re.search(r"(?:段考|期中考|期末考|月考)\s*([一-鿿]{2,4}?)\s*(?:科)?(?:試題|試卷|考卷)", txt)
    if m:
        meta["subject"] = m.group(1)
    return meta


def section_type(text):
    if re.search(r"多重|多選", text):
        return "multiple"
    if re.search(r"填充|填空", text):
        return "fill"
    if re.search(r"是非", text):
        return "truefalse"
    if re.search(r"非選|計算|問答|簡答", text):
        return "essay"
    if re.search(r"單一|單選|選擇", text):
        return "single"
    return None


def split_options(body):
    """把題目全文切成 stem + options。空括號 () 出現在已有選項之後時，依序推定為下一個字母。"""
    marks = []
    last = None
    for m in re.finditer(r"[\(（]\s*([A-E]?)\s*[\)）]", body):
        lab = m.group(1)
        if lab:
            marks.append((m.start(), m.end(), lab, False))
            last = lab
        elif last and last < "E":
            nxt = chr(ord(last) + 1)
            marks.append((m.start(), m.end(), nxt, True))
            last = nxt
    # 至少要有 2 個選項標記才當選擇題，避免題幹中的 (A) 單獨出現
    if len(marks) < 2:
        return body.strip(), [], False
    stem = body[:marks[0][0]].strip()
    opts, guessed = [], False
    for i, (s, e, lab, g) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        opts.append({"label": lab, "text": body[e:end].strip()})
        guessed = guessed or g
    return stem, opts, guessed


def build_gt_draft(stem_name, pages_ordered, engine_note):
    """pages_ordered: [ordered_lines_of_page, ...]（已套雙欄閱讀序、已轉繁）"""
    all_lines = [l for p in pages_ordered for l in p]
    meta = parse_meta(pages_ordered[0] if pages_ordered else [])
    questions, cur = [], None
    section, sec_points, sec_text = None, None, None
    seen = set()

    def flush():
        nonlocal cur
        if cur is None:
            return
        body = join_text(cur["parts"])
        stem, opts, guessed = split_options(body)
        typ = cur["section_type"] or ("single" if opts else "essay")
        if typ in ("single", "multiple") and not opts:
            typ = "essay" if cur["section_type"] is None else typ
        has_fig = bool(re.search(r"如圖|下圖|附圖|圖所示|圖形|如下表|下表|如表|圖[一二三四五六七八九十\d]", body))
        questions.append({
            "no": cur["no"], "type": typ, "section": cur["section"],
            "stem": stem, "options": opts, "answer": None, "points": cur["points"],
            "hasFigure": has_fig,
            "figureNote": "（自動判定：題幹提及圖表，未描述內容）" if has_fig else None,
            "needsReview": True,
        })
        cur = None

    for l in all_lines:
        t = l["text"]
        if RE_SECTION.match(t) and not qno_of(t):
            flush()
            sec_text = t
            section = re.sub(r"[：:].*$", "", t).strip()
            sec_points = None
            m = RE_POINTS.search(t)
            if m:
                sec_points = int(m.group(1))
            continue
        gm = RE_GROUP.search(t)
        if gm and re.match(r"^\s*\d", t) and len(t) < 14 and qno_of(t) is None:
            flush()
            continue
        n = qno_of(t)
        if n is not None and n not in seen and 1 <= n <= 80:
            flush()
            seen.add(n)
            m = RE_QNO.match(t)
            cur = {"no": n, "parts": [t[m.end():].strip()], "section": section,
                   "section_type": section_type(sec_text or ""), "points": sec_points}
            continue
        if cur is None:
            continue
        # 圖表刻度類雜訊（純數字短行）不併入題幹
        if RE_NUM_ONLY.match(t) and len(t) <= 14:
            continue
        cur["parts"].append(t)
    flush()
    return {
        "_draft": {
            "status": "AUTO_DRAFT_UNVERIFIED",
            "note": "由 PP-OCRv5 半自動產生的草稿，尚未人工校正；不可當作標準答案計算準確率，"
                    "需逐題核對 stem/options/type/points 後移除此欄位與各題 needsReview。",
            "engine": engine_note,
            "missing_question_numbers": [n for n in range(1, max([q["no"] for q in questions] or [0]) + 1)
                                         if n not in {q["no"] for q in questions}],
            "answers": "answer 一律為 null（卷上括號內的字母多為學生手寫作答，不是標準答案）。",
        },
        "meta": meta,
        "questions": sorted(questions, key=lambda q: q["no"]),
    }


def write_gt_draft(stem_name, draft):
    path = os.path.join(TESTSET, stem_name + ".gt.json")
    if os.path.exists(path):
        try:
            old = json.load(open(path, encoding="utf-8"))
        except Exception:
            old = None
        if not (isinstance(old, dict) and (old.get("_draft") or {}).get("status") == "AUTO_DRAFT_UNVERIFIED"):
            print(f"  [skip] {path} 已存在且非自動草稿，不覆蓋", file=sys.stderr)
            return None
    with open(path, "w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


# ---------------------------------------------------------------- 主流程
PATTERN_LABELS = {
    "formula_fragment": "公式碎片（含 $、\\指令、{} 的亂碼）",
    "subsup_unicode": "上下標被辨成 Unicode 小字（v₁、²，LaTeX 化時易丟失）",
    "subsup_or_fraction_split": "上下標／分式被拆成獨立小框",
    "simplified_raw": "模型輸出簡體（需 OpenCC 轉繁）",
    "simplified_residual": "轉繁後仍殘留簡體字",
    "digit_glue_or_axis_run": "數字黏連／圖表刻度合併成同一框",
    "qno_bracket_noise": "題號括號被雜訊污染（) / (.) / 0 開頭）",
    "handwriting_in_paren": "題號括號內有手寫作答字母（如 (C)6.）",
    "option_letter_misread": "選項字母誤認（(a) (v) (Cc) (0)…）",
    "opencc_s2twp_overreach": "OpenCC s2twp 詞彙替換過頭（通過→透過、拋→丟擲、高級→高階、函數→函式、循環→迴圈）",
    "low_conf": f"低信心行（<{LOW_CONF}）",
}


def load_or_run(path, dpi, ppocr, use_tess, use_cache):
    stem = os.path.splitext(os.path.basename(path))[0]
    os.makedirs(CACHE, exist_ok=True)
    cpath = os.path.join(CACHE, f"{stem}.dpi{dpi}.json")
    if use_cache and os.path.exists(cpath):
        return json.load(open(cpath, encoding="utf-8")), None
    pages = render_pages(path, dpi)
    out = {"dpi": dpi, "pages": []}
    for i, g in enumerate(pages):
        print(f"  page {i + 1}/{len(pages)} PP-OCRv5 ...", file=sys.stderr, flush=True)
        pl, pdt = ppocr.run(g)
        rec = {"w": g.shape[1], "h": g.shape[0], "pp": {"lines": pl, "seconds": pdt}}
        if use_tess:
            print(f"  page {i + 1}/{len(pages)} Tesseract ...", file=sys.stderr, flush=True)
            tl, tdt = run_tesseract(g)
            rec["tess"] = {"lines": tl, "seconds": tdt}
        out["pages"].append(rec)
    json.dump(out, open(cpath, "w", encoding="utf-8"), ensure_ascii=False)
    return out, True


def aggregate_patterns(per_engine_patterns):
    """{engine: {pattern: [examples]}} → 表格資料"""
    return {eng: {p: len(v) for p, v in pats.items()} for eng, pats in per_engine_patterns.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--testset", default=TESTSET)
    ap.add_argument("--use-cache", action="store_true", help="重用 results/.cache 的 OCR 原始輸出")
    ap.add_argument("--no-tesseract", action="store_true")
    ap.add_argument("--no-gt-draft", action="store_true")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.testset, "*.pdf")) +
                   glob.glob(os.path.join(args.testset, "*.png")) +
                   glob.glob(os.path.join(args.testset, "*.jpg")))
    # 多頁 png（name.1.png）只取一份名稱即可，這裡簡化：只處理 pdf 與單張圖
    files = [f for f in files if not re.search(r"\.\d+\.(png|jpg)$", f)]
    if not files:
        print("ocr/testset/ 沒有 PDF/圖片，請先放入測試考卷。", file=sys.stderr)
        sys.exit(1)

    ppocr = None
    if not args.use_cache:
        ppocr = PPOCR()
        ppocr.warmup()

    os.makedirs(RESULTS, exist_ok=True)
    report = {"dpi": args.dpi, "low_conf_threshold": LOW_CONF, "warmup_seconds": getattr(ppocr, "warmup_s", None),
              "docs": [], "summary": {}}
    agg_pat = {"pp": Counter(), "tess": Counter()}
    pat_examples_all_overreach = []
    pat_examples = {"pp": defaultdict(list), "tess": defaultdict(list)}

    for path in files:
        stem = os.path.splitext(os.path.basename(path))[0]
        print(f"[{stem}]", file=sys.stderr)
        data, fresh = load_or_run(path, args.dpi, ppocr, not args.no_tesseract, args.use_cache)
        doc = {"name": stem, "pages": [], "engines": {}}
        pp_ordered_pages = []
        eng_all = {"pp": {"qnos": [], "secs": 0.0, "lines": 0}, "tess": {"qnos": [], "secs": 0.0, "lines": 0}}
        for pi, pg in enumerate(data["pages"]):
            W = pg["w"]
            page_rec = {"page": pi + 1}
            for eng in ("pp", "tess"):
                if eng not in pg:
                    continue
                lines = finalize(pg[eng]["lines"])
                ordered = None
                if eng == "pp":
                    ordered, mid = order_two_column(lines, W)
                    pp_ordered_pages.append(ordered)
                    page_rec["column_split_x"] = mid
                m = page_metrics(lines, W, pg[eng]["seconds"], ordered)
                m["qno_gaps"] = sequence_gaps(m["qnos"], relative=True)
                page_rec[eng] = m
                pats = detect_patterns(lines, W)
                m["patterns"] = {k: len(v) for k, v in pats.items()}
                for k, v in pats.items():
                    agg_pat[eng][k] += len(v)
                    for e in v:
                        if eng == "pp" and k == "opencc_s2twp_overreach":
                            pat_examples_all_overreach.extend(x for x in e.get("note", "").split(", ") if x)
                        if len(pat_examples[eng][k]) < 8:
                            pat_examples[eng][k].append(dict(e, doc=stem, page=pi + 1))
                eng_all[eng]["qnos"] += m["qnos"]
                eng_all[eng]["secs"] += pg[eng]["seconds"]
                eng_all[eng]["lines"] += len(lines)
                if eng == "pp":
                    # 雙欄順序後的欄內交錯 = 0 為理想；alternations_raw 表示未切欄時的亂序程度
                    pass
            doc["pages"].append(page_rec)
        for eng, a in eng_all.items():
            if a["lines"]:
                doc["engines"][eng] = {"qno_total": len(a["qnos"]), "qno_gaps": sequence_gaps(a["qnos"]),
                                       "seconds_total": round(a["secs"], 2)}
        if pp_ordered_pages and not args.no_gt_draft:
            note = f"PP-OCRv5 (rapidocr_onnxruntime, ocr/models) dpi={args.dpi} + OpenCC s2tw（字形轉換，不做用語替換）+ 雙欄切欄閱讀序"
            tw_pages = [[dict(l, text=l["text_tw"]) for l in pg_] for pg_ in pp_ordered_pages]
            draft = build_gt_draft(stem, tw_pages, note)
            gp = write_gt_draft(stem, draft)
            doc["gt_draft"] = {"path": os.path.relpath(gp, os.path.dirname(HERE)) if gp else None,
                               "questions": len(draft["questions"]),
                               "qnos": [q["no"] for q in draft["questions"]]}
        report["docs"].append(doc)

    # ---- 彙總
    def tot(eng, key):
        return sum(p[eng][key] for d in report["docs"] for p in d["pages"] if eng in p)

    summ = {}
    for eng in ("pp", "tess"):
        if not any(eng in p for d in report["docs"] for p in d["pages"]):
            continue
        ps = [p[eng] for d in report["docs"] for p in d["pages"] if eng in p]
        confs = [p["conf_mean"] for p in ps if p["conf_mean"] is not None]
        chars = sum(p["chars"] for p in ps)
        summ[eng] = {
            "pages": len(ps),
            "lines": sum(p["lines"] for p in ps),
            "conf_mean_of_pages": round(statistics.mean(confs), 4) if confs else None,
            "conf_median_of_pages": round(statistics.median([p["conf_median"] for p in ps if p["conf_median"] is not None]), 4) if confs else None,
            "low_conf_lines": sum(p["low_conf_lines"] for p in ps),
            "qno_count": sum(p["qno_count"] for p in ps),
            "option_marker_total": sum(p["option_marker_total"] for p in ps),
            "sections": sum(len(p["sections"]) for p in ps),
            "groups": sum(len(p["groups"]) for p in ps),
            "points_markers": sum(len(p["points_markers"]) for p in ps),
            "cjk_chars": sum(p["cjk_chars"] for p in ps),
            "readable_ratio": round(sum(p["readable_ratio"] * p["chars"] for p in ps) / max(1, chars), 4),
            "keyword_hits": sum(p["keyword_hits"] for p in ps),
            "qno_order_inversions_raw": sum(p["qno_order_inversions"] for p in ps),
            "col_alternations_raw_order": sum((p["col_alternations_raw_order"] or 0) for p in ps),
            "seconds_total": round(sum(p["seconds"] for p in ps), 2),
            "seconds_per_page": round(statistics.mean([p["seconds"] for p in ps]), 2),
            "patterns": dict(agg_pat[eng]),
        }
        if eng == "pp":
            summ[eng]["qno_order_inversions_after_column_split"] = sum(
                p.get("qno_order_inversions_after_column_split", 0) for p in ps)
    report["summary"] = summ

    # Top 錯誤型態（以 PP-OCRv5 為主；排除純資訊類 low_conf 與 simplified_raw 另列）
    top = []
    INFO = {"simplified_raw", "low_conf"}
    for k, c in agg_pat["pp"].most_common():
        if k in INFO:
            continue
        top.append({"pattern": k, "label": PATTERN_LABELS.get(k, k), "pp_count": c,
                    "tess_count": agg_pat["tess"].get(k, 0), "examples": pat_examples["pp"][k][:6]})
    report["top_error_patterns"] = top
    report["info_counts"] = {k: agg_pat["pp"].get(k, 0) for k in INFO}
    ov = Counter()
    for e in pat_examples_all_overreach:
        ov[e] += 1
    report["opencc_overreach_diffs"] = dict(ov.most_common())
    report["tess_pattern_examples"] = {k: v[:4] for k, v in pat_examples["tess"].items()}

    with open(os.path.join(RESULTS, "ppocr_eval.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(os.path.join(RESULTS, "ppocr_eval.md"), "w", encoding="utf-8") as f:
        f.write(render_md(report))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("\n報告：", os.path.join(RESULTS, "ppocr_eval.md"), file=sys.stderr)


# ---------------------------------------------------------------- Markdown
def render_md(r):
    S = r["summary"]
    pp, ts = S.get("pp"), S.get("tess")
    L = []
    L.append("# 本地 OCR 評測：PP-OCRv5 vs Tesseract\n")
    L.append(f"- 解析度 {r['dpi']} dpi；低信心門檻 < {r['low_conf_threshold']}；全部本地推論，無外部 API。")
    L.append("- **沒有人工 GT**，以下皆為「無 GT 代理指標」：可偵測題號/選項愈多、題號序列缺號愈少、信心愈高、碎片愈少，通常愈好；"
             "但不等於準確率。要算準確率需先把 `*.gt.json` 草稿人工校正。")
    if r.get("warmup_seconds"):
        L.append(f"- PP-OCRv5 冷啟動（載入後第一次推論）約 {r['warmup_seconds']:.1f}s，已在計時前暖機。")
    L.append("")
    L.append("## 1. 整體對照\n")
    if pp:
        rows = [
            ("頁數", "pages", "{}"), ("文字行數", "lines", "{}"), ("平均信心(頁均)", "conf_mean_of_pages", "{:.3f}"),
            ("信心中位數(頁中位)", "conf_median_of_pages", "{:.3f}"), ("低信心行數", "low_conf_lines", "{}"),
            ("偵測到題號數", "qno_count", "{}"), ("選項標記 (A)~(E) 數", "option_marker_total", "{}"),
            ("大題標題數（一、單選題…）", "sections", "{}"), ("題組標記數（第N~M題）", "groups", "{}"),
            ("配分標記（每題N分）", "points_markers", "{}"),
            ("中文字數", "cjk_chars", "{}"), ("可讀字元比例", "readable_ratio", "{:.3f}"),
            ("領域關鍵詞命中次數", "keyword_hits", "{}"),
            ("原始順序下題號倒退次數", "qno_order_inversions_raw", "{}"),
            ("原始順序左右欄來回切換", "col_alternations_raw_order", "{}"),
            ("總耗時(s)", "seconds_total", "{}"), ("每頁耗時(s)", "seconds_per_page", "{}"),
        ]
        L.append("| 指標 | PP-OCRv5 | Tesseract |")
        L.append("|---|---:|---:|")
        for name, k, fmt in rows:
            a = fmt.format(pp[k]) if pp.get(k) is not None else "-"
            b = fmt.format(ts[k]) if ts and ts.get(k) is not None else "-"
            L.append(f"| {name} | {a} | {b} |")
        if "qno_order_inversions_after_column_split" in pp:
            L.append(f"| 套雙欄切欄後題號倒退次數 | {pp['qno_order_inversions_after_column_split']} | - |")
        L.append("")
        L.append("> 注意：Tesseract 的「題號數」是用同一組 regex 掃它的輸出行；它把雙欄行混在一起時，"
                 "題號可能被吃進行中而偵測不到。「可讀字元比例」= 中英數與常見符號佔非空白字元比例（亂碼符號愈多愈低）。\n")
    L.append("## 2. 每份／每頁明細\n")
    for d in r["docs"]:
        L.append(f"### {d['name']}\n")
        L.append("| 頁 | 引擎 | 行數 | 平均信心 | 低信心 | 題號數 | 題號清單 | 缺號 | 選項標記 | 耗時(s) |")
        L.append("|---|---|---:|---:|---:|---:|---|---|---:|---:|")
        for p in d["pages"]:
            for eng, nm in (("pp", "PP-OCRv5"), ("tess", "Tesseract")):
                if eng not in p:
                    continue
                m = p[eng]
                L.append(f"| {p['page']} | {nm} | {m['lines']} | {('%.3f' % m['conf_mean']) if m['conf_mean'] is not None else '-（空白頁）'} | {m['low_conf_lines']} | "
                         f"{m['qno_count']} | {','.join(map(str, m['qnos']))} | {','.join(map(str, m['qno_gaps']['missing'])) or '-'} | "
                         f"{m['option_marker_total']} | {m['seconds']} |")
        for eng, nm in (("pp", "PP-OCRv5"), ("tess", "Tesseract")):
            e = d["engines"].get(eng)
            if e:
                g = e["qno_gaps"]
                L.append(f"\n- **{nm} 全卷**：題號 {e['qno_total']} 個，最大題號 {g['max']}，"
                         f"序列缺號 {g['missing'] or '無'}，重複 {g['duplicates'] or '無'}，耗時 {e['seconds_total']}s")
        if d.get("gt_draft"):
            g = d["gt_draft"]
            L.append(f"- GT 草稿：`{g['path']}`（{g['questions']} 題，needsReview=true，**未人工校正**）" if g["path"]
                     else "- GT 草稿：已存在人工檔，未覆蓋")
        L.append("")
    L.append("## 3. 最常見錯誤型態（PP-OCRv5，依出現次數）\n")
    L.append("| # | 型態 | PP-OCRv5 | Tesseract | 例子（信心） |")
    L.append("|---|---|---:|---:|---|")
    for i, t in enumerate(r["top_error_patterns"], 1):
        ex = "；".join(f"`{e['text'][:40]}`({e['conf']})" for e in t["examples"][:3]).replace("|", "\\|")
        L.append(f"| {i} | {t['label']} | {t['pp_count']} | {t['tess_count']} | {ex} |")
    L.append("")
    ic = r.get("info_counts", {})
    L.append(f"資訊類（不算錯誤）：模型原始輸出簡體的行數 {ic.get('simplified_raw', 0)}（已被 OpenCC 轉繁，是正常現象，"
             f"但代表 rec 模型本身偏簡體字集）；低信心行 {ic.get('low_conf', 0)}。偵測為啟發式，可能有誤報（例如圖上刻度也會觸發數字黏連）。\n")
    if r.get("opencc_overreach_diffs"):
        L.append("**OpenCC s2twp vs s2tw 的詞彙替換差異**（左 s2tw 字形轉換 → 右 s2twp 台灣用語替換；右邊會改變考卷原文用字）：\n")
        L.append("、".join(f"`{k}`×{v}" for k, v in r["opencc_overreach_diffs"].items()) + "\n")
    L.append("## 4. 低信心行樣本（PP-OCRv5，最低 15 筆/頁）\n")
    for d in r["docs"]:
        for p in d["pages"]:
            m = p.get("pp")
            if not m:
                continue
            L.append(f"**{d['name']} p{p['page']}**（{m['low_conf_lines']} 行 < {r['low_conf_threshold']}）")
            for s in m["low_conf_samples"][:15]:
                L.append(f"- {s['conf']}  `{s['text']}`".replace("|", "\\|"))
            L.append("")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
