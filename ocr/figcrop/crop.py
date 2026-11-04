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
    from .refine import edge_cut_ink, refine_box
except ImportError:
    from refine import edge_cut_ink, refine_box

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


def guess_qno(box, raw_lines, W):
    """取同欄圖上最近題號；題組引言「第 N～M 題」一律歸 N。"""
    cx = (box[0] + box[2]) / 2
    # 以圖的中心判欄，不用題號 x 座標；右欄題號常因掃描歪斜而偏左。
    side = cx >= W / 2
    best = None; fallback = None
    for (l, text) in raw_lines:
        if l[1] > box[1] + 12:
            continue
        clean = str(text).replace("［", "(").replace("］", ")")
        m = RE_GROUP.search(clean) or RE_QNUM.match(clean)
        if not m:
            continue
        d = box[1] - l[1]
        row = (d, int(m.group(1)))
        # 圖在右側、題號在左側是單欄橫跨整頁題目的常態；先吃同欄，
        # 沒有才退回最近的任何欄題號，避免因此整批 qno 都是 null。
        if ((l[0] + l[2]) / 2 >= W / 2) == side and (best is None or d < best[0]):
            best = row
        if fallback is None or d < fallback[0]:
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


def merge_figure_panels(boxes, gap=110):
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
                if vertical > .45 * min(a[3]-a[1], b[3]-b[1]) and 0 <= horiz_gap <= gap:
                    boxes[i] = (min(a[0],b[0]), min(a[1],b[1]), max(a[2],b[2]), max(a[3],b[3]))
                    boxes.pop(j); changed = True; break
            if changed: break
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
        # 先排除明確的題目句／文字選項，再讓保守精修補回框線、軸標與圖號。
        # 單靠 layout 框會把「附圖為…？」的整段題幹一併當圖。
        candidate = refine(img, b, lines, pad=4)
        half = img.shape[1] // 2
        column = (0, 0, half, img.shape[0]) if (candidate[0] + candidate[2]) / 2 < half else (half, 0, img.shape[1], img.shape[0])
        candidate = refine_box(img, candidate, kind=kind, ocr_lines=ocr_lines, column=column)
        # 確認 4px 邊帶沒有截斷元件；仍有斷墨時採保守舊式連通塊擴張。
        if edge_cut_ink(img, candidate, band=4):
            candidate = refine(img, candidate, lines, pad=10)
        boxes.append(candidate)
    boxes = merge_figure_panels(merge(boxes))
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
