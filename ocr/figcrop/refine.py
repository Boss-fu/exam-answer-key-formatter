#!/usr/bin/env python3
"""候選圖框的保守精修與無 AI 自評。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np


def clip(box, shape):
    h, w = shape[:2]
    x0, y0, x1, y1 = map(int, box)
    return max(0, x0), max(0, y0), min(w, x1), min(h, y1)


def _ink(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return gray < 180


def _grow_to_blank(ink, box, limit, blank=0.003, step=2):
    """每一側只要仍有墨跡便外推；碰到正文 OCR 行或欄界就停。"""
    x0, y0, x1, y1 = clip(box, ink.shape)
    lx0, ly0, lx1, ly1 = limit
    for _ in range(300):
        changed = False
        # 各邊帶狀投影。空白帶才停止，讓框線／軸標不會貼邊被切掉。
        if x0 > lx0 and ink[y0:y1, max(lx0, x0-step):x0].mean() > blank:
            x0 -= step; changed = True
        if x1 < lx1 and ink[y0:y1, x1:min(lx1, x1+step)].mean() > blank:
            x1 += step; changed = True
        if y0 > ly0 and ink[max(ly0, y0-step):y0, x0:x1].mean() > blank:
            y0 -= step; changed = True
        if y1 < ly1 and ink[y1:min(ly1, y1+step), x0:x1].mean() > blank:
            y1 += step; changed = True
        if not changed:
            break
    return x0, y0, x1, y1


def _touch_or_near(a, b, gap=28):
    return not (a[2] + gap < b[0] or b[2] + gap < a[0] or a[3] + gap < b[1] or b[3] + gap < a[1])


def _merge(a, b):
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def refine_box(image, box, *, kind="figure", ocr_lines=(), column=None):
    """保留緊鄰短標籤，且避免跨越寬正文行／欄界。

    ocr_lines 是 (x0,y0,x1,y1,text)；寬度超過欄寬 60% 的行是硬停損線。
    """
    h, w = image.shape[:2]
    if column is None:
        column = (0, 0, w, h)
    x0, y0, x1, y1 = clip(box, image.shape)
    colw = column[2] - column[0]
    # 圖號、圖說、軸標、(A) 等短且緊鄰的 OCR 框可併入；長正文一律不併。
    for line in ocr_lines:
        lx0, ly0, lx1, ly1, text = line[:5]
        lw = lx1 - lx0
        short = lw <= .60 * colw and len(str(text).strip()) <= 24
        marker = str(text).strip().startswith(("圖", "Fig", "("))
        if short and (marker or lw < .42 * colw) and _touch_or_near((x0,y0,x1,y1), (lx0,ly0,lx1,ly1), 34):
            x0, y0, x1, y1 = _merge((x0,y0,x1,y1), (lx0,ly0,lx1,ly1))
    # 不跨正文行：把可用欄位限制在候選附近的最近完整正文行之間。
    left, top, right, bottom = column
    for lx0, ly0, lx1, ly1, _ in ocr_lines:
        if lx1-lx0 > .60 * colw and not (lx1 < x0 or lx0 > x1):
            if ly1 <= y0: top = max(top, ly1)
            elif ly0 >= y1: bottom = min(bottom, ly0)
        # 題目文字在同一水平帶時，也不允許擴入它的行框。
        if lx1-lx0 > .60 * colw and not (ly1 < y0 or ly0 > y1):
            if lx1 <= x0: left = max(left, lx1)
            elif lx0 >= x1: right = min(right, lx0)
    limit = (left, top, right, bottom)
    out = _grow_to_blank(_ink(image), (x0,y0,x1,y1), limit)
    # 表格一定以框線外緣為準，另留 6px 防止外框被裁掉。
    if kind == "table":
        out = (out[0]-6, out[1]-6, out[2]+6, out[3]+6)
    return clip(out, image.shape)


def edge_cut_ink(image, box, band=4):
    """邊緣帶有跨出裁框的連通元件時，代表可能缺角／漏字。"""
    x0,y0,x1,y1 = clip(box, image.shape); mask = _ink(image).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    bad = 0
    for x,y,w,h,area in stats[1:]:
        # 元件碰裁框邊且也延伸到框外（不是頁面邊界）才算被切。
        inside = x < x1 and x+w > x0 and y < y1 and y+h > y0
        crosses = (x < x0 < x+w or x < x1 < x+w or y < y0 < y+h or y < y1 < y+h)
        if inside and crosses and area >= 3: bad += 1
    return bad


def has_body_line(box, ocr_lines, column_width):
    x0,y0,x1,y1 = box
    return any(l[2]-l[0] > .60*column_width and l[0] >= x0 and l[2] <= x1 and l[1] >= y0 and l[3] <= y1 for l in ocr_lines)


def refine_records(image, records, ocr_lines=(), column=None):
    return [{**r, "refined_box": list(refine_box(image, r["box"], kind=r.get("kind", "figure"), ocr_lines=ocr_lines, column=column))} for r in records]


def main():
    p=argparse.ArgumentParser(); p.add_argument("image"); p.add_argument("boxes_json"); p.add_argument("--out", required=True)
    a=p.parse_args(); image=cv2.imread(a.image); data=json.loads(Path(a.boxes_json).read_text())
    records=refine_records(image, data.get("boxes", data)); Path(a.out).write_text(json.dumps(records, ensure_ascii=False, indent=2))

if __name__ == "__main__": main()
