#!/usr/bin/env python3
"""將 figcrop 的 gt_eval.json 製成可在瀏覽器人工複核的單頁報表。"""
from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import pymupdf

ROOT = Path(__file__).resolve().parents[2]


def as_box(value):
    """接受 JSON list，並拒絕不完整或反向的座標。"""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = tuple(int(round(float(x))) for x in value)
    except (TypeError, ValueError):
        return None
    return box if box[2] > box[0] and box[3] > box[1] else None


def crop(image, box):
    if box is None:
        return None
    h, w = image.shape[:2]
    x0, y0, x1, y1 = max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3])
    return image[y0:y1, x0:x1].copy() if x1 > x0 and y1 > y0 else None


def save_jpeg(path, image):
    """存成長邊約 700px 的 JPEG；回傳相對於 HTML 的 URL。"""
    if image is None or not image.size:
        return None
    h, w = image.shape[:2]
    if max(h, w) > 700:
        ratio = 700 / max(h, w)
        image = cv2.resize(image, (round(w * ratio), round(h * ratio)), interpolation=cv2.INTER_AREA)
    cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tofile(str(path))
    return "img/" + path.name


def overview(image, gt, auto):
    boxes = [b for b in (gt, auto) if b]
    if not boxes:
        return None
    h, w = image.shape[:2]
    x0 = max(0, min(b[0] for b in boxes) - 80); y0 = max(0, min(b[1] for b in boxes) - 80)
    x1 = min(w, max(b[2] for b in boxes) + 80); y1 = min(h, max(b[3] for b in boxes) + 80)
    region = image[y0:y1, x0:x1].copy()
    for box, color in ((gt, (0, 170, 0)), (auto, (0, 0, 220))):
        if box:
            cv2.rectangle(region, (box[0] - x0, box[1] - y0), (box[2] - x0, box[3] - y0), color, 5)
    return region


def render_page(doc, page_number):
    pix = doc[page_number - 1].get_pixmap(dpi=300, alpha=False)
    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def percent(value):
    return "—" if value is None else f"{float(value) * 100:.1f}%"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval", default=str(ROOT / "ocr/results/figcrop_gt/baseline.json"), help="gt_eval.json 路徑")
    ap.add_argument("--out", default=str(ROOT / "ocr/results/figcrop_review/index.html"), help="輸出 HTML")
    ap.add_argument("--limit", type=int, help="僅處理前 N 筆 row（供快速測試）")
    args = ap.parse_args()
    if args.limit is not None and args.limit < 0:
        ap.error("--limit 必須是非負整數")

    source = Path(args.eval)
    data = json.loads(source.read_text(encoding="utf-8"))
    rows = data.get("rows", [])
    if not isinstance(rows, list):
        raise ValueError("評測 JSON 的 rows 必須是陣列")
    if args.limit is not None:
        rows = rows[:args.limit]
    out = Path(args.out); img_dir = out.parent / "img"
    img_dir.mkdir(parents=True, exist_ok=True)

    # 同一頁的所有卡片一起處理：頁面只 render 一次，寫完後立即釋放記憶體。
    groups = defaultdict(list)
    for index, row in enumerate(rows):
        groups[(str(row.get("exam", "未知考卷")), int(row.get("page", 0) or 0))].append((index, row))
    cards = [None] * len(rows)
    docs = {}
    gt_root = ROOT / "ocr/results/figcrop_gt"
    try:
        for (exam, page_no), entries in groups.items():
            page_image = None
            pdf = gt_root / exam / "exam.pdf"
            if page_no > 0 and pdf.exists():
                doc = docs.setdefault(exam, pymupdf.open(pdf))
                if page_no <= len(doc):
                    page_image = render_page(doc, page_no)
            for index, row in entries:
                gt, auto = as_box(row.get("gt")), as_box(row.get("auto"))
                prefix = f"{index:04d}"
                manual_url = auto_url = overview_url = None
                if not row.get("extra"):
                    manual_path = gt_root / exam / "manual" / str(row.get("file", ""))
                    manual = cv2.imdecode(np.fromfile(str(manual_path), dtype=np.uint8), cv2.IMREAD_COLOR) if manual_path.exists() else None
                    manual_url = save_jpeg(img_dir / f"{prefix}_manual.jpg", manual)
                if page_image is not None:
                    auto_url = save_jpeg(img_dir / f"{prefix}_auto.jpg", crop(page_image, auto))
                    overview_url = save_jpeg(img_dir / f"{prefix}_overview.jpg", overview(page_image, gt, auto))
                cards[index] = {
                    "exam": exam, "page": page_no, "qno": row.get("qno"), "auto_qno": row.get("auto_qno"),
                    "iou": row.get("iou"), "hit": bool(row.get("hit")), "extra": bool(row.get("extra")),
                    "qno_ok": row.get("qno_ok"), "manual": manual_url, "auto": auto_url, "overview": overview_url,
                }
            del page_image
    finally:
        for doc in docs.values():
            doc.close()

    manual_rows = [r for r in rows if not r.get("extra") and "hit" in r]
    matched = [r for r in manual_rows if r.get("hit")]
    summary = data.get("summary", {})
    pass_rate = summary.get("recall_iou80", len(matched) / len(manual_rows) if manual_rows else None)
    strict_rate = summary.get("recall_strict")
    if strict_rate is None and manual_rows:
        strict_rate = sum(float(r.get("iou_strict", -1)) >= .8 for r in manual_rows) / len(manual_rows)
    qno_rate = summary.get("qno_accuracy", sum(bool(r.get("qno_ok")) for r in matched) / len(matched) if matched else None)
    per_exam = defaultdict(lambda: [0, 0])
    for r in manual_rows:
        per_exam[str(r.get("exam", "未知考卷"))][1] += 1
        per_exam[str(r.get("exam", "未知考卷"))][0] += bool(r.get("hit"))
    stats = {"pass": percent(pass_rate), "strict": percent(strict_rate), "extra": str(summary.get("extras", sum(bool(r.get("extra")) for r in rows))), "qno": percent(qno_rate), "exams": sorted(per_exam.items())}
    page_data = json.dumps(cards, ensure_ascii=False).replace("</", "<\\/")
    stats_json = json.dumps(stats, ensure_ascii=False).replace("</", "<\\/")
    out.write_text(f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>圖表裁切人工複核</title>
<style>:root{{color-scheme:light dark;--bg:#f5f7f8;--card:#fff;--ink:#18242b;--muted:#61717b;--line:#d8e0e4;--accent:#076e8c}}@media(prefers-color-scheme:dark){{:root{{--bg:#11191d;--card:#18242a;--ink:#ecf3f5;--muted:#a8bbc2;--line:#34464d;--accent:#62cce8}}}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px system-ui,sans-serif}}main{{max-width:1280px;margin:auto;padding:18px}}h1{{font-size:1.45rem;margin:0 0 12px}}.stats,.filters{{display:flex;flex-wrap:wrap;gap:9px;margin:10px 0}}.stat,.exam{{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:8px 11px}}.stat b{{display:block;font-size:1.2rem}}button,select{{font:inherit;padding:7px 10px;border-radius:7px;border:1px solid var(--line);background:var(--card);color:var(--ink)}}button.active{{background:var(--accent);color:var(--bg)}}#count{{color:var(--muted)}}.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:14px 0;overflow:hidden}}.meta{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px}}.tag{{background:color-mix(in srgb,var(--accent) 12%,transparent);padding:3px 7px;border-radius:5px}}.images{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}figure{{margin:0;min-width:0}}figcaption{{font-weight:600;margin:0 0 5px}}img{{display:block;max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px}}.overview{{margin-top:12px}}.empty{{color:var(--muted);font-style:italic}}@media(max-width:600px){{main{{padding:12px}}.images{{grid-template-columns:1fr}}}}</style>
<main><h1>圖表裁切人工複核</h1><div id="stats" class="stats"></div><div id="exams" class="stats"></div><div class="filters"><button data-f="miss-multi" class="active">未命中／多框</button><button data-f="miss">未命中</button><button data-f="extra">多框</button><button data-f="qno">題號錯</button><button data-f="all">全部</button><select id="exam"><option value="">所有考卷</option></select><span id="count"></span></div><section id="cards"></section></main>
<script>const rows={page_data},stats={stats_json};const esc=s=>String(s??'—').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));const statsEl=document.querySelector('#stats');[['合格率',stats.pass],['嚴格率',stats.strict],['多框',stats.extra],['題號準確率',stats.qno]].forEach(x=>statsEl.insertAdjacentHTML('beforeend',`<div class="stat">${{x[0]}}<b>${{x[1]}}</b></div>`));const sel=document.querySelector('#exam');stats.exams.forEach(x=>{{sel.add(new Option(`${{x[0]}}（${{x[1][0]}}/${{x[1][1]}} 命中）`,x[0]));document.querySelector('#exams').insertAdjacentHTML('beforeend',`<span class="exam">${{esc(x[0])}}：${{x[1][0]}}/${{x[1][1]}} 命中</span>`)}});let filter='miss-multi';function show(r){{if(sel.value&&r.exam!==sel.value)return false;if(filter==='all')return true;if(filter==='miss')return !r.extra&&!r.hit;if(filter==='extra')return r.extra;if(filter==='qno')return !r.extra&&r.hit&&r.qno_ok===false;return r.extra||!r.hit}}function image(label,url){{return url?`<figure><figcaption>${{label}}</figcaption><img loading="lazy" src="${{url}}"></figure>`:`<figure><figcaption>${{label}}</figcaption><span class="empty">無可用影像</span></figure>`}}function render(){{const shown=rows.filter(show);document.querySelector('#count').textContent=`顯示 ${{shown.length}} / ${{rows.length}} 筆`;document.querySelector('#cards').innerHTML=shown.map(r=>`<article class="card"><div class="meta"><span class="tag">${{esc(r.exam)}}</span><span class="tag">第 ${{esc(r.page)}} 頁</span>${{r.extra?'':`<span class="tag">題號 ${{esc(r.qno)}}</span><span class="tag">自動題號 ${{esc(r.auto_qno)}}</span>`}}<span class="tag">IoU ${{r.iou==null?'—':Number(r.iou).toFixed(3)}}</span></div><div class="images">${{r.extra?'':image('手動圖片',r.manual)}}${{image('自動裁切',r.auto)}}</div><div class="overview">${{image('頁面區域（綠：手動，紅：自動）',r.overview)}}</div></article>`).join('')||'<p class="empty">沒有符合篩選的資料。</p>'}}document.querySelectorAll('button').forEach(b=>b.onclick=()=>{{filter=b.dataset.f;document.querySelectorAll('button').forEach(x=>x.classList.toggle('active',x===b));render()}});sel.onchange=render;render();</script>''', encoding="utf-8")
    print(f"已建立 {out}（{len(cards)} 筆；圖片在 {img_dir}）")


if __name__ == "__main__":
    main()
