# -*- coding: utf-8 -*-
"""考卷掃描檔的圖片工具：轉頁面、帶座標的檢視圖、裁切、去筆跡、字形修補、聯絡表。
座標一律用「工作圖」的像素座標（pages/p-<n>.png）。"""
import os, sys, glob, subprocess
import cv2, numpy as np
from PIL import Image, ImageDraw, ImageFont
Image.MAX_IMAGE_PIXELS = None
WORK = os.environ.get('EXAM_WORK', '/home/claude/exam')
PAGES = os.path.join(WORK, 'pages'); FIGS = os.path.join(WORK, 'figs'); VIEWS = os.path.join(WORK, 'views')
SANS = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
SERIF_CJK = '/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc'
SERIF_LATIN = '/usr/share/fonts/truetype/freefont/FreeSerif.ttf'
LABEL_FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'

def prep(pdf, dpi=300):
    """PDF → 灰階工作圖 pages/p-1.png…；低於 dpi 的掃描維持原解析度。回傳每頁尺寸。"""
    for d in (PAGES, FIGS, VIEWS): os.makedirs(d, exist_ok=True)
    subprocess.run(['pdftoppm', '-r', str(dpi), '-gray', '-png', pdf, os.path.join(PAGES, 'p')], check=True)
    out = []
    for f in sorted(glob.glob(os.path.join(PAGES, 'p-*.png'))):
        n = int(f.rsplit('-', 1)[1].split('.')[0]); nf = os.path.join(PAGES, f'p-{n}.png')
        if f != nf: os.rename(f, nf)
        out.append((n, Image.open(nf).size))
    return sorted(out)

def load(pg):
    a = cv2.imread(os.path.join(PAGES, f'p-{pg}.png'), 0)
    return a

def whiten(a, lo=20, hi=200):
    """把掃描的灰底拉白（彩色／灰階掃描用；黑白掃描不需要）"""
    b = a.astype(np.int16); return np.clip((b - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)

def view(a_or_pg, box, name, scale=0.5, step=100):
    """輸出帶座標格線的檢視圖到 views/<name>.png。box=(x1,y1,x2,y2)。
    讀座標只看格線上的數字標籤，不要用目測比例推算。"""
    a = load(a_or_pg) if isinstance(a_or_pg, int) else a_or_pg
    x1, y1, x2, y2 = box
    im = Image.fromarray(a[y1:y2, x1:x2]).convert('RGB')
    im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))))
    d = ImageDraw.Draw(im); f = ImageFont.truetype(LABEL_FONT, 13)
    for x in range((x1 // step + 1) * step, x2, step):
        X = (x - x1) * scale; d.line([(X, 0), (X, im.height)], fill=(255, 170, 170)); d.text((X + 2, 2), str(x), fill=(220, 0, 0), font=f)
    for y in range((y1 // step + 1) * step, y2, step):
        Y = (y - y1) * scale; d.line([(0, Y), (im.width, Y)], fill=(170, 170, 255)); d.text((2, Y + 2), str(y), fill=(0, 0, 220), font=f)
    p = os.path.join(VIEWS, name + '.png'); im.save(p); return p

def rect(a, x1, y1, x2, y2):
    """整塊塗白（筆跡落在空白處時用）"""
    a[y1:y2, x1:x2] = 255

def line_white(a, pts, t=7):
    for p, q in zip(pts[:-1], pts[1:]): cv2.line(a, tuple(p), tuple(q), 255, t)

def smart(a, pts, t=7, k=5):
    """沿折線擦掉筆跡，但保留和它交叉的印刷橫線、直線、字的筆畫。
    pts: [(x,y),...] 筆跡路徑；t: 擦除寬度；k: 判斷交叉的距離。"""
    dark = a < 128
    for p, q in zip(pts[:-1], pts[1:]):
        m = np.zeros_like(a); cv2.line(m, tuple(p), tuple(q), 255, t); m = m > 0
        sd = np.array([q[0] - p[0], q[1] - p[1]], float); sd /= np.linalg.norm(sd)
        keep = np.zeros_like(m)
        for dx, dy in [(1, 0), (0, 1), (1, 1), (1, -1)]:
            v = np.array([dx, dy], float) / np.hypot(dx, dy)
            if abs(v @ sd) > 0.8: continue
            for kk in (k, k + 2):
                s1 = np.roll(np.roll(dark, -dy * kk, 0), -dx * kk, 1); s2 = np.roll(np.roll(dark, dy * kk, 0), dx * kk, 1)
                keep |= s1 & s2
        a[m & ~keep] = 255

def hough(a, box, minlen=100):
    """在 box 裡找斜線（可能是筆跡劃線），回傳 [(x1,y1,x2,y2,角度)]。印刷的斜線也會被找到，要自己判斷。"""
    x1, y1, x2, y2 = box
    bw = (a[y1:y2, x1:x2] < 128).astype(np.uint8) * 255
    L = cv2.HoughLinesP(bw, 1, np.pi / 720, threshold=80, minLineLength=minlen, maxLineGap=15)
    out = []
    if L is not None:
        for l in L[:, 0]:
            p, q, r, s = l; ang = np.degrees(np.arctan2(s - q, r - p))
            if 15 < abs(ang) < 85: out.append((p + x1, q + y1, r + x1, s + y1, round(float(ang), 1)))
    return out

def restore(a, orig, dst, search):
    """被劃破的字：在 search 區找同一個字的乾淨版本，整塊貼回 dst。回傳相似度（>0.45 通常可用）。"""
    x1, y1, x2, y2 = dst; t = orig[y1:y2, x1:x2]
    sx1, sy1, sx2, sy2 = search; R = orig[sy1:sy2, sx1:sx2]
    res = cv2.matchTemplate(R, t, cv2.TM_CCOEFF_NORMED); _, mv, _, ml = cv2.minMaxLoc(res)
    cx, cy = sx1 + ml[0], sy1 + ml[1]
    a[y1:y2, x1:x2] = orig[cy:cy + (y2 - y1), cx:cx + (x2 - x1)]
    return round(float(mv), 3), (cx, cy)

def offset(orig, tpl_box, search_box):
    """兩個版面相同的小圖（如四個選項圖）之間的位移：回傳 (dx,dy, 相似度)，使 tpl + (dx,dy) 對到 search 裡的同一處。"""
    x1, y1, x2, y2 = tpl_box; t = orig[y1:y2, x1:x2]
    sx1, sy1, sx2, sy2 = search_box; R = orig[sy1:sy2, sx1:sx2]
    res = cv2.matchTemplate(R, t, cv2.TM_CCOEFF_NORMED); _, mv, _, ml = cv2.minMaxLoc(res)
    return sx1 + ml[0] - x1, sy1 + ml[1] - y1, round(float(mv), 3)

def text(a, xy, s, size=28, font=SERIF_LATIN, idx=0):
    """在圖上重打字（被塗掉的選項標籤等）。中文用 SERIF_CJK idx=3 或 SANS idx=3。"""
    im = Image.fromarray(a); d = ImageDraw.Draw(im)
    d.text(xy, s, fill=0, font=ImageFont.truetype(font, size, index=idx)); a[:] = np.array(im)

def save(a, box, name, pad=10):
    """裁切 box 後自動去白邊，存 figs/<name>.png，回傳 (高,寬)。"""
    x1, y1, x2, y2 = box; c = a[y1:y2, x1:x2]
    m = c < 160; cols = np.where(m.any(0))[0]; rows = np.where(m.any(1))[0]
    c = c[max(rows.min() - pad, 0):rows.max() + pad + 1, max(cols.min() - pad, 0):cols.max() + pad + 1]
    os.makedirs(FIGS, exist_ok=True); cv2.imwrite(os.path.join(FIGS, name + '.png'), c); return c.shape

def edge_lines(a, box, frac=0.8):
    """檢查裁切結果左右邊緣是否帶進欄位分隔線：回傳高度佔比 > frac 的直線 x 座標。"""
    x1, y1, x2, y2 = box; d = a[y1:y2, x1:x2] < 150
    col = d.sum(0); return [x1 + i for i in range(len(col)) if col[i] > frac * (y2 - y1)]

def sheet(names, out='sheet', W=1600, maxw=1400):
    """把多張裁好的圖排成一張聯絡表（views/<out>.png），用來一次目視檢查。"""
    ims = [(n, Image.open(os.path.join(FIGS, n + '.png'))) for n in names]
    x = y = rowh = 0; pos = []
    for n, i in ims:
        if x + i.width > W: x = 0; y += rowh + 30; rowh = 0
        pos.append((n, i, x, y)); x += i.width + 20; rowh = max(rowh, i.height)
    S = Image.new('RGB', (W, y + rowh + 30), 'white'); d = ImageDraw.Draw(S)
    for n, i, x, y in pos: S.paste(i, (x, y + 16)); d.text((x, y), n, fill=(255, 0, 0))
    S.thumbnail((maxw, 3000)); p = os.path.join(VIEWS, out + '.png'); S.save(p); return p
