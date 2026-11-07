#!/usr/bin/env python3
"""自動截圖：考卷頁 → 圖形／表格裁圖（取代手動截圖）。
偵測：PP-DocLayout v3（rapid_layout，本機 ONNX）。精修：跟框重疊的墨跡連通塊整塊納入（不缺角缺邊、不漏圖內字），
大部分在框外的正文行不納入（不多字），框旁短標籤（圖號、軸標、(A)(B)）併入，四周留白。
    python ocr/figcrop/crop.py 考卷.pdf -o 輸出資料夾 [--dpi 300] [--model pp_doc_layoutv3]"""
import argparse, os, sys
import cv2, numpy as np, pymupdf
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import ppocr_engine
import pyexam
try:
    from .refine import RE_CAPTION, edge_cut_ink, option_caption, refine_box, text_like
except ImportError:
    from refine import RE_CAPTION, edge_cut_ink, option_caption, refine_box, text_like

KINDS = {"figure", "image", "chart", "table", "figure_caption", "table_caption"}
_ENG = {}


def _rapid_layout():
    """主機的自動更新只拉程式碼、不裝套件：第一次用到時缺 rapid-layout 就自動裝（版本同 requirements.txt）。"""
    try:
        from rapid_layout import RapidLayout
    except ImportError:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "rapid-layout==1.2.1"], check=True)
        from rapid_layout import RapidLayout
    return RapidLayout


def layout_raw(img, model):
    """版面模型原始輸出 (boxes, class_names, scores)，還沒套任何規則；評測快取（evalcache）只快取這一層。"""
    RapidLayout = _rapid_layout()
    if model not in _ENG:
        _ENG[model] = RapidLayout(model_type=model)
    r = _ENG[model](img)
    return r.boxes, r.class_names, r.scores


def detect(img, model):
    boxes, names, scores = layout_raw(img, model)
    out = []
    for b, c, s in zip(boxes, names, scores):
        if c in KINDS and s >= .35:
            out.append((c, [int(v) for v in b], float(s)))
    return out


import re
# 題目的文字選項「(A)溫帶草原」、題目句（有問號）不是圖的一部分；單獨的代號「(A)」（圖選項標籤）才是
RE_TEXT_OPTION = re.compile(r"[(（]\s*[A-DＡ-Ｄa-d]\s*[)）]\s*\S+|[？?]")


RE_QNUM = re.compile(r"^\s*(?:[（(]?\s*(?:[A-DＡ-Ｄa-d]?|\s)\s*[）)]?)?\s*(\d{1,2})\s*[.．、]")
RE_GROUP = re.compile(r"(?:回答|第)?\s*(\d{1,2})\s*(?:[~～至到\-－—]|\s+to\s+)\s*(\d{1,2})\s*題")
RE_GROUP_CUT = re.compile(r"(?:回答|第)\s*(\d{1,2})\s*[~～至到\-－—]\s*(\d{1,2})\s*$")   # 「題」字被換行切到下一行
RE_END = re.compile(r"試題結束|再.{0,4}檢查")
RE_NOTICE = re.compile(r"請翻|翻頁|翻到|背面|下一頁")
RE_ANSWER_PAGE = re.compile(r"(?:答案卷|答案欄|答案紙|答題卷)\s*$")
RE_SECTION = re.compile(r"每題\s*\d*\.?\d*\s*分|選擇題")
RE_HEADER = re.compile(r"姓\s*名|座\s*號|班\s*級|學\s*號|科目代號|科目名稱|定期考")
RE_PURE_OPT = re.compile(r"^[(（]\s*[A-DＡ-Ｄ]\s*[)）]$")


def _columns(raw_lines, W):
    """題號行的 x 起點分群＝實際欄位（A3 跨頁一頁可能 4 欄），回傳各欄起點。"""
    xs = sorted(l[0] for l, t in raw_lines if RE_QNUM.match(str(t)))
    cols = []
    for x in xs:
        if cols and x - cols[-1][-1] < .08 * W:
            cols[-1].append(x)
        else:
            cols.append([x])
    starts = [c[0] for c in cols if len(c) >= 2]
    return starts if len(starts) >= 2 else ([c[0] for c in cols] or [0])


def _col_of(x, starts):
    return max((i for i, c in enumerate(starts) if c <= x + 40), default=0)


RE_LEAD_JUNK = re.compile(r"^[)）\s\d.．、]*")


def _fix_misread_qnums(raw_lines, starts):
    """OCR 認錯題號（「19.」讀成「49.」）：依閱讀順序（欄、由上而下），前後兩個題號差 2、自己卻不是中間那號，就改成中間那號。"""
    numbered = sorted(((_col_of(l[0], starts), l[1], i, int(m.group(1))) for i, (l, t) in enumerate(raw_lines)
                       for m in [RE_QNUM.match(str(t))] if m and text_like(t)))
    fixed = {}
    for (_c0, _y0, _i0, a), (_c1, _y1, i, x), (_c2, _y2, _i2, c) in zip(numbered, numbered[1:], numbered[2:]):
        if c - a == 2 and x != a + 1:
            t = str(raw_lines[i][1]); m = RE_QNUM.match(t)
            fixed[i] = f"{a + 1}." + t[m.end():]
    return [(l, fixed.get(i, t)) for i, (l, t) in enumerate(raw_lines)] if fixed else raw_lines


def repair_qnums(raw_lines, W):
    """OCR 漏讀題號（「10.」讀成「1」、作答括號旁的題號被手寫蓋掉）：同一欄相鄰兩個題號差 g（2～4），
    中間剛好 g−1 行從題號的 x 起頭（比續行、選項凸出）、不是題號也不是選項，就依序補上漏掉的題號。
    先修認錯的題號（_fix_misread_qnums），再補漏掉的。"""
    starts = _columns(raw_lines, W); tol = .006 * W
    raw_lines = _fix_misread_qnums(raw_lines, starts)
    numbered = [(l, int(m.group(1))) for l, t in raw_lines for m in [RE_QNUM.match(str(t))] if m]
    fixed = {}
    for col in range(len(starts)):
        nums = sorted((l[1], n, l[0]) for l, n in numbered if _col_of(l[0], starts) == col)
        if len(nums) < 3:
            continue
        qx = float(np.median([x for _y, _n, x in nums]))
        for (ya, na, _), (yb, nb, _) in zip(nums, nums[1:]):
            if not 2 <= nb - na <= 4:
                continue
            between = [(i, l, str(t)) for i, (l, t) in enumerate(raw_lines) if ya < l[1] < yb and _col_of(l[0], starts) == col]
            rest = [l[0] for _i, l, _t in between]
            cands = [(i, t) for i, l, t in between if abs(l[0] - qx) <= tol and not RE_QNUM.match(t)
                     and not RE_PURE_OPT.match(t) and not re.match(r"^\s*[(（]\s*[A-DＡ-Ｄ]\s*[)）]", t)
                     and sum(1 for x in rest if x > l[0] + 3 * tol) >= 1]
            if len(cands) == nb - na - 1:           # 漏幾題就要剛好幾行，依上下順序補號
                for k, (i, t) in enumerate(sorted(cands, key=lambda c: raw_lines[c[0]][0][1]), 1):
                    fixed[i] = f"{na + k}." + RE_LEAD_JUNK.sub("", t)
    if not fixed:
        return raw_lines
    return [(l, fixed.get(i, t)) for i, (l, t) in enumerate(raw_lines)]


def guess_qno(box, raw_lines, W):
    """題號：同一欄、圖上方最近的題號；題組引言「第 N～M 題」歸 N。欄位看題號實際排列，不假設左右兩半。"""
    raw_lines = repair_qnums(raw_lines, W)
    starts = _columns(raw_lines, W)
    col = _col_of((box[0] + box[2]) / 2 - .05 * W, starts)
    best = None; fallback = None
    for (l, text) in raw_lines:
        if l[1] > box[1] + max(12, .012 * W):   # 框有留白，頂端比圖高一點
            continue
        clean = str(text).replace("［", "(").replace("］", ")")
        m = RE_GROUP.search(clean) or RE_GROUP_CUT.search(clean)
        if m and (int(m.group(2)) - int(m.group(1)) >= 15 or "每題" in clean):     # 「題組題（1-50題，每題1分）」是大標
            m = None
        m = m or RE_QNUM.match(clean)
        if not m:
            continue
        row = (box[1] - l[1], int(m.group(1)))
        if _col_of(l[0], starts) == col and (best is None or row[0] < best[0]):
            best = row
        if fallback is None or row[0] < fallback[0]:
            fallback = row
    if best:
        # 上一題選項 (D) 已結束，到下一個題號之間還有幾行句子＝新題組的文章，圖屬於下一題。
        same = sorted(((l, str(t)) for l, t in raw_lines if _col_of(l[0], starts) == col), key=lambda x: x[0][1])
        y_best = box[1] - best[0]
        below = [(l[1], int(m.group(1))) for l, t in same for m in [RE_QNUM.match(t)] if m and l[1] > y_best + 10 and l[1] > box[1]]
        # 下一題在下一欄或題號被 OCR 認壞時，同欄找不到：題組文章仍歸下一號。
        y_next, q_next = min(below) if below else (float("inf"), best[1] + 1)
        ends = [l[3] for l, t in same if y_best <= l[1] < y_next and re.search(r"[(（]\s*[DＤ]\s*[)）]", t)]
        if ends and min(ends) < box[1]:
            after = [t for l, t in same if min(ends) <= l[1] < y_next and text_like(t)]
            if len(after) >= 2 or any(re.search(r"下圖|附圖|右圖|根據|回答", t) for t in after):
                return q_next
            # 上一題已結束、圖印在下一題右邊且頂端略高於題號：圖屬於下一題。
            if any(l[1] == y_next and l[2] <= box[0] + 10 for l, t in same) and y_next < (box[1] + box[3]) / 2:
                return q_next
    if not (best or fallback):      # 圖在頁面最上方、上面沒有任何題號：歸同欄下面最近的一題
        below = [(l[1], int(m.group(1))) for l, t in raw_lines for m in [RE_QNUM.match(str(t))] if m and _col_of(l[0], starts) == col and l[1] > box[1]]
        return min(below)[1] if below else None
    return (best or fallback)[1]


def text_lines(img, raw=None):
    """回傳 (框, 是否為題目正文)。正文＝題目句或文字選項，裁圖時要排除。raw 給 list 時順便收 (框, 文字)。"""
    lines = []
    for it in ppocr_engine.ocr_lines(img):
        b = it["box"]; xs, ys = [p[0] for p in b], [p[1] for p in b]
        box = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
        lines.append((box, bool(RE_TEXT_OPTION.search(it["text"]))))
        if raw is not None:
            raw.append((box, it["text"]))
    return lines


def overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0]); h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0, w) * max(0, h)


def refine(img, box, lines, pad=8):
    H, W = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ink = (gray < 200).astype(np.uint8)
    x0, y0, x1, y1 = box
    # 正文行：寬（>45% 頁寬的一半欄寬）且大部分在框外 → 不屬於圖，挖掉再算連通塊
    body = np.zeros_like(ink)
    for l, is_body in lines:
        area = (l[2] - l[0]) * (l[3] - l[1])
        if area and (is_body or (overlap(l, box) / area < .5 and (l[2] - l[0]) > .18 * W)):
            body[max(0, l[1] - 2):l[3] + 2, max(0, l[0] - 2):l[2] + 2] = 1
    ink2 = cv2.dilate(ink & (1 - body), np.ones((5, 5), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(ink2, 8)
    inside = set(np.unique(lab[y0:y1, x0:x1])) - {0}
    for i in inside:
        cx0, cy0, cw, ch = st[i, 0], st[i, 1], st[i, 2], st[i, 3]
        if cw * ch > .25 * W * H:          # 跟整頁連在一起（掃描黑邊）就不採用
            continue
        x0, y0, x1, y1 = min(x0, cx0), min(y0, cy0), max(x1, cx0 + cw), max(y1, cy0 + ch)
    # 框旁短標籤（≤ 欄寬 18%）：完全在外擴 3% 範圍內就併入
    m = int(.03 * W)
    for l, is_body in lines:
        if not is_body and (l[2] - l[0]) <= .18 * W and l[0] >= x0 - m and l[2] <= x1 + m and l[1] >= y0 - m and l[3] <= y1 + m:
            x0, y0, x1, y1 = min(x0, l[0]), min(y0, l[1]), max(x1, l[2]), max(y1, l[3])
    # 最後再把框邊上的正文行切掉：正文行跟框重疊時，從離圖較遠的那一邊收回到正文行外
    for l, is_body in lines:
        if not is_body or overlap(l, (x0, y0, x1, y1)) == 0:
            continue
        if l[1] >= (y0 + y1) / 2: y1 = min(y1, l[1] - 2)       # 在下半：框底收到正文行上緣
        elif l[3] <= (y0 + y1) / 2: y0 = max(y0, l[3] + 2)     # 在上半：框頂收到正文行下緣
    return max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)


def merge(boxes):
    boxes = sorted(boxes, key=lambda b: (b[1], b[0])); out = []
    for b in boxes:
        for i, o in enumerate(out):
            if overlap(b, o) > 0:
                out[i] = (min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])); break
        else:
            out.append(b)
    return out


def stop_at_other_layout(orig, grown, layout_boxes, ocr_lines):
    """精修往外長時不可長進上／下方另一個「別題」的版面模型框：兩題的圖上下緊貼時，長在一起會被 merge 併成一張。
    別題＝兩框頂端之間有題號行（題號常在左邊文字欄）。同一題的上下面板（(A)(B)／(C)(D)、表格被拆兩塊）中間沒有題號，照舊可以長在一起。"""
    x0, y0, x1, y1 = grown
    # 只看同一欄的題號：兩欄卷另一欄的題號剛好落在兩框之間不代表是別題（同 guess_qno 的欄位判斷）
    W = max([l1 for l0, l2, l1, l3, t in ocr_lines] + [x1, 1])
    raw = [((l0, l2, l1, l3), t) for l0, l2, l1, l3, t in ocr_lines]
    starts = _columns(raw, W); col = _col_of((orig[0] + orig[2]) / 2 - .05 * W, starts)
    # 題號行要像句子（「14.右圖為…」）：圖內刻度「0.1M」「2.」也會被 RE_QNUM 當成題號
    qtops = [l2 for l0, l2, l1, l3, t in ocr_lines if RE_QNUM.match(str(t)) and text_like(t) and _col_of(l0, starts) == col]
    for o in layout_boxes:
        if tuple(o) == tuple(orig) or overlap(orig, o) > 0 or overlap((x0, y0, x1, y1), o) == 0:
            continue
        lo, hi = sorted((orig[1], o[1]))
        if not any(lo < q < hi for q in qtops):
            continue
        # 停在兩個模型框間隙的中線：對方精修時也可能往這邊長幾 px，停在對方邊上仍會重疊被併
        if o[1] >= orig[3]:
            y1 = min(y1, (orig[3] + o[1]) // 2)
        elif o[3] <= orig[1]:
            y0 = max(y0, (o[3] + orig[1]) // 2 + 1)
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else grown


def has_qnum_inside(a, b, ocr_lines):
    """併起來的範圍內出現題號行＝跨題，不合併。"""
    x0, y0, x1, y1 = min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])
    return any(RE_QNUM.match(str(t)) and l0 >= x0 - 10 and l0 <= x1 and l2 >= y0 - 10 and l3 <= y1 + 10 for l0, l2, l1, l3, t in ocr_lines)


def merge_figure_panels(boxes, gap=110, ocr_lines=()):
    """同一題並排的子圖常被 layout 拆開；僅在高度實質重疊時合併。"""
    boxes = list(boxes)
    changed = True
    while changed:
        changed = False
        for i, a in enumerate(boxes):
            for j in range(i + 1, len(boxes)):
                b = boxes[j]
                vertical = min(a[3], b[3]) - max(a[1], b[1])
                horiz_gap = max(a[0], b[0]) - min(a[2], b[2])
                # 幾乎等高的並排面板是同一張圖，間距可以再寬一點
                ha, hb = a[3]-a[1], b[3]-b[1]
                limit = gap * 4 / 3 if min(ha, hb) > .9 * max(ha, hb) and vertical > .9 * max(ha, hb) else gap
                if vertical > .45 * min(ha, hb) and 0 <= horiz_gap <= limit and not has_qnum_inside(a, b, ocr_lines):
                    boxes[i] = (min(a[0],b[0]), min(a[1],b[1]), max(a[2],b[2]), max(a[3],b[3]))
                    boxes.pop(j); changed = True; break
            if changed: break
    return boxes


def merge_option_panels(boxes, ocr_lines, W, H):
    """圖選項 (A)(B)(C)(D) 常被拆成幾張：高度相近、對齊、中間沒有題目文字的子圖併成一張（橫排與 2×2）。"""
    def text_between(x0, y0, x1, y1):
        return any(text_like(t) and not option_caption(t) and min(l1, x1) - max(l0, x0) > 0 and y0 < (l2 + l3) / 2 < y1   # 只擦到邊的上下題目不算夾在中間
                   for l0, l2, l1, l3, t in ocr_lines)
    def has_option_labels(a, b):
        """併起來的範圍內看得到 ≥2 個獨立代號 (A)(B)(C)(D) 才是圖選項；沒有代號就是不同題各自的圖。"""
        m = int(.02 * W); x0, y0, x1, y1 = min(a[0], b[0]) - m, min(a[1], b[1]) - m, max(a[2], b[2]) + m, max(a[3], b[3]) + m
        found = set()
        for l0, l2, l1, l3, t in ocr_lines:
            pure = RE_PURE_OPT.match(str(t).strip())    # 純代號可能印在圖左邊一段距離
            if (l0 >= x0 or (pure and l0 >= x0 - .1 * W)) and l1 <= x1 and l2 >= y0 and l3 <= y1:
                found |= set(re.findall(r"[(（]\s*([A-DＡ-Ｄ])\s*[)）]", str(t)))
        return len(found) >= 2
    boxes = list(boxes); changed = True
    while changed:
        changed = False
        for i, a in enumerate(boxes):
            for j in range(i + 1, len(boxes)):
                b = boxes[j]; ha, hb = a[3] - a[1], b[3] - b[1]; wa, wb = a[2] - a[0], b[2] - b[0]
                vov = min(a[3], b[3]) - max(a[1], b[1]); hov = min(a[2], b[2]) - max(a[0], b[0])
                hgap = max(a[0], b[0]) - min(a[2], b[2]); vgap = max(a[1], b[1]) - min(a[3], b[3])
                row = vov > .8 * min(ha, hb) and min(ha, hb) > .7 * max(ha, hb) and 0 <= hgap <= .07 * W \
                    and not text_between(min(a[2], b[2]), max(a[1], b[1]), max(a[0], b[0]), min(a[3], b[3]))
                grid = hov > .8 * min(wa, wb) and min(wa, wb) > .7 * max(wa, wb) and 0 <= vgap <= .03 * H \
                    and not text_between(max(a[0], b[0]), min(a[3], b[3]), min(a[2], b[2]), max(a[1], b[1]))
                if (row or grid) and has_option_labels(a, b) and not has_qnum_inside(a, b, ocr_lines):
                    boxes[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    boxes.pop(j); changed = True; break
            if changed:
                break
    # 併好的圖選項：印在左邊的純代號 (A)(C) 也要框進來，手動截圖都會帶到。
    out = []
    for b in boxes:
        inside = sum(1 for l0, l2, l1, l3, t in ocr_lines if RE_PURE_OPT.match(str(t).strip()) and l0 >= b[0] and l1 <= b[2] and l2 >= b[1] - 10 and l3 <= b[3] + 10)
        x0 = b[0]
        if inside:
            for l0, l2, l1, l3, t in ocr_lines:
                if RE_PURE_OPT.match(str(t).strip()) and l1 <= b[0] + 20 and l0 >= b[0] - .1 * W and l2 >= b[1] - 10 and l3 <= b[3] + 10:
                    x0 = min(x0, l0)
        out.append((x0, b[1], b[2], b[3]))
    return out


def sentence_cover(box, ocr_lines):
    """框內被句子行蓋掉的面積比例：備援候選常把壓在題目上的手寫筆跡當成圖。"""
    x0, y0, x1, y1 = box
    area = sum(max(0, min(l1, x1) - max(l0, x0)) * max(0, min(l3, y1) - max(l2, y0))
               for l0, l2, l1, l3, t in ocr_lines if text_like(t))
    return area / max(1, (x1 - x0) * (y1 - y0))


def is_red_mark(img, box):
    """窄小且墨跡幾乎全是彩色＝老師／學生的批改筆跡，不是印刷的圖。"""
    x0, y0, x1, y1 = [int(v) for v in box]
    if x1 - x0 > .08 * img.shape[1]:
        return False
    sub = img[max(0, y0):y1, max(0, x0):x1]
    if sub.size == 0:
        return False
    ink = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY) < 180
    return ink.any() and (cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)[:, :, 1] > 80)[ink].mean() > .8


def trim_stem_edges(box, ocr_lines):
    """面板併好之後，框的上下緣還帶到題幹末行就切掉（合併前每塊都沒碰到，併起來才碰到）。"""
    x0, y0, x1, y1 = box; h = y1 - y0
    for l0, l2, l1, l3, t in ocr_lines:
        area = max(1, (l1 - l0) * (l3 - l2))
        over = max(0, min(l1, x1) - max(l0, x0)) * max(0, min(l3, y1) - max(l2, y0))
        if not text_like(t) or option_caption(t) or over < .5 * area or len(re.findall(r"[\u4e00-\u9fff]", str(t))) < 4:
            continue
        cy = (l2 + l3) / 2
        if cy < box[1] + .15 * h:
            y0 = max(y0, l3 + 2)
        elif cy > box[3] - .15 * h:
            y1 = min(y1, l2 - 2)
    return (x0, y0, x1, y1) if y1 - y0 > .5 * h else box


RE_QNUM_TOKEN = re.compile(r"^[\s()（）A-DＡ-Ｄa-d]*\d{1,2}\s*[.．、]")


def is_answer_mark(box, ocr_lines, W):
    """作答括號裡的手寫答案（A～D）：框很小，而且題號就在框裡或緊貼框的右邊。真的小圖不會剛好長在題號的作答位置。"""
    x0, y0, x1, y1 = box
    if max(x1 - x0, y1 - y0) >= .08 * W:
        return False
    for l0, l2, l1, l3, t in ocr_lines:
        if RE_QNUM_TOKEN.match(str(t)) and y0 <= (l2 + l3) / 2 <= y1 and x0 - .02 * W <= l0 <= x1 + .03 * W:
            return True
    return False


def pad_box(box, p, ocr_lines, shape):
    """四周留白 p，但上下的留白只能長進空白：框外緊貼的文字行（表格正上方的題幹）不可被留白切進半行。
    框本身已經切到的行不在這裡處理。"""
    H, W = shape[:2]; x0, y0, x1, y1 = box
    top, bottom = max(0, y0 - p), min(H, y1 + p)
    for l0, l2, l1, l3, _t in ocr_lines:
        if min(l1, x1) - max(l0, x0) < 20:
            continue
        if l2 < y0 and top < l3 <= y0 + 2:        # 行在框上方、留白會碰到它
            top = min(y0, max(top, l3))
        if l3 > y1 and y1 - 2 <= l2 < bottom:     # 行在框下方
            bottom = max(y1, min(bottom, l2))
    return (max(0, x0 - p), top, min(W, x1 + p), bottom)


def drop_partial_lines(img, box, ocr_lines):
    """框的上／下緣把框外的字行切到一小截（不到半行在框內）：退到行邊，把那一截字拿掉。
    表格框常這樣（模型框＋外擴比上框線高一點，碰到正上方題幹的下緣）。退的時候不越過長橫線（表格上框線），也不讓框變太扁。"""
    x0, y0, x1, y1 = box; h = y1 - y0; base = max(0, y0)
    ink = cv2.cvtColor(img[base:y1, max(0, x0):x1], cv2.COLOR_BGR2GRAY) < 160
    long_row = ink.mean(1) > .5 if ink.size else np.zeros(0, bool)      # 框線：橫跨框寬一半以上；索引從原框上緣算
    for l0, l2, l1, l3, t in ocr_lines:
        lh = l3 - l2
        if lh < 8 or len(re.findall(r"[一-鿿]", str(t))) < 2 or min(l1, x1) - max(l0, x0) < .5 * (l1 - l0):
            continue
        if l2 < y0 < l3 and (l3 - y0) / lh < .5:
            frame = np.where(long_row[y0 - base:l3 - base + 1])[0]
            ny = y0 + int(frame[0]) - 2 if len(frame) else l3
            if ny > y0 and y1 - ny > .6 * h:
                y0 = ny
        elif l2 < y1 < l3 and (y1 - l2) / lh < .5:
            frame = np.where(long_row[l2 - base:y1 - base])[0]
            ny = l2 + int(frame[-1]) + 2 if len(frame) else l2
            if ny < y1 and ny - y0 > .6 * h:
                y1 = ny
    return (x0, y0, x1, y1)


def extend_row(img, box, others, ocr_lines):
    """模型常只抓到半張圖（兩支量筒只抓到甲）：同一列緊鄰處還有一塊不是字的墨跡、高度也夠，就是圖的另一半。"""
    H, W = img.shape[:2]; x0, y0, x1, y1 = box; h = y1 - y0
    ink = cv2.cvtColor(img[y0:y1], cv2.COLOR_BGR2GRAY) < 180
    ink &= cv2.cvtColor(img[y0:y1], cv2.COLOR_BGR2HSV)[:, :, 1] <= 80      # 彩色的批改筆跡不算
    # 題組外框線不算：橫線比頁寬 10% 長、直線比框高一半長
    m = ink.astype(np.uint8)
    frames = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((1, int(.1 * W)), np.uint8)) | cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((max(3, h // 2), 1), np.uint8))
    ink &= ~(cv2.dilate(frames, np.ones((5, 5), np.uint8)) > 0)
    for l0, l2, l1, l3, t in ocr_lines:
        if text_like(t) and min(l3, y1) > max(l2, y0):
            ink[max(0, l2 - y0 - 3):max(0, l3 - y0 + 3), max(0, l0 - 3):l1 + 3] = False
    has_ink = ink.sum(0) > 2
    def block(start, step):
        """從框邊往外找第一塊墨跡，回傳 (近端, 遠端)；太遠或沒有就回 None。"""
        limit = int(.06 * W); blank = int(.015 * W); x = start
        while 0 <= x < W and abs(x - start) <= limit and not has_ink[x]:
            x += step
        if not (0 <= x < W) or abs(x - start) > limit:
            return None
        near = x; far = x; gap = 0
        while 0 <= x < W and gap < blank:
            gap = 0 if has_ink[x] else gap + 1
            if has_ink[x]:
                far = x
            x += step
        return near, far
    for step, start in ((1, x1 + 1), (-1, x0 - 1)):
        found = block(start, step) if 0 <= start < W else None
        if not found:
            continue
        a, b = sorted(found)
        rows = np.where(ink[:, a:b + 1].sum(1) > 2)[0]
        if b - a < .02 * W or not len(rows) or rows[-1] - rows[0] < .35 * h:
            continue
        # 另一半的圖墨量不會太少；斷掉的外框線、鉛筆痕只有一點點
        if ink[:, a:b + 1].sum() < .1 * max(1, ink[:, x0:x1].sum()):
            continue
        cand = (min(x0, a), y0, max(x1, b + 1), y1)
        if any(overlap((a, y0, b + 1, y1), o) > 0 for o in others) or has_qnum_inside(box, cand, ocr_lines):
            continue
        # 這塊裡有句子（題幹被挖掉後的殘影）就不是圖
        if any(text_like(t) and min(l1, b + 1) > max(l0, a) and min(l3, y1) > max(l2, y0) for l0, l2, l1, l3, t in ocr_lines):
            continue
        x0, x1 = cand[0], cand[2]
    return x0, y0, x1, y1


def has_border(img, box):
    """四邊都有實際的框線才算外框；只是把兩張圖連起來的範圍沒有框線。"""
    x0, y0, x1, y1 = [int(v) for v in box]; r = 12
    ink = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) < 180
    H, W = ink.shape
    def line(a, axis):
        return a.size > 0 and a.mean(axis=axis).max() > .7
    return (line(ink[max(0, y0-r):min(H, y0+r), x0:x1], 1) and line(ink[max(0, y1-r):min(H, y1+r), x0:x1], 1)
            and line(ink[y0:y1, max(0, x0-r):min(W, x0+r)], 0) and line(ink[y0:y1, max(0, x1-r):min(W, x1+r)], 0))


def is_pencil_mark(img, box):
    """又窄又沒有純黑線條＝邊欄的鉛筆塗鴉；印刷的圖就算淺也比較寬，所以兩個條件都要成立，免得刪到真圖。"""
    x0, y0, x1, y1 = [int(v) for v in box]
    if x1 - x0 > .1 * img.shape[1]:
        return False
    gray = cv2.cvtColor(img[max(0, y0):y1, max(0, x0):x1], cv2.COLOR_BGR2GRAY)
    ink = gray[gray < 200]
    return ink.size > 50 and np.percentile(ink, 10) >= 125


def crop_page(img, model="pp_doc_layoutv3", raw=None, ocr=None):
    """ocr：同一張 img 已經跑過的 text_lines 結果 [(框, 文字)]（crop_items 判直立時跑的），給了就不再跑 OCR。"""
    dets = detect(img, model)
    if ocr is not None:
        if raw is not None:
            raw.extend(ocr)
    else:
        text_lines(img, raw)
    # refine_box 會用 OCR 長正文行作為硬停損線，再逐邊擴到空白；避免舊版
    # 「先擴、最後硬切」把軸標／圖號切掉的問題。
    ocr_lines = [(l[0], l[1], l[2], l[3], text) for l, text in (raw or [])]
    # 整頁外框（考卷四周畫一圈框線）會被版面模型當成一個超大表格，把整頁的圖都吃進去；超過半頁的偵測框不是題目的圖。
    page_area = img.shape[0] * img.shape[1]
    candidates = [(c, b, False) for c, b, _ in dets if c in ("figure", "image", "chart", "table")
                  and (b[2] - b[0]) * (b[3] - b[1]) <= .5 * page_area]
    # DocLayout 會漏掉線條非常密的生物剖面圖；既有的本地連通元件候選器
    # 正好補這類圖。只收完全未被 layout 命中的候選，避免表格與已偵測圖重複。
    layout_boxes = [tuple(b) for _, b, _ in candidates]
    raw_candidates = list(pyexam.figure_candidates(img, dpi=300))
    # 有外框的圖：模型常只抓到框裡的小圖，備援卻抓到外框。框裡有 >=2 個模型框、沒有句子也沒有題號，就是同一張圖。
    def inside(x, f):
        return overlap(x, f) > .8 * max(1, (x[2]-x[0])*(x[3]-x[1]))
    frames = [tuple(int(v) for v in f) for f in raw_candidates if sum(1 for x in layout_boxes if inside(x, f)) >= 2
              and sentence_cover(f, ocr_lines) < .05 and not has_qnum_inside(f, f, ocr_lines) and has_border(img, f)]
    for b in raw_candidates:
        # 分母取小框：整頁寬的備援框只蓋到模型框一點比例，也是重複，留著會把模型框吃掉。
        if not any(overlap(b, x) / max(1, min((b[2]-b[0])*(b[3]-b[1]), (x[2]-x[0])*(x[3]-x[1]))) > .15 for x in layout_boxes):
            candidates.append(("figure", list(b), True))
    boxes = []; extra = []; tables = []
    for c, b, _fallback in candidates:
        kind = "table" if c == "table" else "figure"
        if _fallback and any(RE_SECTION.search(str(t)) and min(l1, b[2]) > max(l0, b[0]) and min(l3, b[3]) > max(l2, b[1]) for l0, l2, l1, l3, t in ocr_lines):
            continue        # 第一頁的作答說明框、大題標題
        if (_fallback and sentence_cover(b, ocr_lines) >= .5) or (kind == "figure" and (is_red_mark(img, b) or is_pencil_mark(img, b))):
            continue
        # 細長的備援框多半只是一條線，併進來會把兩邊的圖連成一張
        if _fallback and max(b[2]-b[0], b[3]-b[1]) > 4 * min(b[2]-b[0], b[3]-b[1]):
            continue
        # 版面模型的框本身就準（掃描卷也是）；只在頁寬 1.5% 內小幅外擴補回被切到的筆畫，
        # 不用連通塊整塊併入——掃描卷的外框或文字一連通，框就會爆到整頁。
        m = int(.015 * img.shape[1])
        column = (b[0] - m, b[1] - m, b[2] + m, b[3] + m)
        candidate = refine_box(img, b, kind=kind, ocr_lines=ocr_lines, column=column)
        if not _fallback:
            candidate = stop_at_other_layout(b, candidate, layout_boxes, ocr_lines)
        (extra if _fallback else tables if kind == "table" else boxes).append(candidate)
    for f in frames:
        boxes = [b for b in boxes if not inside(b, f)] + [f]
    boxes = merge_figure_panels(merge(boxes), gap=int(.045 * img.shape[1]), ocr_lines=ocr_lines)
    boxes = merge(boxes + tables)     # 表格旁邊的圖是另一張，只有真的重疊才併
    boxes = merge_option_panels(boxes, ocr_lines, img.shape[1], img.shape[0])   # 選項做成表格也要併
    # 備援框併入條件：很近就併；稍遠要高度相近（遠又矮多半是別的東西）。真的重疊最後由 merge 處理。
    for e in extra:
        for i, b in enumerate(boxes):
            vov = min(e[3], b[3]) - max(e[1], b[1]); hgap = max(e[0], b[0]) - min(e[2], b[2])
            lo, hi = sorted((e[3] - e[1], b[3] - b[1]))
            near = hgap <= .025 * img.shape[1] or (hgap <= .045 * img.shape[1] and lo > .4 * hi)
            if vov > .45 * lo and hgap >= 0 and near and not has_qnum_inside(e, b, ocr_lines):
                boxes[i] = (min(e[0], b[0]), min(e[1], b[1]), max(e[2], b[2]), max(e[3], b[3]))
                break
        else:
            boxes.append(e)
    boxes = merge(boxes)
    # 表格的列也像句子，修剪會切掉表頭和底列，所以含表格的框不修。
    has_table = lambda b: any(overlap(b, t) > .5 * max(1, (t[2]-t[0])*(t[3]-t[1])) for t in tables)
    boxes = [b if has_table(b) else trim_stem_edges(b, ocr_lines) for b in boxes]
    boxes = [b if has_table(b) else extend_row(img, b, [o for o in boxes if o != b], ocr_lines) for b in boxes]
    p = int(.006 * img.shape[1])      # 手動截圖四周都會留一點白
    boxes = [pad_box(b, p, ocr_lines, img.shape) for b in boxes]
    boxes = [drop_partial_lines(img, b, ocr_lines) for b in boxes]
    H = img.shape[0]   # 頁首小圖（校徽、QR、圖示）不是題目的圖
    boxes = [b for b in boxes if not (b[3] < .14 * H and b[3] - b[1] < .06 * H)]
    # 卷頭的班級座號姓名列、科目代號表格（常做成表格，比頁首小圖高）也不是題目的圖。
    # 只刪扁長條（寬 ≥ 2 倍高）：卷頭文字和第一題的圖併成一框時，那框接近方形，不能整框刪掉。
    boxes = [b for b in boxes if not (b[1] < .2 * H and b[3] - b[1] < .15 * H and b[2] - b[0] >= 2 * (b[3] - b[1]) and any(
        RE_HEADER.search(str(t)) and l0 >= b[0] - 5 and l1 <= b[2] + 5 and l2 >= b[1] - 5 and l3 <= b[3] + 5
        for l0, l2, l1, l3, t in ocr_lines))]
    # 題組前的裝飾圖示（星星等）：長邊不到頁寬 5%，手動截圖最小的圖也比這大得多
    boxes = [b for b in boxes if max(b[2] - b[0], b[3] - b[1]) >= .05 * img.shape[1]]
    boxes = [b for b in boxes if not is_answer_mark(b, ocr_lines, img.shape[1])]
    # 卷末的「試題結束，請再次檢查」方框不是題目的圖
    # 提示行要佔框寬一半以上才算：圖的框邊碰巧帶到頁尾「背面還有題目」時圖還是要留
    boxes = [b for b in boxes if not any(RE_END.search(str(t)) and l0 >= b[0] and l1 <= b[2] and l2 >= b[1] and l3 <= b[3] for l0, l2, l1, l3, t in ocr_lines)]
    boxes = [b for b in boxes if not any(RE_NOTICE.search(str(t)) and l0 >= b[0] and l1 <= b[2] and l2 >= b[1] and l3 <= b[3] and l1 - l0 > .5 * (b[2] - b[0]) for l0, l2, l1, l3, t in ocr_lines)]
    # 答案卷整頁都是作答格，不是題目的圖。只認標題結尾，說明句「請將答案填入答案卡」不算。
    if any(RE_ANSWER_PAGE.search(str(t)) and l3 < .25 * H for l0, l2, l1, l3, t in ocr_lines):
        boxes = []
    return boxes, dets


def page_rotation(img):
    """掃描卷可能轉 90°／180°。回傳 k：np.rot90(img, k) 才是正的。
    OCR 會自動把直立的行轉過來讀，所以轉正的頁、再逆時針差 90° 的頁都讀得到字，要用行框方向再分一次。"""
    small = cv2.resize(img, None, fx=.4, fy=.4, interpolation=cv2.INTER_AREA)
    def read(k):
        raw = []; text_lines(np.rot90(small, k).copy(), raw)
        good = [b for b, t in raw if len(re.findall(r"[\u4e00-\u9fff]", str(t))) >= 4]
        return len(good), sum(1 for b in good if b[3] - b[1] > b[2] - b[0])
    n0, tall0 = read(0)
    if n0 >= 5:
        return 1 if tall0 > .5 * n0 else 0
    n1, tall1 = read(1)
    if n1 >= 5:
        return 2 if tall1 > .5 * n1 else 1
    return 3 if read(3)[0] >= 5 else 0


def looks_upright(raw):
    """整頁 OCR 行框判直立：≥4 個中文字的行至少 15 行、直立長條不過半。
    門檻比 page_rotation 的 5 行嚴：倒過來（180°）的頁整頁 OCR 會讀出最多約 5 行亂碼，5 行會誤判成直立
    （全量比對：實際 1305 頁 0 不一致；直立頁人工轉 90/180/270 共 90 張 0 不一致；約 78% 頁走快速路徑）。不夠 15 行就照舊走 page_rotation。"""
    good = [b for b, t in raw if len(re.findall(r"[一-鿿]", str(t))) >= 4]
    return len(good) >= 15 and sum(1 for b in good if b[3] - b[1] > b[2] - b[0]) <= .5 * len(good)


def unrotate_box(box, k, shape):
    """np.rot90(img, k) 上的框換回原頁座標；shape 是原頁。"""
    H, W = shape[:2]; x0, y0, x1, y1 = box
    return {0: (x0, y0, x1, y1), 1: (W - y1, x0, W - y0, x1), 2: (W - x1, H - y1, W - x0, H - y0), 3: (y0, H - x1, y1, H - x0)}[k % 4]


def strip_caption(box, raw):
    """把框上下緣的圖說行（圖(五)、表一）去掉後的框；手動截圖有時框圖說、有時不框，兩種都算合格。"""
    x0, y0, x1, y1 = box; h = y1 - y0
    for l, t in raw:
        t = str(t).strip()
        if not (RE_CAPTION.match(t) and len(t) <= 16 and l[0] >= x0 - 10 and l[2] <= x1 + 10 and l[1] >= box[1] - 10 and l[3] <= box[3] + 10):
            continue
        if (l[1] + l[3]) / 2 > box[1] + .7 * h:
            y1 = min(y1, l[1] - 4)
        elif (l[1] + l[3]) / 2 < box[1] + .3 * h:
            y0 = max(y0, l[3] + 4)
    return (x0, y0, x1, y1) if y1 - y0 > 40 else box


def crop_items(img, model="pp_doc_layoutv3", ocr_page=None, reuse_lines=None):
    """整頁流程：先轉正再切。回傳每張圖的 box（原頁座標）、box_nocap（評測用：不含圖說的框）、qno、crop（轉正後的圖）。
    提速：先跑一次整頁 OCR，行框看起來是直立的頁（最常見）就直接當 k=0、這批 OCR 給 crop_page 沿用，
    不用再對 0.4 倍小圖另跑轉向 OCR（每頁省約 2 秒）。看起來不直立才走原本的 page_rotation。
    ocr_page（ocr_reuse.PageOcr，--ocr-dir）：題號改用原稿 OCR＋切題結果約束（已對齊到本頁），多回傳 qno_conf；轉向用對齊結果。
    reuse_lines：框的精修也用原稿 OCR 的行、不再對本頁跑 OCR（省時間，但框會因兩邊 OCR 字不同而變，見交接文件）；
    預設看環境變數 FIGCROP_REUSE_LINES=1，否則精修照舊用本頁自己的 OCR（框和沒給 --ocr-dir 時一樣）。"""
    if ocr_page is not None:
        if reuse_lines is None:
            reuse_lines = os.environ.get("FIGCROP_REUSE_LINES") == "1"
        k = ocr_page.k
        up = np.rot90(img, k).copy() if k else img
        raw = []; boxes, _ = crop_page(up, model, raw, ocr=ocr_page.raw if reuse_lines else None)
        items = []
        for b in boxes:
            r = ocr_page.assign(b); q, conf = r["qno"], r["conf"]
            items.append({"box": tuple(int(v) for v in unrotate_box(b, k, img.shape)), "qno": q, "qno_conf": conf,
                          "qno_group": list(r["group"]) if r["group"] else None, "qno_rule": r["rule"],
                          "box_nocap": tuple(int(v) for v in unrotate_box(strip_caption(b, raw), k, img.shape)),
                          "crop": up[b[1]:b[3], b[0]:b[2]], "_col": ocr_page._col_of(b), "_up": b})
        last = None                              # 同頁依閱讀順序（欄、上下）題號倒退：標低信心，不改號
        for it in sorted(items, key=lambda it: (it["_col"], it["_up"][1])):
            if it["qno"] is not None:
                if last is not None and it["qno"] < last:
                    it["qno_conf"] = "low"
                last = max(it["qno"], last or it["qno"])
        for it in items:
            it.pop("_col"); it.pop("_up")
        return items
    first = []; text_lines(img, first)
    k = 0 if looks_upright(first) else page_rotation(img)
    up = np.rot90(img, k).copy() if k else img
    raw = []; boxes, _ = crop_page(up, model, raw, ocr=first if k == 0 else None)
    return [{"box": tuple(int(v) for v in unrotate_box(b, k, img.shape)), "qno": guess_qno(b, raw, up.shape[1]),
             "box_nocap": tuple(int(v) for v in unrotate_box(strip_caption(b, raw), k, img.shape)),
             "crop": up[b[1]:b[3], b[0]:b[2]]} for b in boxes]


def page_dpi(page, dpi=300):
    """清稿 PDF 曾把像素數當頁面點數（A4 變 2271×3299 點），300dpi 會變一頁 1.3 億像素；這種頁面不放大（72dpi＝原圖解析度）。
    正常紙張（含 A3）照用 dpi。正式切圖（main）和評測（gt_eval／wild_run／new_regress）都要用這個，兩邊解析度才一致。"""
    return 72 if max(page.rect.width, page.rect.height) > 2000 else dpi


def page_numbers(spec, total):
    """解析 1,3-5；無指定時回傳全部（CLI 可在 30 秒限制下逐頁呼叫）。"""
    if not spec:
        return set(range(1, total + 1))
    out = set()
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        start = int(a); end = int(b or a)
        out.update(range(start, end + 1))
    return {n for n in out if 1 <= n <= total}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("pdf"); ap.add_argument("-o", required=True)
    ap.add_argument("--dpi", type=int, default=300); ap.add_argument("--model", default="pp_doc_layoutv3")
    ap.add_argument("--pages", help="1 起算頁碼，例如 1,3-5；供逐頁執行")
    ap.add_argument("--json", action="store_true", help="輸出 JSON（座標用 PDF 點、裁圖 base64 PNG、推測題號）給茲茲 OCR／raw.html")
    ap.add_argument("--ocr-dir", help="pyexam 對原稿跑出的資料夾（含 *_ocr_boxes.json）：沿用它的 OCR 與切題結果，不再對本 PDF 跑 OCR；"
                                      "題號依切題結果約束，JSON 每張圖多一個 qno_conf（high／low）")
    a = ap.parse_args()
    reuse = None
    if a.ocr_dir:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import ocr_reuse
        reuse = ocr_reuse.OcrReuse(a.ocr_dir)
    os.makedirs(a.o, exist_ok=True)
    pages = []
    doc = pymupdf.open(a.pdf)
    wanted = page_numbers(a.pages, len(doc))
    for pn, page in enumerate(doc, 1):
        if pn not in wanted:
            continue
        # 清稿 PDF 曾把像素數當頁面點數（A4 變 2271×3299 點），300dpi 會變一頁 1.3 億像素；
        # 這種頁面不放大，用接近原圖的解析度。正常紙張（含 A3）不受影響。
        dpi = page_dpi(page, a.dpi)
        pix = page.get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3][:, :, ::-1].copy()
        boxes = crop_items(img, a.model, ocr_page=reuse.page(pn, img) if reuse else None)
        k_pt = 72 / dpi; items = []
        for k, it in enumerate(boxes, 1):
            x0, y0, x1, y1 = it["box"]
            path = os.path.join(a.o, f"p{pn}_fig{k}.png"); pyexam._imwrite(path, it["crop"])   # Windows 中文路徑
            items.append({"x": x0 * k_pt, "y": y0 * k_pt, "w": (x1 - x0) * k_pt, "h": (y1 - y0) * k_pt,
                          "qno": it["qno"], "file": path,
                          **({"qno_conf": it["qno_conf"], "qno_group": it["qno_group"]} if "qno_conf" in it else {})})
        pages.append({"page": pn, "width": page.rect.width, "height": page.rect.height, "items": items})
        if not a.json:
            print(f"p{pn}: {len(boxes)} 張")
    if a.json:
        import base64, json
        for p in pages:
            for it in p["items"]:
                it["png"] = base64.b64encode(open(it.pop("file"), "rb").read()).decode()
        print(json.dumps({"ok": True, "pages": pages}, ensure_ascii=False))


if __name__ == "__main__":
    main()
