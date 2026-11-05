#!/usr/bin/env python3
"""沒有手動標準答案的新卷：自動切圖後畫出編號紅框，交給 AGY（Gemini）逐頁檢查，統計判定。

python ocr/figcrop/wild_run.py crop <pdf...>   # 切圖、畫總覽，寫 <out>/<卷>/items.json 與 pages/
python ocr/figcrop/wild_run.py review          # 每頁請 agy 判定（姓名列先遮掉），寫 pages/pN.json（已判過的跳過）
python ocr/figcrop/wild_run.py report          # 統計並列出有問題的框

AGY 的判定只是第二意見，不是標準答案；這裡的分數不能拿來和 gt_eval 的手動分數比，也不進 gate。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import pymupdf

try:
    from . import crop, evalcache
except ImportError:
    import crop, evalcache

ROOT = Path(__file__).resolve().parents[2]
PROMPT = """讀取目前資料夾裡的 {img}。這是一頁國中考卷，紅框是自動切圖的結果，每個紅框左上角有編號。
切圖的目標是取代人工截圖：框住題目用到的圖或表，不多不少。頁面可能整頁旋轉，請自行判斷方向。

逐一判定每個紅框，verdict 只能是：
- ok：完整框住圖或表（含圖內標籤）。圖號如「圖(一)」框到或沒框到都算 ok，四周多一點空白也算 ok。
- extra_text：框進了題目句子或文字選項。
- cut：圖、表或圖內標籤被切到，或同一張圖只框到一部分。
- merged：把不同題目的圖併在同一個框。
- not_figure：框內不是印刷的圖或表（純文字、手寫筆跡、裝飾圖示、校徽、頁首頁尾）。

再列出 missing：題目用到、但完全沒有紅框的印刷圖或表。純文字選項、答案卡、校徽不算。沒有就回空陣列。

只回一個 JSON，不要其他文字、不要程式碼區塊：
{{"boxes":[{{"n":1,"verdict":"ok","note":"有問題才寫，20 字內"}}],"missing":[{{"what":"簡述","where":"位置"}}]}}
這頁共有 {count} 個紅框。"""


def render(page, dpi=300):
    pix = page.get_pixmap(dpi=crop.page_dpi(page, dpi), alpha=False)    # 和正式切圖同解析度（超大清稿頁 72dpi）
    return cv2.cvtColor(np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3], cv2.COLOR_RGB2BGR)


def mask_identity(img, first_page, boxes=()):
    """送 AGY 前遮掉姓名、班級座號那一列。整塊塗白會蓋到圖、害 AGY 誤判成切到；第一頁找不到欄位才遮最上面一小塊。"""
    k = crop.page_rotation(img)
    keep = img.copy()      # 紅框內不遮：遮到圖會害 AGY 誤判成切到
    up = np.rot90(img, k).copy(); raw = []
    small = cv2.resize(up, None, fx=.4, fy=.4, interpolation=cv2.INTER_AREA)
    crop.text_lines(small, raw)
    rows = [l for l, t in raw if re.search(r"姓名|座號|班級|學號|[年班]\s*[_（(]?\s*班|號\s*姓", str(t))]
    for l in rows:
        h = l[3] - l[1]
        up[max(0, int((l[1] - h) / .4)):int((l[3] + h) / .4)] = 255
    if first_page and not rows:
        up[:int(.12 * up.shape[0])] = 255
    out = np.rot90(up, -k).copy()
    for x0, y0, x1, y1 in boxes:
        out[max(0, y0 - 8):y1 + 8, max(0, x0 - 8):x1 + 8] = keep[max(0, y0 - 8):y1 + 8, max(0, x0 - 8):x1 + 8]
    return out


def cmd_crop(a):
    for pdf in map(Path, a.pdfs):
        work = a.out / pdf.stem; pages = work / "pages"; pages.mkdir(parents=True, exist_ok=True)
        if (work / "items.json").exists():
            continue
        doc = pymupdf.open(pdf); rows = []
        for pn, page in enumerate(doc, 1):
            img = render(page); items = crop.crop_items(img); vis = img.copy()
            for n, it in enumerate(items, 1):
                x0, y0, x1, y1 = it["box"]
                cv2.rectangle(vis, (x0, y0), (x1, y1), (0, 0, 230), 6)
                cv2.putText(vis, str(n), (x0 + 8, y0 + 70), cv2.FONT_HERSHEY_SIMPLEX, 2.6, (0, 0, 230), 7)
                rows.append({"page": pn, "n": n, "box": it["box"], "qno": it["qno"]})
            vis = mask_identity(vis, pn == 1, [it["box"] for it in items])
            s = 2200 / max(vis.shape[:2])
            crop.pyexam._imwrite(str(pages / f"p{pn}.jpg"),cv2.resize(vis, None, fx=s, fy=s, interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 85])
        (work / "items.json").write_text(json.dumps({"pdf": str(pdf), "pages": len(doc), "items": rows}, ensure_ascii=False, indent=1, default=int), encoding="utf-8")
        print(f"{pdf.stem}: {len(doc)} 頁 {len(rows)} 框", flush=True)


def parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


def cmd_review(a):
    for items_file in sorted(a.out.glob("*/items.json")):
        work = items_file.parent; data = json.loads(items_file.read_text(encoding="utf-8"))
        for pn in range(1, data["pages"] + 1):
            result = work / "pages" / f"p{pn}.json"
            if result.exists():
                continue
            count = sum(1 for it in data["items"] if it["page"] == pn)
            run = subprocess.run(["agy", "--print-timeout", "180s", "-p", PROMPT.format(img=f"p{pn}.jpg", count=count)],
                                 cwd=work / "pages", stdin=subprocess.DEVNULL, capture_output=True, text=True)
            try:
                parsed = parse_json(run.stdout)
            except json.JSONDecodeError:
                parsed = None
            if parsed is None:          # 不寫結果檔，下次重跑還會再判
                print(f"{work.name} p{pn}: agy 沒回 JSON：{(run.stdout or run.stderr)[-120:]!r}", flush=True)
                continue
            result.write_text(json.dumps(parsed, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{work.name} p{pn}: {Counter(b.get('verdict') for b in parsed.get('boxes', []))} 漏 {len(parsed.get('missing', []))}", flush=True)


def cmd_report(a):
    total = Counter(); missing = 0; lines = []
    for items_file in sorted(a.out.glob("*/items.json")):
        work = items_file.parent; data = json.loads(items_file.read_text(encoding="utf-8")); c = Counter(); m = 0; judged = 0
        for pn in range(1, data["pages"] + 1):
            result = work / "pages" / f"p{pn}.json"
            if not result.exists():
                continue
            judged += 1; parsed = json.loads(result.read_text(encoding="utf-8"))
            for b in parsed.get("boxes", []):
                c[b.get("verdict")] += 1
                if b.get("verdict") != "ok":
                    lines.append(f"{work.name} p{pn} #{b.get('n')} {b.get('verdict')} {b.get('note', '')}")
            for x in parsed.get("missing", []):
                m += 1; lines.append(f"{work.name} p{pn} 漏 {x.get('what')}（{x.get('where')}）")
        total += c; missing += m
        print(f"{work.name[:34]:<34} 判 {judged}/{data['pages']} 頁  框 {sum(c.values()):>3}  ok {c['ok']:>3}  問題 {sum(c.values()) - c['ok']:>2}  漏 {m:>2}")
    boxes = sum(total.values())
    if boxes:
        print(f"\n合計 框 {boxes}，ok {total['ok']}（{total['ok'] / boxes:.1%}），{dict(total)}；漏 {missing}")
        print(f"AGY 判定的召回估計 ok/(框+漏) = {total['ok'] / (boxes + missing):.1%}（第二意見，不是手動標準答案）")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "ocr/results/figcrop_wild")
    ap.add_argument("--no-cache", action="store_true", help="crop 不用評測快取（OCR／版面模型／備援候選全部重算）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("crop").add_argument("pdfs", nargs="+")
    sub.add_parser("review"); sub.add_parser("report")
    a = ap.parse_args()
    if a.cmd == "crop" and not a.no_cache:
        evalcache.enable(crop)
    {"crop": cmd_crop, "review": cmd_review, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
