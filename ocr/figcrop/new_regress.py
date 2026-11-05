#!/usr/bin/env python3
"""沒有手動答案的新卷：大量回歸比對（不靠 AI）。只看「改規則前後有變的框」，做低解析度拼貼一次看完。

新卷放 ocr/results/figcrop_newset/{work,hold}/*.pdf（不進 git）；hold 是隨機留下的 20%，只看總數，
不輸出逐卷清單、不出拼貼，不可拿來調規則。

python ocr/figcrop/new_regress.py run <tag> [--part work|hold|all] [--jobs 2] [--no-cache]
python ocr/figcrop/new_regress.py diff <舊tag> <新tag> [--part work|hold]      # 有變的框＋拼貼（hold 只給總數）
python ocr/figcrop/new_regress.py suspects <tag> [--top 48]                    # 疑似切到墨跡／框到正文的框（work）

結果在 ocr/results/figcrop_newset/runs/<tag>/<part>/<卷>.json；框座標是轉正後頁面的 300 dpi 像素。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import pymupdf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import crop       # noqa: E402
import evalcache  # noqa: E402
from refine import edge_cut_ink  # noqa: E402

SET = ROOT / "ocr/results/figcrop_newset"
DPI = 300


def render(page, dpi=DPI):
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    return cv2.cvtColor(np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3], cv2.COLOR_RGB2BGR)


def _limit_threads(n):
    """平行跑時每個行程只給 n 條 onnxruntime 執行緒；預設是全部核心，多行程互搶反而更慢。
    rapidocr／rapid_layout 只在有設定時才改 SessionOptions，所以改預設值就夠了。"""
    import onnxruntime as ort
    base = ort.SessionOptions

    class Limited(base):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.intra_op_num_threads = n; self.inter_op_num_threads = 1
    ort.SessionOptions = Limited
    cv2.setNumThreads(n)


def run_pdf(args):
    pdf, out, use_cache = args
    if use_cache:
        evalcache.enable(crop)
    doc = pymupdf.open(pdf); pages = []
    for pn, page in enumerate(doc, 1):
        dpi = crop.page_dpi(page, DPI); img = render(page, dpi)       # 和正式切圖同解析度（超大清稿頁 72dpi）
        # 同 crop.crop_items：先轉正再切；另外留 raw 算正文行數，順便給拼貼用的轉向 k
        first = []; crop.text_lines(img, first)
        k = 0 if crop.looks_upright(first) else crop.page_rotation(img)
        up = np.rot90(img, k).copy() if k else img
        raw = []; boxes, _ = crop.crop_page(up, raw=raw, ocr=first if k == 0 else None)
        lines = [(l, bool(crop.RE_TEXT_OPTION.search(str(t)))) for l, t in raw]
        items = []
        for b in boxes:
            b = tuple(int(v) for v in b)
            body = sum(1 for l, is_body in lines if is_body and l[0] >= b[0] and l[2] <= b[2] and l[1] >= b[1] and l[3] <= b[3])
            items.append({"box": b, "qno": crop.guess_qno(b, raw, up.shape[1]), "edge": int(edge_cut_ink(up, b)), "body": body})
        # raw（OCR 行框＋文字）一起存：之後要加新的自評指標不用重跑整批
        pages.append({"page": pn, "k": int(k), "w": up.shape[1], "h": up.shape[0], "dpi": dpi,
                      "text_layer": len(page.get_text().strip()) > 40, "items": items,
                      "raw": [[[int(v) for v in l], str(t)] for l, t in raw]})
    out.write_text(json.dumps({"pdf": str(pdf), "pages": pages}, ensure_ascii=False), encoding="utf-8")
    return pdf.stem, sum(len(p["items"]) for p in pages), len(pages)


def parts(part):
    return ["work", "hold"] if part == "all" else [part]


def part_dir(part):
    """tune＝交接文件的「調規則用的 8 份」，放 ocr/results/figcrop_tune（不進 git）。"""
    return ROOT / "ocr/results/figcrop_tune" if part == "tune" else SET / part


def cmd_run(a):
    jobs = []
    for part in parts(a.part):
        out = SET / "runs" / a.tag / part; out.mkdir(parents=True, exist_ok=True)
        for pdf in sorted(part_dir(part).glob("*.pdf")):
            target = out / (pdf.stem + ".json")
            if not target.exists():
                jobs.append((pdf, target, not a.no_cache))
    print(f"{a.tag}：待跑 {len(jobs)} 份", flush=True)
    done = 0
    threads = max(1, (os.cpu_count() or 4) // a.jobs)
    with ProcessPoolExecutor(max_workers=a.jobs, initializer=_limit_threads, initargs=(threads,)) as ex:
        for stem, n, pg in ex.map(run_pdf, jobs):
            done += 1
            hold = jobs[done - 1][1].parent.name == "hold"      # 保留卷不印卷名與框數
            print(f"[{done}/{len(jobs)}]" + ("" if hold else f" {stem}: {pg} 頁 {n} 框"), flush=True)


def load(tag, part):
    d = SET / "runs" / tag / part
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))}


CJK = __import__("re").compile(r"[一-鿿]")


def edge_cuts(page, box):
    """框的上／下緣把一行字切成兩半（字有 20%～80% 在框內、水平重疊過半行）：
    交接文件「框上緣擦到半行題幹」的代理指標。回傳 (上緣切到行數, 下緣切到行數)。"""
    x0, y0, x1, y1 = box; top = bottom = 0
    for (l0, l2, l1, l3), t in page.get("raw", []):
        if len(CJK.findall(t)) < 2 or l3 - l2 < 8:
            continue
        if min(l1, x1) - max(l0, x0) < .5 * (l1 - l0):
            continue
        h = l3 - l2
        if l2 < y0 < l3 and .2 < (l3 - y0) / h < .8:
            top += 1
        if l2 < y1 < l3 and .2 < (y1 - l2) / h < .8:
            bottom += 1
    return top, bottom


def cmd_stats(a):
    for part in a.parts:
        data = load(a.tag, part)
        if not data:
            continue
        n = {"卷": len(data), "頁": 0, "框": 0, "上緣切字": 0, "下緣切字": 0, "切到墨跡": 0, "框到正文": 0, "無題號": 0}
        scan = {"文字層": [0, 0], "圖片型": [0, 0]}     # [框, 上下緣切字的框]
        for d in data.values():
            for p in d["pages"]:
                n["頁"] += 1
                kind = "文字層" if p["text_layer"] else "圖片型"
                for it in p["items"]:
                    t, b = edge_cuts(p, it["box"]); n["框"] += 1
                    n["上緣切字"] += t > 0; n["下緣切字"] += b > 0
                    n["切到墨跡"] += it["edge"] > 0; n["框到正文"] += it["body"] > 0; n["無題號"] += it["qno"] is None
                    scan[kind][0] += 1; scan[kind][1] += (t or b) > 0
        print(f"{a.tag}/{part}：" + "、".join(f"{k} {v}" for k, v in n.items())
              + "｜" + "、".join(f"{k} {v[1]}/{v[0]}" for k, v in scan.items()))


def iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1]); x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    return inter / max(1, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def page_changes(old, new):
    """一頁的舊／新框配對：IoU≥.97 視為沒變。回傳 (沒變數, [(舊框或None, 新框或None, 題號變化)])。"""
    olds = [it for it in old["items"]]; news = [it for it in new["items"]]
    same = 0; changes = []; used = set()
    for o in olds:
        best = max(((iou(o["box"], n["box"]), j) for j, n in enumerate(news) if j not in used), default=(0, None))
        if best[0] >= .97:
            used.add(best[1]); same += 1
            if o["qno"] != news[best[1]]["qno"]:
                changes.append((o, news[best[1]], "qno"))
        elif best[0] >= .3:
            used.add(best[1]); changes.append((o, news[best[1]], "box"))
        else:
            changes.append((o, None, "removed"))
    changes += [(None, n, "added") for j, n in enumerate(news) if j not in used]
    return same, changes


def tile(pdf_doc, page, boxes, dpi=100, size=360):
    """舊框紅、新框綠，裁兩者聯集外擴一點，縮到 size 寬高內。"""
    s = dpi / DPI
    # 框是用 page["dpi"]（超大清稿頁 72）算的；縮圖照同比例縮
    tdpi = max(1, round(dpi * page.get("dpi", DPI) / DPI)); s = tdpi / page.get("dpi", DPI)   # pymupdf 的 dpi 要整數
    img = render(pdf_doc[page["page"] - 1], tdpi)
    if page["k"]:
        img = np.rot90(img, page["k"]).copy()
    xs = [v for b, _ in boxes if b for v in (b[0], b[2])]; ys = [v for b, _ in boxes if b for v in (b[1], b[3])]
    pad = 40
    x0, y0 = max(0, int((min(xs) - pad) * s)), max(0, int((min(ys) - pad) * s))
    x1, y1 = min(img.shape[1], int((max(xs) + pad) * s)), min(img.shape[0], int((max(ys) + pad) * s))
    for b, color in boxes:
        if b:
            cv2.rectangle(img, (int(b[0] * s), int(b[1] * s)), (int(b[2] * s), int(b[3] * s)), color, 2)
    t = img[y0:y1, x0:x1]
    f = min(size / max(1, t.shape[1]), size / max(1, t.shape[0]), 1.5)
    t = cv2.resize(t, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    canvas = np.full((size + 22, size, 3), 255, np.uint8)
    canvas[22:22 + t.shape[0], :t.shape[1]] = t
    return canvas


def mosaic(tiles, labels, path, cols=4):
    rows = []
    for i in range(0, len(tiles), cols):
        row = []
        for t, label in zip(tiles[i:i + cols], labels[i:i + cols]):
            t = t.copy(); cv2.putText(t, label, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, .45, (120, 0, 0), 1)
            row.append(cv2.copyMakeBorder(t, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=(200, 200, 200)))
        while len(row) < cols:
            row.append(np.full_like(row[0], 255))
        rows.append(cv2.hconcat(row))
    crop.pyexam._imwrite(str(path), cv2.vconcat(rows), [cv2.IMWRITE_JPEG_QUALITY, 80])


def cmd_diff(a):
    old, new = load(a.old, a.part), load(a.new, a.part)
    common = sorted(set(old) & set(new))
    tot = {"卷": len(common), "頁": 0, "舊框": 0, "新框": 0, "沒變": 0, "框變": 0, "新增": 0, "消失": 0, "題號變": 0}
    per = []; sheet = []
    for stem in common:
        n_changes = 0
        for po, pn in zip(old[stem]["pages"], new[stem]["pages"]):
            tot["頁"] += 1; tot["舊框"] += len(po["items"]); tot["新框"] += len(pn["items"])
            same, changes = page_changes(po, pn); tot["沒變"] += same
            for o, n, kind in changes:
                tot[{"box": "框變", "added": "新增", "removed": "消失", "qno": "題號變"}[kind]] += 1
                n_changes += 1
                if kind != "qno":
                    sheet.append((stem, pn, o, n, kind))
        if n_changes:
            per.append((n_changes, stem))
    print(f"{a.old} → {a.new}（{a.part}）：", "、".join(f"{k} {v}" for k, v in tot.items()))
    if a.part == "hold":
        return                                  # 保留卷只給總數
    for n, stem in sorted(per, reverse=True)[:15]:
        print(f"  {n:>3} 處  {stem}")
    if not sheet:
        return
    out = SET / "runs" / a.new / f"diff_vs_{a.old}"; out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.jpg"):
        f.unlink()
    docs = {}; per_sheet = a.per_sheet
    for i in range(0, len(sheet), per_sheet):
        tiles, labels = [], []
        for stem, page, o, n, kind in sheet[i:i + per_sheet]:
            doc = docs.setdefault(stem, pymupdf.open(new[stem]["pdf"]))
            tiles.append(tile(doc, page, [(o and o["box"], (0, 0, 220)), (n and n["box"], (0, 170, 0))]))
            labels.append(f"{i + len(tiles)} {kind} p{page['page']} {stem[:14]}")
        mosaic(tiles, labels, out / f"sheet_{i // per_sheet + 1:02d}.jpg")
    (out / "list.json").write_text(json.dumps([{"n": i + 1, "exam": s, "page": p["page"], "kind": k, "old": o and o["box"], "new": n and n["box"]}
                                               for i, (s, p, o, n, k) in enumerate(sheet)], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"拼貼 {len(sheet)} 格 → {out}（紅＝舊、綠＝新）")


def cmd_suspects(a):
    data = {**load(a.tag, "work"), **load(a.tag, "tune")}; rows = []
    for stem, d in data.items():
        for p in d["pages"]:
            for it in p["items"]:
                top, bottom = edge_cuts(p, it["box"])
                if a.only == "topcut":
                    score = top * 3 + bottom
                elif a.only == "body":
                    score = it["body"]
                elif a.only == "edge":
                    score = it["edge"] if it["edge"] >= 5 else 0
                else:
                    score = it["edge"] * 2 + it["body"] + (it["qno"] is None) + 3 * (top + bottom)
                if score:
                    rows.append((score, stem, p, {**it, "cut": (top, bottom)}))
    rows.sort(key=lambda r: -r[0])
    print(f"{a.tag}：疑似 {len(rows)} 框（切到墨跡 {sum(1 for r in rows if r[3]['edge'])}、框到正文 {sum(1 for r in rows if r[3]['body'])}、無題號 {sum(1 for r in rows if r[3]['qno'] is None)}）")
    out = SET / "runs" / a.tag / "suspects"; out.mkdir(parents=True, exist_ok=True)
    docs = {}; tiles, labels = [], []
    for score, stem, p, it in rows[:a.top]:
        doc = docs.setdefault(stem, pymupdf.open(data[stem]["pdf"]))
        tiles.append(tile(doc, p, [(it["box"], (0, 0, 220))]))
        labels.append(f"{len(tiles)} c{it['cut'][0]}/{it['cut'][1]} e{it['edge']} b{it['body']} q{it['qno']} p{p['page']} {stem[:10]}")
    if tiles:
        for i in range(0, len(tiles), a.per_sheet):
            mosaic(tiles[i:i + a.per_sheet], labels[i:i + a.per_sheet], out / f"sheet_{i // a.per_sheet + 1:02d}.jpg")
        print(f"拼貼前 {len(tiles)} 名 → {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("tag"); r.add_argument("--part", default="work", choices=["work", "hold", "all", "tune"])
    r.add_argument("--jobs", type=int, default=max(1, min(4, (os.cpu_count() or 2) // 4))); r.add_argument("--no-cache", action="store_true")
    d = sub.add_parser("diff"); d.add_argument("old"); d.add_argument("new"); d.add_argument("--part", default="work", choices=["work", "hold", "tune"])
    d.add_argument("--per-sheet", type=int, default=24)
    s = sub.add_parser("suspects"); s.add_argument("tag"); s.add_argument("--top", type=int, default=48); s.add_argument("--per-sheet", type=int, default=24)
    s.add_argument("--only", choices=["all", "topcut", "edge", "body"], default="all")
    t = sub.add_parser("stats"); t.add_argument("tag"); t.add_argument("--parts", nargs="+", default=["tune", "work", "hold"])
    a = ap.parse_args()
    {"run": cmd_run, "diff": cmd_diff, "suspects": cmd_suspects, "stats": cmd_stats}[a.cmd](a)


if __name__ == "__main__":
    main()
