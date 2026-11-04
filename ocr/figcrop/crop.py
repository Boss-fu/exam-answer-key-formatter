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
    from .refine import edge_cut_ink, refine_box, text_like
except ImportError:
    from refine import edge_cut_ink, refine_box, text_like

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


def detect(img, model):
    RapidLayout = _rapid_layout()
    if model not in _ENG:
        _ENG[model] = RapidLayout(model_type=model)
    r = _ENG[model](img)
    out = []
    for b, c, s in zip(r.boxes, r.class_names, r.scores):
        if c in KINDS and s >= .35:
            out.append((c, [int(v) for v in b], float(s)))
    return out


import re
# 題目的文字選項「(A)溫帶草原」、題目句（有問號）不是圖的一部分；單獨的代號「(A)」（圖選項標籤）才是
RE_TEXT_OPTION = re.compile(r"[(（]\s*[A-DＡ-Ｄa-d]\s*[)）]\s*\S+|[？?]")


RE_QNUM = re.compile(r"^\s*(?:[（(]?\s*(?:[A-DＡ-Ｄa-d]?|\s)\s*[）)]?)?\s*(\d{1,2})\s*[.．、]")
RE_GROUP = re.compile(r"(?:回答|第)?\s*(\d{1,2})\s*(?:[~～至到\-－—]|\s+to\s+)\s*(\d{1,2})\s*題")


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


def guess_qno(box, raw_lines, W):
    """題號：同一欄、圖上方最近的題號；題組引言「第 N～M 題」歸 N。欄位看題號實際排列，不假設左右兩半。"""
    starts = _columns(raw_lines, W)
    col = _col_of((box[0] + box[2]) / 2 - .05 * W, starts)
    best = None; fallback = None
    for (l, text) in raw_lines:
        if l[1] > box[1] + max(12, .012 * W):   # 框有留白，頂端比圖高一點
            continue
        clean = str(text).replace("［", "(").replace("］", ")")
        m = RE_GROUP.search(clean) or RE_QNUM.match(clean)
        if not m:
            continue
        row = (box[1] - l[1], int(m.group(1)))
        if _col_of(l[0], starts) == col and (best is None or row[0] < best[0]):
            best = row
        if fallback is None or row[0] < fallback[0]:
            fallback = row
    return (best or fallback)[1] if (best or fallback) else None


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
                if vertical > .45 * min(a[3]-a[1], b[3]-b[1]) and 0 <= horiz_gap <= gap and not has_qnum_inside(a, b, ocr_lines):
                    boxes[i] = (min(a[0],b[0]), min(a[1],b[1]), max(a[2],b[2]), max(a[3],b[3]))
                    boxes.pop(j); changed = True; break
            if changed: break
    return boxes


def merge_option_panels(boxes, ocr_lines, W, H):
    """圖選項 (A)(B)(C)(D) 常被拆成幾張：高度相近、對齊、中間沒有題目文字的子圖併成一張（橫排與 2×2）。"""
    def text_between(x0, y0, x1, y1):
        return any(text_like(t) and min(l1, x1) - max(l0, x0) > 0 and min(l3, y1) - max(l2, y0) > 0
                   for l0, l2, l1, l3, t in ocr_lines)
    def has_option_labels(a, b):
        """併起來的範圍內看得到 ≥2 個獨立代號 (A)(B)(C)(D) 才是圖選項；沒有代號就是不同題各自的圖。"""
        m = int(.02 * W); x0, y0, x1, y1 = min(a[0], b[0]) - m, min(a[1], b[1]) - m, max(a[2], b[2]) + m, max(a[3], b[3]) + m
        found = set()
        for l0, l2, l1, l3, t in ocr_lines:
            if l0 >= x0 and l1 <= x1 and l2 >= y0 and l3 <= y1:
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
                row = vov > .8 * min(ha, hb) and min(ha, hb) > .7 * max(ha, hb) and 0 <= hgap <= .12 * W \
                    and not text_between(min(a[2], b[2]), max(a[1], b[1]), max(a[0], b[0]), min(a[3], b[3]))
                grid = hov > .8 * min(wa, wb) and min(wa, wb) > .7 * max(wa, wb) and 0 <= vgap <= .03 * H \
                    and not text_between(max(a[0], b[0]), min(a[3], b[3]), min(a[2], b[2]), max(a[1], b[1]))
                if (row or grid) and has_option_labels(a, b) and not has_qnum_inside(a, b, ocr_lines):
                    boxes[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    boxes.pop(j); changed = True; break
            if changed:
                break
    return boxes


def crop_page(img, model="pp_doc_layoutv3", raw=None):
    dets = detect(img, model)
    lines = text_lines(img, raw)
    # refine_box 會用 OCR 長正文行作為硬停損線，再逐邊擴到空白；避免舊版
    # 「先擴、最後硬切」把軸標／圖號切掉的問題。
    ocr_lines = [(l[0], l[1], l[2], l[3], text) for l, text in (raw or [])]
    candidates = [(c, b, False) for c, b, _ in dets if c in ("figure", "image", "chart", "table")]
    # DocLayout 會漏掉線條非常密的生物剖面圖；既有的本地連通元件候選器
    # 正好補這類圖。只收完全未被 layout 命中的候選，避免表格與已偵測圖重複。
    layout_boxes = [tuple(b) for _, b, _ in candidates]
    for b in pyexam.figure_candidates(img, dpi=300):
        if not any(overlap(b, x) / max(1, (b[2]-b[0])*(b[3]-b[1])) > .15 for x in layout_boxes):
            candidates.append(("figure", list(b), True))
    boxes = []
    for c, b, _fallback in candidates:
        kind = "table" if c == "table" else "figure"
        # 版面模型的框本身就準（掃描卷也是）；只在頁寬 2.5% 內小幅外擴補回被切到的筆畫，
        # 不用連通塊整塊併入——掃描卷的外框或文字一連通，框就會爆到整頁。
        m = int(.025 * img.shape[1])
        column = (b[0] - m, b[1] - m, b[2] + m, b[3] + m)
        candidate = refine_box(img, b, kind=kind, ocr_lines=ocr_lines, column=column)
        boxes.append(candidate)
    boxes = merge_figure_panels(merge(boxes), gap=int(.045 * img.shape[1]), ocr_lines=ocr_lines)
    boxes = merge_option_panels(boxes, ocr_lines, img.shape[1], img.shape[0])
    p = int(.006 * img.shape[1])      # 手動截圖四周都會留一點白
    boxes = [(max(0, b[0] - p), max(0, b[1] - p), min(img.shape[1], b[2] + p), min(img.shape[0], b[3] + p)) for b in boxes]
    H = img.shape[0]   # 頁首小圖（校徽、QR、圖示）不是題目的圖
    boxes = [b for b in boxes if not (b[3] < .14 * H and b[3] - b[1] < .06 * H)]
    return boxes, dets


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
    a = ap.parse_args()
    os.makedirs(a.o, exist_ok=True)
    pages = []
    doc = pymupdf.open(a.pdf)
    wanted = page_numbers(a.pages, len(doc))
    for pn, page in enumerate(doc, 1):
        if pn not in wanted:
            continue
        pix = page.get_pixmap(dpi=a.dpi)
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3][:, :, ::-1].copy()
        raw = []
        boxes, _ = crop_page(img, a.model, raw)
        k_pt = 72 / a.dpi; items = []
        for k, (x0, y0, x1, y1) in enumerate(boxes, 1):
            path = os.path.join(a.o, f"p{pn}_fig{k}.png"); cv2.imwrite(path, img[y0:y1, x0:x1])
            items.append({"x": x0 * k_pt, "y": y0 * k_pt, "w": (x1 - x0) * k_pt, "h": (y1 - y0) * k_pt,
                          "qno": guess_qno((x0, y0, x1, y1), raw, img.shape[1]), "file": path})
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
