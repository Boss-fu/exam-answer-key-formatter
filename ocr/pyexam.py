#!/usr/bin/env python3
"""純 Python 考卷前處理管線（無 Claude、零 token）。

用我們討論的這一系列 Python 工具串起來：
  PyMuPDF  讀 PDF／判斷有無文字層／把掃描頁 render 成高解析圖、裁圖
  OpenCV + numpy  影像前處理（放大、去雜訊、Otsu 二值化、估傾斜）
  PP-OCRv5 (rapidocr_onnxruntime + OpenCC s2twp)  掃描頁 OCR（預設，完全本地）
  Tesseract (pytesseract, chi_tra+eng)  備援：ocr/models/ 缺模型時自動退回
  python-docx  把抽出的文字重排成乾淨 Word

策略（關鍵）：
  * 數位 PDF（有文字層）→ 直接用 PyMuPDF 取字，近乎 100% 且免費免 OCR。
  * 掃描 PDF（無文字層）→ render 300dpi → 雙欄切割 → PP-OCRv5（無模型則 Tesseract）。
  圖片一律不重畫：偵測到圖就裁成 *_p{n}_fig{k}.png，並在文字裡留〔含圖 k〕佔位。

PP-OCRv5 路徑的後處理（見 ocr_page_ppocr_records）：
  * 圖區（連通域偵測）內／旁的刻度、軸名、(A)(B) 字母不併入正文；半壓到圖的文字框遮掉圖後重辨
  * 題號括號正規化成 `( )N.`（)4. (.)9. 021. （)13. (C)6. → ( )N.）；吞掉括號的 `N.` 依序補回
  * Unicode 上下標 → v_{1}、m/s^{2}；同行黏住的選項 (B)(C)(D)(E) 前補空白
  * 行信心 < LOW_CONF(0.8) 的行尾加「　⚠需確認」，公式碎片先靠它標出來（真正修在階段 2）

用法：
    python3 ocr/pyexam.py 某考卷.pdf -o out/            # 產 .txt + .docx + 裁圖
    python3 ocr/pyexam.py 某考卷.pdf --dpi 400 --page 1  # 只試第1頁、提高解析度
"""
import argparse
import json
import os
import re
import sys
import time

import numpy as np
import pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dehand import apply as dehand  # noqa: E402
try:
    import ppocr_engine
except Exception:
    ppocr_engine = None

try:
    import cv2
except Exception:
    cv2 = None
try:
    import pytesseract
except Exception:
    pytesseract = None


def _progress(out_dir, started, stage, page=0, pages=0, msg="", img=None):
    """Best-effort 即時狀態：絕不能因為畫面回報失敗而中斷 OCR。"""
    if os.environ.get("OCR_PROGRESS") != "1":
        return
    try:
        event = {"t": round(time.time() - started, 1), "stage": stage,
                 "page": page, "pages": pages, "msg": msg, "img": img}
        with open(os.path.join(out_dir, "progress.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _imwrite(path, image, params=()):
    """cv2.imwrite 在 Windows 碰到中文路徑會失敗且不報錯，圖就全部不見；改成先編碼再寫檔。"""
    ok, buf = cv2.imencode(os.path.splitext(path)[1], image, list(params))
    if ok:
        buf.tofile(path)
    return ok


def _save_preview(image, out_dir, name):
    """存給處理畫面的輕量卷面圖；失敗時回傳 None。"""
    try:
        if cv2 is None or image is None or image.size == 0:
            return None
        previews = os.path.join(out_dir, "previews")
        os.makedirs(previews, exist_ok=True)
        h, w = image.shape[:2]
        if w > 900:
            image = cv2.resize(image, (900, max(1, round(h * 900 / w))), interpolation=cv2.INTER_AREA)
        filename = f"{name}.jpg"
        if not _imwrite(os.path.join(previews, filename), image, [cv2.IMWRITE_JPEG_QUALITY, 70]):
            return None
        return f"previews/{filename}"
    except Exception:
        return None


# ---------- 影像前處理（OpenCV）----------
def preprocess(img_bgr, upscale=1.5):
    """灰階 → 放大 → 去雜訊 → Otsu 二值化。回傳適合 OCR 的單通道圖。"""
    if cv2 is None:
        return img_bgr
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    if upscale and upscale != 1.0:
        gray = cv2.resize(gray, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.fastNlMeansDenoising(gray, h=10)
    _, binimg = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binimg


def page_to_bgr(page, dpi):
    pix = page.get_pixmap(dpi=dpi)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR) if cv2 is not None else arr[:, :, :3]
    elif pix.n == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR) if cv2 is not None else arr
    return arr


def remove_color_ink(bgr, restore_text=False):
    """老師批改、學生作答的紅筆／藍筆塗白（印刷是黑色，彩色的幾乎都是筆跡）。
    不清的話，題號旁的紅色勾勾會讓「7.」被認成「入」、紅筆寫的答案「A」跑進內文。
    門檻依實際掃描卷量測：紅筆色相 0~10／160~179、飽和度中位數約 85；淡掉的筆畫邊緣再往外擴 2px 一起清。
    灰階掃描（沒有彩色）時什麼都不會動。回傳 (清過的影像, 被清掉的像素比例)。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    ink = (s >= 45) & (v >= 60) & ((h <= 10) | (h >= 160) | ((h >= 95) & (h <= 135)))
    if not ink.any():
        return bgr, 0.0
    if (s >= 45).mean() > .05:
        # 整片彩色校徽浮水印：疊在上面的黑字掃出來也帶色（暗紅、暗藍），不能整塊塗白。
        # 實測：疊在浮水印上的字 V 80~140（一般黑字約 7）、彩色浮水印 >170。
        # 帶色像素（飽和度 ≥20，含淡粉紅區）取亮度 V 拉伸 110→0、170→255：浮水印變白、被染色的字補回跟其他字一樣黑。
        # 不帶色的像素（鉛筆、圖的灰階）不拉伸，免得鉛筆字和圖內刻度被加黑後讀進題幹
        col = s >= 45
        out = bgr.copy()
        if restore_text:
            # 給人看的乾淨卷（clean_pdf、網頁）：被染色的字補黑。OCR 不用這條——
            # 實測圖內刻度、標籤跟著變深後會被切進題幹（112 南市復興 92.6%→90.4%）
            tint = s >= 20
            out[tint] = np.clip((v[tint].astype(np.float32) - 110) * (255 / 60), 0, 255).astype(np.uint8)[:, None]
        else:
            out[col] = np.where(v[col] >= 150, 255, v[col])[:, None]
        # 校徽的灰色部分（實測約 175）跟字同色只是較淡：中灰（150~235）塗白，但黑字（<100）周圍 2px 不動
        g = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
        near_text = cv2.dilate((g < 100).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        out[(g >= 150) & (g <= 235) & ~near_text] = 255
        return out, float(col.mean())
    mask = cv2.dilate(ink.astype(np.uint8), np.ones((5, 5), np.uint8))
    out = bgr.copy()
    out[mask > 0] = 255
    return out, float(ink.mean())


_ROT = {90: "ROTATE_90_CLOCKWISE", 180: "ROTATE_180", 270: "ROTATE_90_COUNTERCLOCKWISE"}


def fix_orientation(bgr, return_boxes=False):
    """掃描時整頁放反（上下顛倒）或轉 90 度：縮小後各方向試認一次，取「認得出的字」最多的方向。
    1. 先看文字偵測框是「橫的」還是「直的」：直框居多＝整頁轉了 90°，候選改成 90°／270°。
    2. 兩個候選方向各認一次，分數＝信心 ≥ 0.8 的字數；倒過來的字 PP-OCR 會吐低信心亂碼，差距很明顯。
    回傳 (轉正後的影像, 角度)。"""
    s = 1600.0 / max(bgr.shape[:2])
    small = cv2.resize(bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else bgr

    def score(img):
        return sum(len(it["text"]) for it in ppocr_engine.ocr_lines(img) if it["conf"] >= 0.8)

    def rot(img, deg):
        return cv2.rotate(img, getattr(cv2, _ROT[deg])) if deg else img

    detected = ppocr_engine.det_boxes(small)
    wide = tall = 0
    for b in detected:
        w = max(p[0] for p in b) - min(p[0] for p in b)
        h = max(p[1] for p in b) - min(p[1] for p in b)
        wide += w > 2 * h
        tall += h > 2 * w
    def done(image, deg, boxes):
        if not return_boxes:
            return image, deg
        # 未旋轉頁面直接重用本函式本來就跑過的偵測框；座標從 small 換回 300dpi。
        if not deg:
            boxes = [[[float(x) / s, float(y) / s] for x, y in box] for box in boxes]
        else:
            # 旋正是少數情況，原座標不能直接重用；重偵測一次保證座標正確。
            boxes = ppocr_engine.det_boxes(image)
        return image, deg, boxes

    if wide + tall < 5:                                  # 幾乎空白的頁，不動
        return done(bgr, 0, detected)
    if tall > wide:
        scores = {90: score(rot(small, 90)), 270: score(rot(small, 270))}
        scores[0] = 0
    else:
        scores = {0: score(small), 180: score(rot(small, 180))}
    best = max(scores, key=scores.get)
    if best and scores[best] > 1.5 * max(1, scores[0]):
        return done(rot(bgr, best), best, detected)
    return done(bgr, 0, detected)


# ---------- 圖片偵測（OpenCV 輪廓）----------
def detect_figures(binimg, page_h, min_area_ratio=0.02, max_area_ratio=0.45):
    """找出像「圖／表」的矩形區塊（大片連通、非整行文字）。回傳 bbox 清單（原圖座標比例）。"""
    if cv2 is None:
        return []
    inv = 255 - binimg
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
    closed = cv2.morphologyEx(inv, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    H, W = binimg.shape[:2]
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = (w * h) / float(W * H)
        aspect = w / float(h + 1)
        # 太小=雜點；太扁太寬=整段文字；取中等塊且近方形者視為圖
        if min_area_ratio <= area <= max_area_ratio and 0.25 <= aspect <= 4.0 and h > 0.05 * H:
            boxes.append((x / W, y / H, (x + w) / W, (y + h) / H))
    boxes.sort(key=lambda b: b[1])
    return boxes


# ---------- 欄位偵測（numpy 直向投影找白色間隔）----------
def find_column_split(binimg):
    """考卷常見雙欄。用每一欄(column)的墨點量找中間的白色間隔帶；
    找到就回傳切割 x 座標，否則回傳 None（單欄）。"""
    H, W = binimg.shape[:2]
    ink = (binimg < 128).sum(axis=0).astype(float)  # 每個 x 的黑點數
    # 只在中央 40%~60% 找間隔，避免切到頁邊
    lo, hi = int(W * 0.38), int(W * 0.62)
    band = ink[lo:hi]
    if band.size == 0:
        return None
    # 間隔帶：墨點量遠低於整頁中位數
    thresh = max(1.0, np.median(ink) * 0.15)
    gutter = np.where(band < thresh)[0]
    if gutter.size < (hi - lo) * 0.15:  # 中央幾乎沒有夠長的留白 → 視為單欄
        return None
    return lo + int(np.median(gutter))


def text_column_splits(bgr):
    """用 PP-OCR 文字「偵測框」的位置找欄界（不看墨水，所以不怕中間被打勾、寫字弄髒，也抓得到很窄的欄距）。
    做法：偵測框在 x 方向疊起來成「覆蓋數」曲線，在候選區間找最低點；最低點要遠低於左右兩欄
    （< 兩側中位數的 35%）才算欄界——用相對值，所以學生跨過欄距的幾筆手寫不會讓判斷失敗。
    直式頁只在 35%~65% 找一刀（雙欄）；橫式頁（A3 一張印兩頁）中央必切，左右兩半再各找一刀（最多四欄）。
    回傳欄界 x 清單（原圖座標）；字框太少就回傳 []，交給墨水投影法。"""
    return text_layout(bgr)["cuts"]


def text_layout(bgr):
    """text_column_splits 的完整版：回傳 {"cuts": 欄界, "hard": 必切的欄界（橫式頁中線）, "header": 頁首底線 y}。
    頁首＝頁面上方 30% 內「跨過欄界」的字框（校名、考試名稱、作答說明）：這一帶要整行讀，不能被欄界切成兩半。"""
    import cv2
    H, W = bgr.shape[:2]
    none = {"cuts": [], "hard": [], "header": 0}
    s = min(1.0, 2480.0 / max(H, W))                    # 約 150~200dpi 偵測就夠判斷版面，一頁不到 1 秒
    small = cv2.resize(bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else bgr
    boxes = []
    for b in ppocr_engine.det_boxes(small):
        xs = [p[0] / s for p in b]
        ys = [p[1] / s for p in b]
        boxes.append((max(0, int(min(xs))), min(W, int(max(xs))), int(min(ys)), int(max(ys))))
    hs = sorted(y1 - y0 for x0, x1, y0, y1 in boxes if x1 - x0 > 2 * (y1 - y0))
    hmed = hs[len(hs) // 2] if hs else 0
    # 只用「印刷字高度」的橫向字框：跨全頁的大標題、比印刷字高很多或矮很多的（多半是手寫）都不算
    spans = [(x0, x1) for x0, x1, y0, y1 in boxes
             if x1 - x0 < 0.6 * W and y1 - y0 < 0.05 * H
             and (not hmed or 0.6 * hmed <= y1 - y0 <= 1.6 * hmed)]
    if len(spans) < 20:
        return none
    cov = np.zeros(W, float)
    for x0, x1 in spans:
        cov[x0:x1] += 1
    k = max(3, int(0.004 * W))
    cov = np.convolve(cov, np.ones(k) / k, "same")      # 抹平：單一像素的縫不算欄距

    def cut_in(a, b, force=False):
        a, b = int(a * W), int(b * W)
        c = a + int(np.argmin(cov[a:b]))
        w = int(0.18 * W)
        left = np.median(cov[max(0, c - w):max(1, c - k * 3)])
        right = np.median(cov[min(W - 1, c + k * 3):min(W, c + w)])
        side = min(left, right)
        if force or (side >= 4 and cov[c] < 0.35 * side):
            return c
        # 一邊幾乎空白（例如右欄只放了一張表）但中間有完全乾淨的直條：仍是雙欄
        if cov[c] <= 1 and max(left, right) >= 8 and side >= 1:
            return c
        return None

    hard = []
    if W > 1.2 * H:
        mid = cut_in(0.42, 0.58, force=True)
        hard = [mid]
        cuts = [cut_in(0.15, 0.35), mid, cut_in(0.65, 0.85)]
    else:
        cuts = [cut_in(0.35, 0.65)]
    cuts = sorted(c for c in cuts if c is not None)
    soft = [c for c in cuts if c not in hard]
    pad = int(0.01 * W)
    crossing = [(y0, y1) for x0, x1, y0, y1 in boxes
                if y1 < 0.3 * H and any(x0 < c - pad and x1 > c + pad for c in soft)]
    header = max(y1 for _, y1 in crossing) + int(0.004 * H) if crossing else 0
    if crossing and soft:
        # 頁首不能深過「右欄第一行正文」：右欄開始有字就代表雙欄已經開始（跨欄的手寫或圖會把頁首拉太低）
        top = min(y0 for y0, _ in crossing)
        right = [y0 for x0, x1, y0, y1 in boxes
                 if x0 > soft[0] + pad and y0 > top and x1 - x0 > 2 * (y1 - y0) and (not hmed or y1 - y0 <= 1.6 * hmed)]
        if right:
            header = min(header, min(right) - int(0.002 * H))
    return {"cuts": cuts, "hard": hard, "header": max(0, header)}


def find_column_splits(binimg):
    """回傳所有欄界 x（由左到右，可能是空清單）。
    橫式頁（A3/B4 一張印兩頁）先在正中央對切成左右兩半，每半再各自找雙欄 → 最多四欄。
    中央切線取 45%~55% 之間墨點最少的位置；找不到乾淨留白就直接切正中間（兩半本來就是兩頁）。"""
    H, W = binimg.shape[:2]
    if W <= 1.2 * H:
        s = find_column_split(binimg)
        return [s] if s else []
    ink = (binimg < 128).sum(axis=0).astype(float)
    lo, hi = int(W * 0.45), int(W * 0.55)
    mid = lo + int(np.argmin(np.convolve(ink[lo:hi], np.ones(15), "same")))
    splits = []
    for x0, x1 in ((0, mid), (mid, W)):
        s = find_column_split(binimg[:, x0:x1])
        if s:
            splits.append(x0 + s)
        if x1 < W:
            splits.append(mid)
    return splits


# ---------- OCR 一頁（自動雙欄切割）----------
LOW_CONF = 0.8                  # 行信心低於此值 → 行尾標「需確認」
LOW_CONF_MARK = "　⚠需確認"
RE_CJK = re.compile(r"[一-鿿]")


def ppocr_available():
    return ppocr_engine is not None and ppocr_engine.models_available()


def resolve_engine(engine="auto"):
    """auto：模型在就用 PP-OCRv5，否則 Tesseract。回傳 'ppocr' 或 'tesseract'。"""
    if engine == "tesseract":
        return "tesseract"
    if engine == "ppocr":
        if not ppocr_available():
            raise RuntimeError("找不到 PP-OCRv5 模型或 rapidocr_onnxruntime（見 ocr/models/、OCR_MODELS_DIR）")
        return "ppocr"
    return "ppocr" if ppocr_available() else "tesseract"


# ---------- 圖區偵測（連通域）＋圖內文字過濾 ----------
def _line_positions(profile, thresh, gap=8):
    """投影值超過門檻的座標，相鄰的併成一條線，回傳每條線的中心"""
    pos = np.where(profile > thresh)[0]
    out, start, prev = [], None, None
    for p in pos:
        if start is None:
            start = prev = p
        elif p - prev <= gap:
            prev = p
        else:
            out.append((start + prev) // 2)
            start = prev = p
    if start is not None:
        out.append((start + prev) // 2)
    return out


def detect_tables(bgr, dpi=300):
    """有框線的表格：形態學取出長橫線／長直線 → 外框 → 列線、欄線 → 儲存格（同列缺直線＝左右合併）。
    回傳 [{"box":(x0,y0,x1,y1), "ys":[列線], "xs":[欄線], "spans":[[每列各格跨幾欄]]}]。
    只認「四邊都有框、至少 2 列 2 欄」的；座標圖的方格紙要靠後面「多數格子有字」再濾掉。"""
    if cv2 is None:
        return []
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    H, W = ink.shape
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, int(0.25 * dpi)), 1)))
    vert = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(50, int(0.2 * dpi)))))
    grid = cv2.dilate(horiz | vert, np.ones((3, 3), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(grid, connectivity=8)
    tables = []
    for i in range(1, n):
        x, y, w, h, _ = st[i]
        if w < 0.6 * dpi or h < 0.25 * dpi or w * h > 0.5 * W * H:
            continue
        hb, vb = horiz[y:y + h, x:x + w] > 0, vert[y:y + h, x:x + w] > 0
        # 掃描微歪時一條橫線會跨好幾個像素列：投影前先上下（直線則左右）加粗 3px，才算得到完整長度
        hs = cv2.dilate(hb.astype(np.uint8), np.ones((7, 1), np.uint8)) > 0
        vs = cv2.dilate(vb.astype(np.uint8), np.ones((1, 7), np.uint8)) > 0
        ys = _line_positions(hs.sum(axis=1), 0.6 * w)
        xs = _line_positions(vs.sum(axis=0), 0.25 * h)
        if len(ys) < 3 or len(xs) < 2 or ys[0] > 0.1 * h or ys[-1] < 0.9 * h or xs[0] > 0.1 * w or xs[-1] < 0.9 * w:
            continue
        spans = []
        for r0, r1 in zip(ys, ys[1:]):
            a, b = r0 + 8, r1 - 8
            row, run = [], 1
            for c in xs[1:-1]:
                seg = vs[a:b, max(0, c - 4):c + 5].any(axis=1) if b > a else np.array([True])
                if seg.mean() > 0.6:
                    row.append(run)
                    run = 1
                else:
                    run += 1
            row.append(run)
            spans.append(row)
        tables.append({"box": (int(x), int(y), int(x + w), int(y + h)),
                       "ys": [int(y + v) for v in ys], "xs": [int(x + v) for v in xs], "spans": spans})
    return tables


def _ocr_cell(bgr, x0, y0, x1, y1):
    """空白儲存格補認：只有一個字（甲、乙…）的格子偵測器常常框不到，裁出格子內部直接辨識。
    格子裡沒有墨跡就回傳空字串。單獨一個 z／Z 幾乎都是「乙」被認錯。"""
    m = 10
    crop = bgr[y0 + m:y1 - m, x0 + m:x1 - m]
    if crop.size == 0:
        return ""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    ink = (gray < 128).astype(np.uint8)
    n, _, st, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    keep = [st[i] for i in range(1, n) if st[i][4] >= 30]          # 濾掉掃描雜點，否則裁切範圍被撐大
    if not keep:
        return ""
    x0_ = min(k[0] for k in keep); y0_ = min(k[1] for k in keep)
    x1_ = max(k[0] + k[2] for k in keep); y1_ = max(k[1] + k[3] for k in keep)
    crop = crop[max(0, y0_ - 6):y1_ + 7, max(0, x0_ - 6):x1_ + 7]
    crop = cv2.copyMakeBorder(crop, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    text, conf = ppocr_engine.rec_only(crop)          # 字已經裁好，跳過偵測直接辨識
    if conf < 0.5:
        return ""
    return {"z": "乙", "Z": "乙", "2": "乙"}.get(text, text) if len(text) == 1 else text


def fill_table(t, items, bgr=None):
    """把落在表格裡的 OCR 框分到儲存格，回傳 rows=[[[文字, 跨欄數], …], …]（buildlib.tbl 吃的格式）"""
    ys, xs = t["ys"], t["xs"]
    rows = []
    for r, (r0, r1) in enumerate(zip(ys, ys[1:])):
        row, ci = [], 0
        for span in t["spans"][r]:
            c0, c1 = xs[ci], xs[ci + span]
            cell = [it for it in items if c0 <= _center(it)[0] < c1 and r0 <= _center(it)[1] < r1]
            text = "".join(l["text"] for l in group_into_lines(cell))
            if text.strip() in ("z", "Z"):
                text = "乙"
            if not text and bgr is not None:
                text = _ocr_cell(bgr, c0, r0, c1, r1)
            row.append([postprocess_line(text) if text else "", span])
            ci += span
        rows.append(row)
    return rows


def figure_candidates(bgr, dpi=300, merge_gap=60):
    """用墨跡連通域找「圖」候選（座標圖、示意圖、尺／紙帶照片…）。
    文字筆畫的連通域都很小；圖的線條彼此相連，bbox 大。回傳 [(x0,y0,x1,y1)] 像素座標。
    表格框線／整行底線會因為太細（短邊 < 0.33 吋）被排除；表格本身靠後面的「框內中文字數」濾掉。"""
    if cv2 is None:
        return []
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    H, W = ink.shape[:2]
    n, _, st, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    min_short, min_long = 0.33 * dpi, 0.43 * dpi
    boxes = []
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if min(w, h) < min_short or max(w, h) < min_long:
            continue
        if w * h > 0.4 * W * H or w / float(h) > 6 or h / float(w) > 6:
            continue
        boxes.append([x, y, x + w, y + h])
    # 距離近的合併（例如 (A)~(E) 五張小圖併成一塊）
    merged = True
    while merged:
        merged = False
        out = []
        for b in boxes:
            for o in out:
                if (b[0] - merge_gap < o[2] and o[0] - merge_gap < b[2] and
                        b[1] - merge_gap < o[3] and o[1] - merge_gap < b[3]):
                    o[0], o[1], o[2], o[3] = min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])
                    merged = True
                    break
            else:
                out.append(list(b))
        boxes = out
    boxes.sort(key=lambda b: (b[1], b[0]))
    return [tuple(int(v) for v in b) for b in boxes]


def _center(it):
    xs = [p[0] for p in it["box"]]
    ys = [p[1] for p in it["box"]]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(xs), min(ys), max(xs), max(ys)


def _inter_ratio(it, fig):
    _, _, x0, y0, x1, y1 = _center(it)
    iw = min(x1, fig[2]) - max(x0, fig[0])
    ih = min(y1, fig[3]) - max(y0, fig[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    return iw * ih / float(max(1.0, (x1 - x0) * (y1 - y0)))


_RE_FIG_OPTION = re.compile(r"^\s*[\(（]\s*[A-Ea-e]\s*[\)）]")
_RE_FIG_QNO = re.compile(r"^\s*\d{1,2}\s*[.．、]")


def _short_figure_label(text):
    """分類圖／流程圖的短標籤；題幹、選項、表格內容不可由此吃掉。"""
    text = text.strip()
    n_cjk = len(RE_CJK.findall(text))
    return (0 < n_cjk <= 8 and len(text) <= 20 and
            not _RE_FIG_OPTION.match(text) and not _RE_FIG_QNO.match(text) and
            not re.search(r"[，。？?：:；;！!]", text))


def split_figure_text(items, cands, pad=70, max_cjk_in_figure=6):
    """依 OCR 框決定哪些候選才是真的圖（框內「成句」的中文字 ≤ max_cjk_in_figure；表格／標題框內是句子，不是圖），
    並把圖內文字框分出來。回傳 (figs, body_items, partial_items)：
      body_items    不受圖影響的文字框
      partial_items [(item, fig)] 只有一部分壓到圖，要遮掉圖再重辨
    圖內文字 = 中心在圖框內，或在圖框外擴 pad px 內且為短的非中文標註（刻度、軸名、(A)(B) 選項字母）。"""
    figs = []
    for c in cands:
        inside = [it for it in items if c[0] <= _center(it)[0] <= c[2] and c[1] <= _center(it)[1] <= c[3]]
        # 地圖、示意圖上常有一堆短標籤（也可能是「無機化合物」這種 5 字詞）；
        # 表格、標題框裡才會有長句或標點。因此不能把所有 4 字詞累加成正文。
        sentence_cjk = sum(n for it in inside for n in [len(RE_CJK.findall(it["text"]))]
                           if n > 8 or re.search(r"[，。？?：；;！!]", it["text"]))
        # 學生用筆圈起來的一段題文：框裡大部分面積是高信心的印刷字 → 是圈圈不是圖（裁掉會讓題文消失）
        area = max(1, (c[2] - c[0]) * (c[3] - c[1]))
        text_area = sum((_center(it)[4] - _center(it)[2]) * (_center(it)[5] - _center(it)[3])
                        for it in inside if it["conf"] >= 0.8)
        if text_area / area > 0.3 and any(len(RE_CJK.findall(it["text"])) >= 3 for it in inside):
            continue
        if sentence_cjk <= max_cjk_in_figure:
            figs.append(c)
    body, partial = [], []
    labels = {i: [] for i in range(len(figs))}
    # 候選是由相連的線條／墨跡得來；短標籤若集中在候選周圍，通常是流程、
    # 分類或示意圖的一部分。先算好群集，避免只因貼著一條底線就吃掉一個詞。
    nearby = {}
    for i, f in enumerate(figs):
        nearby[i] = [it for it in items
                     if _short_figure_label(it["text"])
                     and f[0] - pad <= _center(it)[0] <= f[2] + pad
                     and f[1] - pad <= _center(it)[1] <= f[3] + pad]
    for it in items:
        cx, cy, *_ = _center(it)
        text = it["text"]
        n_cjk = len(RE_CJK.findall(text))
        # 題目的選項可剛好壓在示意圖框裡，仍必須留在正文；否則會直接少一個選項。
        if _RE_FIG_OPTION.match(text):
            body.append(it)
            continue
        short_label = _short_figure_label(text)
        # 軸名、刻度串（「降雨量（毫米）」「120150E180150W」「586266707478」）：不像句子、而且水平位置落在圖的範圍內
        axis_like = n_cjk <= 6 and not re.search(r"[，。？?：:、；]", text) and (
            n_cjk == 0 or len(text) <= 10)
        drop, part = False, None
        # 長句或有標點才是正文。過去以「4 個中文字」判斷，會讓「無機化合物」、
        # 「天然聚合物」這類分類圖標籤逐行漏回正文。
        sentence = it["conf"] >= 0.3 and (n_cjk > 8 or re.search(r"[，。？?：；;！!]", text))
        for i, f in enumerate(figs):
            r = _inter_ratio(it, f)
            if sentence and (r >= 0.12 or (f[0] <= cx <= f[2] and f[1] <= cy <= f[3])):
                continue                              # 整行保留，也不走「塗白圖區再重認」（圖框蓋掉大半行時會重認成空白）
            if r >= 0.6 or (f[0] <= cx <= f[2] and f[1] <= cy <= f[3]):
                drop = True
                labels[i].append(it)
            elif short_label and (f[0] - pad <= cx <= f[2] + pad and f[1] - pad <= cy <= f[3] + pad) and (
                    # 圖內的短標籤直接收；圖外標籤至少須與另一短標籤成群，且
                    # 水平落在圖的延伸範圍，避免正文旁邊的一個短詞被誤收。
                    (f[0] <= cx <= f[2] and f[1] <= cy <= f[3]) or
                    (len(nearby[i]) >= 2 and f[0] - pad // 2 <= cx <= f[2] + pad // 2)):
                drop = True
                labels[i].append(it)
            elif axis_like and (f[0] - pad // 2 <= _center(it)[2] and _center(it)[4] <= f[2] + pad // 2
                                and f[1] - pad <= cy <= f[3] + pad):
                drop = True
                labels[i].append(it)
            elif r >= 0.12:
                part = f
        if drop:
            continue
        if part is not None:
            partial.append((it, part))
        else:
            body.append(it)
    return figs, body, partial, labels


def _reocr_masked(bgr, it, fig):
    """文字框只有一部分壓到圖：把圖區塗白後，只對這個框附近重新辨識。"""
    _, _, x0, y0, x1, y1 = _center(it)
    H, W = bgr.shape[:2]
    px = 8
    cx0, cy0 = max(0, int(x0) - px), max(0, int(y0) - px)
    cx1, cy1 = min(W, int(x1) + px), min(H, int(y1) + px)
    crop = bgr[cy0:cy1, cx0:cx1].copy()
    fx0, fy0 = max(0, fig[0] - cx0), max(0, fig[1] - cy0)
    fx1, fy1 = max(0, fig[2] - cx0), max(0, fig[3] - cy0)
    crop[fy0:fy1, fx0:fx1] = 255
    out = []
    for r in ppocr_engine.ocr_lines(crop):
        r["box"] = [[x + cx0, y + cy0] for x, y in r["box"]]
        out.append(r)
    return out


# ---------- 行重組與後處理 ----------
def group_into_lines(items, min_conf=0.0):
    """把 PP-OCR 的文字框依「垂直重疊」併成視覺行（表格同列的格子併成一行），
    行內由左到右、行間由上到下。回傳 [{text, conf, y0, y1, x0, x1}]；conf 以字數加權平均。"""
    boxes = []
    for it in items:
        if it["conf"] < min_conf:
            continue
        xs = [p[0] for p in it["box"]]
        ys = [p[1] for p in it["box"]]
        boxes.append({"x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys),
                      "text": it["text"], "conf": it["conf"]})
    boxes.sort(key=lambda b: (b["y0"] + b["y1"]) / 2)
    rows = []
    for b in boxes:
        cy = (b["y0"] + b["y1"]) / 2
        h = b["y1"] - b["y0"]
        if rows:
            r = rows[-1]
            rcy = sum((m["y0"] + m["y1"]) / 2 for m in r) / len(r)
            rh = sum(m["y1"] - m["y0"] for m in r) / len(r)
            if abs(cy - rcy) < 0.5 * min(h, rh):
                r.append(b)
                continue
        rows.append([b])
    lines = []
    for r in rows:
        r.sort(key=lambda m: m["x0"])
        hmed = sorted(m["y1"] - m["y0"] for m in r)[len(r) // 2]
        text = r[0]["text"]
        for prev, cur in zip(r, r[1:]):
            gap = cur["x0"] - prev["x1"]
            text += (" " if gap > 0.6 * hmed else "") + cur["text"]
        wsum = sum(max(1, len(m["text"])) for m in r)
        lines.append({"text": text,
                      "conf": sum(m["conf"] * max(1, len(m["text"])) for m in r) / wsum,
                      "y0": min(m["y0"] for m in r), "y1": max(m["y1"] for m in r),
                      "x0": r[0]["x0"], "x1": max(m["x1"] for m in r)})
    return lines


# 題號：行首 ( )N. 被讀成 )4. / (.)9. / 021. / （)13.，甚至括號裡夾了手寫選項字母 (C)6.
# → 一律還原成乾淨的「( )N.」。只有「真的帶括號殘影」的行才動，沒括號的 18. 維持原樣。
_RE_QNO_BAD = re.compile(
    r"^\s*(?:[\(（\[]\s*[A-Ea-e.·,，\s]{0,3}\s*[\)）\]]?|[\)）]|[0Oo](?=\d))"
    r"\s*(\d{1,2})\s*[\.．、](?=\s*[^\d\s])")
_RE_QNO_CANON = re.compile(r"^\( \)\d{1,2}\.")
_RE_QNO_BARE = re.compile(r"^\s*(\d{1,2})\s*[\.．、](?=\s*[^\d\s])")
_RE_OPT_GLUE = re.compile(r"(?<=\S)(?=[\(（][B-E][\)）])")

_SUP = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7",
        "⁸": "8", "⁹": "9", "⁺": "+", "⁻": "-", "ⁿ": "n", "ⁱ": "i"}
_SUB = {"₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5", "₆": "6", "₇": "7",
        "₈": "8", "₉": "9", "₊": "+", "₋": "-", "ₐ": "a", "ₑ": "e", "ₒ": "o", "ₓ": "x",
        "ₙ": "n", "ₘ": "m", "ᵢ": "i", "ⱼ": "j"}
_RE_SUP = re.compile("[" + "".join(_SUP) + "]+")
_RE_SUB = re.compile("[" + "".join(_SUB) + "]+")


def normalize_qno(text):
    if _RE_QNO_CANON.match(text):
        return text
    m = _RE_QNO_BAD.match(text)
    if not m:
        return text
    return "( )" + m.group(1) + "." + text[m.end():]


def qno_of_line(text):
    """行首題號（已正規化的 ( )N. 或裸 N.）→ int，否則 None。"""
    m = _RE_QNO_CANON.match(text) or _RE_QNO_BARE.match(text)
    return int(re.search(r"\d+", m.group(0)).group(0)) if m else None


def restore_bare_qnos(texts, prev_qno=None):
    """OCR 把整個「( )」吞掉、只剩 `21.有關…` 時：若題號剛好是上一題 +1，補回 ( )。
    texts 為依閱讀序的行文字（可含佔位／分隔行）；回傳 (新文字, 最後一個題號)。"""
    out, last = [], prev_qno
    for t in texts:
        q = qno_of_line(t)
        if q is not None:
            if last is not None and q == last + 1 and not _RE_QNO_CANON.match(t):
                m = _RE_QNO_BARE.match(t)
                t = "( )" + str(q) + "." + t[m.end():]
            last = q
        out.append(t)
    return out, last


def unicode_scripts_to_tex(text):
    """v₁ → v_{1}、m/s² → m/s^{2}（連續的上/下標字併成一組）。"""
    text = _RE_SUB.sub(lambda m: "_{" + "".join(_SUB[c] for c in m.group(0)) + "}", text)
    return _RE_SUP.sub(lambda m: "^{" + "".join(_SUP[c] for c in m.group(0)) + "}", text)


def split_options(text):
    """同一行黏在一起的 (A)…(B)…(C)… → 在 (B)(C)(D)(E) 前補一個空白。"""
    return _RE_OPT_GLUE.sub(" ", text)


def postprocess_line(text, conf=1.0, low_conf=LOW_CONF):
    text = normalize_qno(text)
    text = unicode_scripts_to_tex(text)
    text = split_options(text)
    if conf < low_conf:
        text += LOW_CONF_MARK
    return text


def table_has_question_or_paragraph(t, items):
    """表格外框不能把整段題文吞掉。

    有些考卷會把一組題目或題組圖用大框圈起來；形態學能看見外框與
    圖中的格線，卻無法分辨它和真正的表格。題目框的題號必須是連續的
    且靠整個外框的左緣；表格內欄位的 1.、2. 說明文字不可誤觸發。
    沒有內部直格線的外框，才以連續長正文作為題目框的輔助訊號。
    """
    lines = group_into_lines(items)
    bx0, _, bx1, _ = t["box"]
    left_edge = bx0 + 0.25 * (bx1 - bx0)
    qnos = []
    long_lines = 0
    for line in lines:
        text = normalize_qno(line["text"])
        qno = qno_of_line(text)
        if qno is not None and line["x0"] <= left_edge:
            qnos.append(qno)
        if len(re.sub(r"\s+", "", text)) >= 20:
            long_lines += 1
    if any(b == a + 1 for a, b in zip(qnos, qnos[1:])):
        return True
    # 幾乎只有外框的單欄、兩列框才用長段落輔助；有內部格線時，
    # 長段落可能只是其中一欄的說明文字。
    return len(t["xs"]) <= 2 and len(t["ys"]) <= 3 and long_lines >= 2


def ocr_page_ppocr_records(bgr, split_x=None, dpi=300, low_conf=LOW_CONF, prev_qno=None, header=0, hard=()):
    """PP-OCRv5 一頁：整欄辨識 → 圖內文字過濾 → 重組成行 → 後處理。
    回傳 (records, figs)。record = {text, conf, kind('text'|'fig'|'sep'), x0,y0,x1,y1}，
    已依閱讀順序排好（多欄：由左到右逐欄，欄與欄之間一筆 sep）；fig 是「〔含圖 k〕」佔位行。
    figs = [(x0,y0,x1,y1)] 像素座標，編號 k 即為清單順序（由上到下）。
    split_x：單一欄界（舊用法）或欄界清單（find_column_splits 的結果，橫式四欄）。
    header／hard（text_layout 的結果）：y < header 的頁首整行讀（只在 hard 欄界＝橫式頁中線切開），
    閱讀順序變成「每一頁：頁首 → 各欄」。"""
    H, W = bgr.shape[:2]
    items = []
    cuts = sorted(split_x) if isinstance(split_x, (list, tuple)) else ([split_x] if split_x else [])
    header = header if cuts else 0
    edges = [0] + cuts + [W]
    spans = list(zip(edges, edges[1:]))
    pedges = [0] + sorted(hard) + [W]
    pages = list(zip(pedges, pedges[1:]))           # 橫式頁的左右兩頁；直式頁就是整頁
    regions = [(x0, x1, 0, header, "h") for x0, x1 in pages] if header else []
    regions += [(x0, x1, header, H, "c") for x0, x1 in spans]
    for x0, x1, y0, y1, zone in regions:
        for it in ppocr_engine.ocr_lines(bgr[y0:y1, x0:x1]):
            it["box"] = [[x + x0, y + y0] for x, y in it["box"]]
            it["zone"] = zone
            items.append(it)
    # 有框線的表格：裡面的字改放進儲存格，不進正文；多數格子有字才算表格（座標圖的方格紙不算）
    tables = []
    for t in detect_tables(bgr, dpi):
        bx0, by0, bx1, by1 = t["box"]
        inside = [it for it in items if bx0 <= _center(it)[0] <= bx1 and by0 <= _center(it)[1] <= by1]
        if table_has_question_or_paragraph(t, inside):
            continue
        rows = fill_table(t, inside, bgr)
        cells = [c for r in rows for c in r]
        if not cells or sum(1 for c in cells if c[0]) < 0.5 * len(cells):
            continue
        t["rows"] = rows
        t["widths"] = [b - a for a, b in zip(t["xs"], t["xs"][1:])]
        tables.append(t)
        ids = {id(it) for it in inside}
        items = [it for it in items if id(it) not in ids]
    tab_boxes = [t["box"] for t in tables]
    cands = [c for c in figure_candidates(bgr, dpi=dpi)
             if not any(c[0] >= b[0] - 10 and c[1] >= b[1] - 10 and c[2] <= b[2] + 10 and c[3] <= b[3] + 10
                        for b in tab_boxes)]
    figs, body, partial, labels = split_figure_text(items, cands, pad=int(0.23 * dpi))
    for i, f in enumerate(figs):                     # 裁切範圍＝圖本體＋它吸收的標籤
        bs = [_center(it)[2:] for it in labels[i]]
        figs[i] = (min([f[0]] + [b[0] for b in bs]), min([f[1]] + [b[1] for b in bs]),
                   max([f[2]] + [b[2] for b in bs]), max([f[3]] + [b[3] for b in bs]))
    figs = [tuple(int(v) for v in f) for f in figs]
    for it, fig in partial:
        body.extend(dict(r, zone=it.get("zone")) for r in _reocr_masked(bgr, it, fig))

    def render(col_items, col_figs):
        lines = group_into_lines(col_items)
        for l in lines:
            l["kind"] = "text"
        for k, f in col_figs:
            # 用圖的垂直中心排序：放在題目右邊的圖，上緣常比題號還高，用上緣排會掛到上一題
            cy = (f[1] + f[3]) // 2
            lines.append({"text": f"〔含圖 {k}〕", "conf": 1.0, "kind": "fig",
                          "y0": cy, "y1": cy, "x0": f[0], "x1": f[2]})
        for k, t in col_tabs(col_figs, col_items):
            b = t["box"]
            lines.append({"text": f"〔表格 {k}〕", "conf": 1.0, "kind": "table",
                          "y0": b[1], "y1": b[1], "x0": b[0], "x1": b[2]})
        lines.sort(key=lambda l: (l["y0"] + l["y1"]) / 2)
        for l in lines:
            if l["kind"] == "text":
                l["text"] = postprocess_line(l["text"], l["conf"], low_conf)
        return lines

    numbered = list(enumerate(figs, 1))
    tab_numbered = list(enumerate(tables, 1))
    _zone = {}                                       # 目前 render 的範圍（x0,x1,是否頁首），給 col_tabs 用

    def col_tabs(col_figs, col_items):
        x0, x1, head = _zone.get("now", (0, W, False))
        return [(k, t) for k, t in tab_numbered
                if x0 <= (t["box"][0] + t["box"][2]) / 2 < x1 and (((t["box"][1] + t["box"][3]) / 2 < header) == head)]

    recs = []
    if cuts:
        is_head = lambda it: it.get("zone") == "h"
        side = 0
        for px0, px1 in pages:
            inside = lambda x: px0 <= x < px1
            head = [it for it in body if is_head(it) and inside(_center(it)[0])]
            head_figs = [(k, f) for k, f in numbered if (f[1] + f[3]) / 2 < header and inside((f[0] + f[2]) / 2)]
            _zone["now"] = (px0, px1, True)
            recs.extend(render(head, head_figs))
            for x0, x1 in spans:
                if not (px0 <= x0 and x1 <= px1):
                    continue
                col_items = [it for it in body if not is_head(it) and x0 <= _center(it)[0] < x1]
                col_figs = [(k, f) for k, f in numbered
                            if x0 <= (f[0] + f[2]) / 2 < x1 and (f[1] + f[3]) / 2 >= header]
                _zone["now"] = (x0, x1, False)
                col = render(col_items, col_figs)
                if side >= 1 and col and recs:
                    label = "右欄" if len(spans) == 2 else f"第 {side + 1} 欄"
                    recs.append({"text": f"─── ({label}) ───", "conf": 1.0, "kind": "sep",
                                 "x0": 0, "y0": 0, "x1": 0, "y1": 0})
                recs.extend(col)
                side += 1
    else:
        _zone["now"] = (0, W, False)
        recs = render(body, numbered)
    # 補回被吞掉括號的題號（需看上一題號，故在整頁排好序後處理）
    idx = [i for i, r in enumerate(recs) if r["kind"] == "text"]
    new_t, _ = restore_bare_qnos([recs[i]["text"] for i in idx], prev_qno)
    for i, t in zip(idx, new_t):
        recs[i]["text"] = t
    return recs, figs, [tuple(int(v) for v in _center(it)[2:]) for it in body], tables


def ocr_page_ppocr_lines(bgr, split_x=None, dpi=300, low_conf=LOW_CONF, prev_qno=None, header=0, hard=()):
    """同上，但回傳 (行文字清單, 圖框清單, 正文字框清單, 表格清單)；欄分隔線前後各留一個空行。
    表格在文字裡以「〔表格 k〕」佔位，內容（rows／widths）在表格清單第 k 個。"""
    recs, figs, body_boxes, tables = ocr_page_ppocr_records(bgr, split_x, dpi, low_conf, prev_qno, header, hard)
    lines = []
    for r in recs:
        if r["kind"] == "sep":
            lines += ["", r["text"], ""]
        else:
            lines.append(r["text"])
    return lines, figs, body_boxes, tables, line_geometry(recs, bgr)


def line_geometry(recs, bgr=None):
    """每一行文字的版面位置：第幾欄、離該欄左緣縮排幾個字高、行高。
    題號印在欄的最左邊，題幹與選項都會縮排——切題時比只看文字可靠（題號被手寫蓋掉也還有位置可看）。"""
    cols, col = [], []
    for r in recs:
        if r["kind"] == "sep":
            cols.append(col)
            col = []
        elif r["kind"] == "text":
            col.append(r)
    cols.append(col)
    out = []
    for ci, col in enumerate(cols):
        if not col:
            continue
        xs = sorted(r["x0"] for r in col)
        margin = xs[max(0, len(xs) // 10)]
        hs = sorted(r["y1"] - r["y0"] for r in col)
        ch = max(1.0, hs[len(hs) // 2])
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr is not None and cv2 is not None else None
        for r in col:
            indent = (r["x0"] - margin) / ch
            ink = 0.0
            # 題號位置有沒有墨跡：欄左緣到這行文字開頭之間（題號、作答括號、蓋在上面的手寫都算）
            if gray is not None and indent >= 1.0:
                y0, y1 = int(r["y0"]), int(r["y1"])
                x0, x1 = int(max(0, margin - 0.3 * ch)), int(r["x0"] - 0.25 * ch)
                if x1 > x0 and y1 > y0:
                    ink = float((gray[y0:y1, x0:x1] < 128).mean())
            out.append({"col": ci, "text": r["text"], "indent": round(indent, 2),
                        "h": round((r["y1"] - r["y0"]) / ch, 2), "y": int(r["y0"]), "margin_ink": round(ink, 3)})
    return out


def ocr_page_ppocr(bgr, split_x=None, dpi=300, low_conf=LOW_CONF):
    """PP-OCRv5 吃原圖（BGR，不二值化）。split_x 為原圖座標的欄界。回傳純文字。"""
    lines, *_ = ocr_page_ppocr_lines(bgr, split_x, dpi, low_conf)
    return "\n".join(lines)


def ocr_page_tesseract(binimg, lang="chi_tra+eng", split_columns=True):
    """舊版 Tesseract 路徑（備援）。"""
    if pytesseract is None:
        return "[未安裝 pytesseract]"
    cfg = "--oem 1 --psm 6"
    split = find_column_split(binimg) if split_columns else None
    if split:
        left = binimg[:, :split]
        right = binimg[:, split:]
        lt = pytesseract.image_to_string(left, lang=lang, config=cfg)
        rt = pytesseract.image_to_string(right, lang=lang, config=cfg)
        return lt.rstrip() + "\n\n─── (右欄) ───\n\n" + rt
    return pytesseract.image_to_string(binimg, lang=lang, config=cfg)


def ocr_page(binimg, lang="chi_tra+eng", split_columns=True, bgr=None, upscale=1.5, engine="auto", dpi=300):
    """單頁 OCR 入口。engine=auto 時優先 PP-OCRv5（需傳 bgr 原圖），否則 Tesseract。
    binimg 是 preprocess() 的結果（放大 upscale 倍），欄界在其座標系找出再換算回 bgr。"""
    if bgr is not None and resolve_engine(engine) == "ppocr":
        split = find_column_split(binimg) if split_columns else None
        split_x = int(round(split / upscale)) if split else None
        return ocr_page_ppocr(bgr, split_x, dpi)
    return ocr_page_tesseract(binimg, lang=lang, split_columns=split_columns)


# ---------- 文字清理 ----------
# 卷頭「班級／座號／姓名」後面是學生手寫的資料：只留欄位名稱（不輸出學生個資，也不是考題內容）
_RE_STUDENT = re.compile(r"(班級|座號|姓名)\s*[:：]\s*[^\s班座姓]{0,8}")


_RE_STUDENT_GRID = re.compile(r"(班級|座號|姓名)\s*[:：]?\s*[^\s班座姓]{0,8}")


def _strip_student(ln):
    """有冒號的「姓名：王小明」；表格式卷頭（同一行有兩個以上欄位名、沒有冒號）也清掉欄位後面的字"""
    ln = _RE_STUDENT.sub(r"\1：", ln)
    if sum(k in ln for k in ("班級", "座號", "姓名")) >= 2:
        ln = _RE_STUDENT_GRID.sub(r"\1：", ln)
    return ln


def tidy_text(txt):
    lines = [_strip_student(re.sub(r"[ \t]+", " ", ln)).strip() for ln in txt.splitlines()]
    out, blank = [], 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank <= 1:
                out.append("")
            continue
        blank = 0
        out.append(ln)
    return "\n".join(out).strip()


# ---------- 主流程 ----------
def _ocr_one_page(job):
    """單頁 OCR（可在子程序裡跑）：回傳這一頁的文字區塊、報告、表格、版面位置；附圖直接寫檔。"""
    pdf_path, i, out_dir, base, dpi, upscale, lang, eng_name, prev_q, pages, started = job
    skip_clean = os.environ.get("OCR_SKIP_CLEAN") == "1"
    n = i + 1
    page = pymupdf.open(pdf_path)[i]
    # 即使是有文字層的 PDF，也輸出卷面小圖，讓處理畫面逐頁可見。
    rendered = page_to_bgr(page, dpi)
    orig_img = _save_preview(rendered, out_dir, f"p{n}_orig")
    _progress(out_dir, started, "render", n, pages, "已顯示原始卷面", orig_img)
    native = page.get_text("text").strip()
    if len(native) > 40:  # 有真文字層：直接用，免 OCR
        clean_img = _save_preview(rendered, out_dir, f"p{n}_clean")
        if not skip_clean:
            _progress(out_dir, started, "clean", n, pages, "本頁沒有需要清除的筆跡", clean_img)
        _progress(out_dir, started, "ocr", n, pages, "已讀取本頁文字", clean_img)
        return {"n": n, "text": f"\n【第 {n} 頁（數位文字層）】\n{native}", "report": f"p{n}: 文字層 {len(native)} 字（免 OCR）",
                "tables": {}, "geo": [], "lines": []}
    # 掃描頁：render → (PP-OCR：圖區偵測＋圖內文字過濾｜Tesseract：舊版輪廓裁圖) → OCR
    bgr = rendered
    t0 = time.time()
    turned, inked = 0, 0.0
    if eng_name == "ppocr":
        bgr, turned, dehand_boxes = fix_orientation(bgr, return_boxes=True)
        if not skip_clean:
            bgr, inked = remove_color_ink(bgr)
            bgr, dehanded = dehand.erase(bgr, dpi, detected_boxes=dehand_boxes)
            inked += dehanded
    clean_img = _save_preview(bgr, out_dir, f"p{n}_clean")
    if not skip_clean:
        _progress(out_dir, started, "clean", n, pages, "已轉正並清除筆跡", clean_img)
    binimg = preprocess(bgr, upscale=upscale)
    H, W = bgr.shape[:2]
    lines = []
    if eng_name == "ppocr":
        lay = text_layout(bgr)
        if not lay["cuts"]:
            lay = {"cuts": [int(round(s / upscale)) for s in find_column_splits(binimg)], "hard": [], "header": 0}
        lines, fig_px, body_px, tables, geo = ocr_page_ppocr_lines(bgr, lay["cuts"], dpi, prev_qno=prev_q,
                                                                   header=lay["header"], hard=lay["hard"])
        txt = tidy_text("\n".join(lines))
        pad = int(0.05 * dpi)                    # 圖框已含標籤，只留一點白邊
        figs = [(max(0, x0 - pad) / W, max(0, y0 - pad) / H, min(W, x1 + pad) / W, min(H, y1 + pad) / H)
                for x0, y0, x1, y1 in fig_px]
    else:
        figs = detect_figures(binimg, page.rect.height)
        body_px, tables, geo = [], [], []
        txt = tidy_text(ocr_page(binimg, lang=lang, bgr=bgr, upscale=upscale, engine="tesseract"))
    dt = time.time() - t0
    for k, (x0, y0, x1, y1) in enumerate(figs, 1):
        cx0, cy0, cx1, cy1 = int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)
        crop = bgr[cy0:cy1, cx0:cx1].copy()
        for bx0, by0, bx1, by1 in body_px:       # 白邊夾到的正文（半截題目、選項）塗白
            if bx1 > cx0 and bx0 < cx1 and by1 > cy0 and by0 < cy1:
                crop[max(0, by0 - cy0 - 3):max(0, by1 - cy0 + 3), max(0, bx0 - cx0 - 3):max(0, bx1 - cx0 + 3)] = 255
        if cv2 is not None and crop.size:
            _imwrite(os.path.join(out_dir, f"{base}_p{n}_fig{k}.png"), crop)
    fig_note = f"（本頁偵測到 {len(figs)} 個圖塊，已裁為 *_p{n}_fig*.png；文中以〔含圖〕標示）" if figs else ""
    for g in geo:
        g["page"] = n
    _progress(out_dir, started, "ocr", n, pages, "已辨識本頁文字", clean_img)
    return {"n": n, "text": f"\n【第 {n} 頁（掃描→OCR）{fig_note}】\n{txt}", "lines": lines,
            "tables": {f"p{n}_t{k}": {"rows": t["rows"], "widths": t["widths"]} for k, t in enumerate(tables, 1)},
            "geo": geo,
            "report": f"p{n}: 掃描OCR[{eng_name}]，{len(txt)} 字，{len(figs)} 圖，OCR {dt:.1f}s"
                      + (f"，已自動轉正 {turned}°" if turned else "")
                      + (f"，已清除紅藍筆跡 {inked:.2%}" if inked >= 0.0001 else "")}


def process(pdf_path, out_dir, dpi=300, only_page=None, upscale=1.5, lang="chi_tra+eng", engine="auto"):
    """整份 PDF。預設逐頁；OCR_WORKERS>1 可多頁同時處理（這台 M2 Pro 實測沒有比較快）。
    逐頁時會把上一頁最後的題號傳給下一頁（補回被吞掉括號的題號）；平行時各頁獨立，差別很小。"""
    os.makedirs(out_dir, exist_ok=True)
    n_pages = pymupdf.open(pdf_path).page_count
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    eng_name = resolve_engine(engine)
    pages = [i for i in range(n_pages) if not only_page or i + 1 == only_page]
    started = time.time()
    # 預設逐頁：實測 4 頁平行反而較慢（61s→71s），OCR 引擎本身已吃滿所有核心；多台小主機或 GPU 再設 OCR_WORKERS>1
    workers = int(os.environ.get("OCR_WORKERS", "1"))
    results = []
    if workers > 1 and len(pages) > 1:
        import concurrent.futures as cf
        import multiprocessing as mp
        jobs = [(pdf_path, i, out_dir, base, dpi, upscale, lang, eng_name, None, len(pages), started) for i in pages]
        with cf.ProcessPoolExecutor(max_workers=min(workers, len(pages)), mp_context=mp.get_context("spawn")) as ex:
            results = list(ex.map(_ocr_one_page, jobs))
    else:
        prev_q = None
        for i in pages:
            r = _ocr_one_page((pdf_path, i, out_dir, base, dpi, upscale, lang, eng_name, prev_q, len(pages), started))
            prev_q = next((q for q in (qno_of_line(l) for l in reversed(r["lines"])) if q is not None), prev_q)
            results.append(r)
    results.sort(key=lambda r: r["n"])
    all_text = [r["text"] for r in results]
    report = [r["report"] for r in results]
    all_tables = {k: v for r in results for k, v in r["tables"].items()}
    all_geo = [g for r in results for g in r["geo"]]
    full = "\n".join(all_text).strip()
    if all_geo:                                      # 每行的版面位置（給切題用）
        import json
        with open(os.path.join(out_dir, f"{base}_lines.json"), "w", encoding="utf-8") as f:
            json.dump(all_geo, f, ensure_ascii=False)
    if all_tables:                                   # 〔表格 k〕的內容：{"p頁_tk": {"rows", "widths"(像素)}}
        import json
        with open(os.path.join(out_dir, f"{base}_tables.json"), "w", encoding="utf-8") as f:
            json.dump(all_tables, f, ensure_ascii=False)
    txt_path = os.path.join(out_dir, f"{base}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(full)
    docx_path = write_docx(full, os.path.join(out_dir, f"{base}.docx"))
    return {"txt": txt_path, "docx": docx_path, "report": report}


def write_docx(text, path):
    from docx import Document
    from docx.shared import Pt
    d = Document()
    style = d.styles["Normal"]
    style.font.name = "DFPYuanMedium-B5"
    style.font.size = Pt(12)
    for para in text.split("\n"):
        d.add_paragraph(para)
    d.save(path)
    return path


def main():
    ap = argparse.ArgumentParser(description="純 Python 考卷前處理（PyMuPDF+OpenCV+PP-OCRv5(Tesseract備援)+docx）")
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", default="out", help="輸出資料夾")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--page", type=int, default=None, help="只處理某一頁（除錯用）")
    ap.add_argument("--upscale", type=float, default=1.5)
    ap.add_argument("--lang", default="chi_tra+eng", help="僅 Tesseract 備援使用")
    ap.add_argument("--engine", choices=["auto", "ppocr", "tesseract"], default="auto",
                    help="auto=有 ocr/models 就用 PP-OCRv5，否則 Tesseract")
    args = ap.parse_args()
    r = process(args.pdf, args.out, dpi=args.dpi, only_page=args.page,
                upscale=args.upscale, lang=args.lang, engine=args.engine)
    print("✅ 完成")
    for line in r["report"]:
        print("  ", line)
    print("  TXT :", r["txt"])
    print("  DOCX:", r["docx"])


if __name__ == "__main__":
    sys.exit(main())
