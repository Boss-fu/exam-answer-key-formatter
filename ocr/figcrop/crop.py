#!/usr/bin/env python3
"""自動截圖：考卷頁 → 圖形／表格裁圖（取代手動截圖）。
偵測：PP-DocLayout v3（rapid_layout，本機 ONNX）。精修：跟框重疊的墨跡連通塊整塊納入（不缺角缺邊、不漏圖內字），
大部分在框外的正文行不納入（不多字），框旁短標籤（圖號、軸標、(A)(B)）併入，四周留白。
    python ocr/figcrop/crop.py 考卷.pdf -o 輸出資料夾 [--dpi 300] [--model pp_doc_layoutv3]"""
import argparse, os, sys
import cv2, numpy as np, pymupdf
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import ppocr_engine

KINDS = {"figure", "image", "chart", "table", "figure_caption", "table_caption"}
_ENG = {}


def detect(img, model):
    from rapid_layout import RapidLayout
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


RE_QNUM = re.compile(r"^[\s(（A-Da-dＡ-Ｄ)）|lI]{0,6}?(\d{1,2})\s*[.．、]")   # 「(D)1.」「( )4.」「6.」


def guess_qno(box, raw_lines, W):
    """題號：同一欄、圖的上方（或同高左側）最近的「N.」開頭行。"""
    cx = (box[0] + box[2]) / 2; best = None
    for (l, text) in raw_lines:
        m = RE_QNUM.match(text)
        if not m or l[1] > box[1] + 10:
            continue
        if (l[0] < W / 2) != (cx < W / 2) and abs(l[0] - box[0]) > W * .25:
            continue
        d = box[1] - l[1]
        if best is None or abs(d) < abs(best[0]):
            best = (d, int(m.group(1)))
    return best[1] if best else None


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


def crop_page(img, model="pp_doc_layoutv3", raw=None):
    dets = detect(img, model)
    lines = text_lines(img, raw)
    boxes = merge([refine(img, b, lines) for c, b, s in dets if c in ("figure", "image", "chart", "table")])
    return boxes, dets


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("pdf"); ap.add_argument("-o", required=True)
    ap.add_argument("--dpi", type=int, default=300); ap.add_argument("--model", default="pp_doc_layoutv3")
    ap.add_argument("--json", action="store_true", help="輸出 JSON（座標用 PDF 點、裁圖 base64 PNG、推測題號）給茲茲 OCR／raw.html")
    a = ap.parse_args()
    os.makedirs(a.o, exist_ok=True)
    pages = []
    for pn, page in enumerate(pymupdf.open(a.pdf), 1):
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
