#!/usr/bin/env python3
"""候選圖框的保守精修與無 AI 自評。"""
from __future__ import annotations

import argparse
import json
import re
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


RE_CAPTION = re.compile(r"^[(（]?\s*[圖表]\s*[(（]?\s*[一二三四五六七八九十\d]")


def text_like(text):
    """像題目文字（不是圖內標籤或圖說）：句尾標點、選項代號、或 ≥8 個中文字；「圖(十三)」「表一」這類圖說不算。"""
    t = str(text).strip()
    if RE_CAPTION.match(t) and len(t) <= 16:
        return False
    cjk = len(re.findall(r"[\u4e00-\u9fff]", t))
    return cjk >= 8 or (cjk >= 2 and bool(re.search(r"[。？?；：:]$", t))) or bool(re.search(r"[(（]\s*[A-DＡ-Ｄ]\s*[)）]\s*\S", t))


def option_caption(text):
    """圖選項底下的短圖說，如「(B)加熱」：只有代號加幾個字，不是文字選項（有數字的「(A)136大卡」不算）。"""
    t = str(text).strip()
    return bool(re.match(r"^[(（]\s*[A-DＡ-Ｄ]\s*[)）]\s*[\u4e00-\u9fff]{0,4}$", t))


def _drop_frames(ink, box):
    """題組外框：比候選框長很多的直／橫線不算圖的墨跡，否則框會沿著外框一路長到整個題組。"""
    x0, y0, x1, y1 = box
    k = np.ones((1, max(120, int(1.3 * (x1 - x0)))), np.uint8)
    v = np.ones((max(120, int(1.3 * (y1 - y0))), 1), np.uint8)
    m = ink.astype(np.uint8)
    lines = cv2.morphologyEx(m, cv2.MORPH_OPEN, k) | cv2.morphologyEx(m, cv2.MORPH_OPEN, v)
    return ink & ~(cv2.dilate(lines, np.ones((3, 3), np.uint8)) > 0)


def shrink_to_figure(ink, box, ocr_lines):
    """框內挖掉句子行後，把框收到剩下的墨跡（真正的圖）範圍：版面模型常把圖旁邊的題目文字一起框進來。
    圖內短標籤不算句子，會留著。剩下的墨跡太少（整個框都是字）就不動。"""
    x0, y0, x1, y1 = box
    sub = ink[y0:y1, x0:x1].copy()
    if sub.size == 0:
        return box
    total = sub.sum()
    for lx0, ly0, lx1, ly1, text in ocr_lines:
        if text_like(text) and min(lx1, x1) - max(lx0, x0) > 0 and min(ly1, y1) - max(ly0, y0) > 0:
            sub[max(0, ly0 - y0 - 3):max(0, ly1 - y0 + 3), max(0, lx0 - x0 - 3):max(0, lx1 - x0 + 3)] = False
    if total == 0 or sub.sum() < .25 * total:
        return box
    cols = np.where(sub.mean(0) > .004)[0]; rows = np.where(sub.mean(1) > .004)[0]
    if len(cols) < 20 or len(rows) < 20:
        return box
    out = (x0 + int(cols[0]), y0 + int(rows[0]), x0 + int(cols[-1]) + 1, y0 + int(rows[-1]) + 1)
    for lx0, ly0, lx1, ly1, text in ocr_lines:      # 圖說屬於圖，墨跡淡也要留
        if RE_CAPTION.match(str(text).strip()) and lx0 >= x0 and lx1 <= x1 and ly0 >= y0 and ly1 <= y1:
            out = _merge(out, (lx0, ly0, lx1, ly1))
    return out


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
    colw = max(column[2] - column[0], w / 2)
    column = (max(0, column[0]), max(0, column[1]), min(w, column[2]), min(h, column[3]))
    # 圖號、圖說、軸標、(A) 等短且緊鄰的 OCR 框可併入；長正文一律不併。
    for line in ocr_lines:
        lx0, ly0, lx1, ly1, text = line[:5]
        lw = lx1 - lx0
        short = lw <= .60 * colw and len(str(text).strip()) <= 24
        marker = str(text).strip().startswith(("圖", "Fig", "(")) or bool(RE_CAPTION.match(str(text).strip()))
        # 旁邊的文字選項「(D)272卡。」也以括號開頭；沒碰到框的句子行不是圖的標籤。
        if text_like(text) and (min(lx1, x1) <= max(lx0, x0) or min(ly1, y1) <= max(ly0, y0)):
            continue
        # 「附圖為…」和 (A)文字選項在窄欄也可能低於 42% 欄寬；非圖號
        # 只接受真正短的軸名／代號，才不會在 grow 前先把題幹併進來。
        aligned = min(lx1, x1) > max(lx0, x0) or min(ly1, y1) > max(ly0, y0)   # 斜對角靠近的是別的東西（上面表格的格子）
        if short and aligned and (marker or (lw < .25 * colw and len(str(text).strip()) <= 12)) and _touch_or_near((x0,y0,x1,y1), (lx0,ly0,lx1,ly1), 34):
            x0, y0, x1, y1 = _merge((x0,y0,x1,y1), (lx0,ly0,lx1,ly1))
    # 不跨正文行：把可用欄位限制在候選附近的最近完整正文行之間。
    left, top, right, bottom = column
    for lx0, ly0, lx1, ly1, text in ocr_lines:
        # 掃描卷的一行題幹有時只佔欄寬 35%（右欄縮排題組），不能只靠
        # 寬度判斷；長辨識字串同樣是不可跨越的正文停損線。圖內標籤通常短。
        inside = max(0, min(lx1, x1) - max(lx0, x0)) * max(0, min(ly1, y1) - max(ly0, y0)) > .5 * max(1, (lx1 - lx0) * (ly1 - ly0))
        body = lx1-lx0 > .60 * colw or ((lx1-lx0 > .35 * colw) and len(str(text).strip()) >= 18) \
            or (kind != "table" and not inside and text_like(text))
        if body and not (lx1 < x0 or lx0 > x1):
            if ly1 <= y0: top = max(top, ly1)
            elif ly0 >= y1: bottom = min(bottom, ly0)
        # 題目文字在同一水平帶時，也不允許擴入它的行框。
        if body and not (ly1 < y0 or ly0 > y1):
            if lx1 <= x0: left = max(left, lx1)
            elif lx0 >= x1: right = min(right, lx0)
    limit = (left, top, right, bottom)
    ink = _ink(image)
    if kind != "table":
        ink = _drop_frames(ink, (x0, y0, x1, y1))
    out = _grow_to_blank(ink, (x0,y0,x1,y1), limit)
    if kind != "table":                 # 框的上下緣還有題目文字行（版面模型自己框進來的）就切掉
        ax0, ay0, ax1, ay1 = out; hh = ay1 - ay0
        for lx0, ly0, lx1, ly1, text in ocr_lines:
            # 只擦到框邊的句子（別欄的題目、圖內標籤和選項被 OCR 讀成一行）不能拿來切圖
            if not text_like(text) or min(lx1, ax1) - max(lx0, ax0) < .5 * min(lx1 - lx0, ax1 - ax0):
                continue
            if ay0 - 5 <= ly0 and (ly0 + ly1) / 2 < ay0 + .25 * hh:
                ay0 = max(ay0, ly1 + 4)
            elif ly1 <= ay1 + 5 and (ly0 + ly1) / 2 > ay1 - .25 * hh:
                ay1 = min(ay1, ly0 - 4)
        if ay1 - ay0 > 40:
            out = (ax0, ay0, ax1, ay1)
        out = shrink_to_figure(ink, clip(out, image.shape), ocr_lines)
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
