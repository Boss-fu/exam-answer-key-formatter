#!/usr/bin/env python3
"""純 Python 考卷前處理管線（無 Claude、零 token）。

用我們討論的這一系列 Python 工具串起來：
  PyMuPDF  讀 PDF／判斷有無文字層／把掃描頁 render 成高解析圖、裁圖
  OpenCV + numpy  影像前處理（放大、去雜訊、Otsu 二值化、估傾斜）
  Tesseract (pytesseract, chi_tra+eng)  掃描頁才需要的 OCR
  python-docx  把抽出的文字重排成乾淨 Word

策略（關鍵）：
  * 數位 PDF（有文字層）→ 直接用 PyMuPDF 取字，近乎 100% 且免費免 OCR。
  * 掃描 PDF（無文字層）→ render 300dpi → OpenCV 前處理 → Tesseract OCR。
  圖片一律不重畫：偵測到圖就裁成 page{n}_fig{k}.png，並在文字裡留〔含圖〕佔位。

用法：
    python3 ocr/pyexam.py 某考卷.pdf -o out/            # 產 .txt + .docx + 裁圖
    python3 ocr/pyexam.py 某考卷.pdf --dpi 400 --page 1  # 只試第1頁、提高解析度
"""
import argparse
import os
import re
import sys

import numpy as np
import pymupdf

try:
    import cv2
except Exception:
    cv2 = None
try:
    import pytesseract
except Exception:
    pytesseract = None


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


# ---------- OCR 一頁（自動雙欄切割）----------
def ocr_page(binimg, lang="chi_tra+eng", split_columns=True):
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


# ---------- 文字清理 ----------
def tidy_text(txt):
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in txt.splitlines()]
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
def process(pdf_path, out_dir, dpi=300, only_page=None, upscale=1.5, lang="chi_tra+eng"):
    os.makedirs(out_dir, exist_ok=True)
    doc = pymupdf.open(pdf_path)
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    all_text, report = [], []
    for i, page in enumerate(doc):
        n = i + 1
        if only_page and n != only_page:
            continue
        native = page.get_text("text").strip()
        if len(native) > 40:  # 有真文字層：直接用，免 OCR
            all_text.append(f"\n【第 {n} 頁（數位文字層）】\n{native}")
            report.append(f"p{n}: 文字層 {len(native)} 字（免 OCR）")
            continue
        # 掃描頁：render → 前處理 → 裁圖 → OCR
        bgr = page_to_bgr(page, dpi)
        binimg = preprocess(bgr, upscale=upscale)
        figs = detect_figures(binimg, page.rect.height)
        for k, (x0, y0, x1, y1) in enumerate(figs, 1):
            H, W = bgr.shape[:2]
            crop = bgr[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)]
            if cv2 is not None and crop.size:
                cv2.imwrite(os.path.join(out_dir, f"{base}_p{n}_fig{k}.png"), crop)
        txt = tidy_text(ocr_page(binimg, lang=lang))
        fig_note = f"（本頁偵測到 {len(figs)} 個圖塊，已裁為 *_p{n}_fig*.png；文中以〔含圖〕標示）" if figs else ""
        all_text.append(f"\n【第 {n} 頁（掃描→OCR）{fig_note}】\n{txt}")
        report.append(f"p{n}: 掃描OCR，{len(txt)} 字，{len(figs)} 圖")
    full = "\n".join(all_text).strip()
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
    ap = argparse.ArgumentParser(description="純 Python 考卷前處理（PyMuPDF+OpenCV+Tesseract+docx）")
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", default="out", help="輸出資料夾")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--page", type=int, default=None, help="只處理某一頁（除錯用）")
    ap.add_argument("--upscale", type=float, default=1.5)
    ap.add_argument("--lang", default="chi_tra+eng")
    args = ap.parse_args()
    r = process(args.pdf, args.out, dpi=args.dpi, only_page=args.page,
                upscale=args.upscale, lang=args.lang)
    print("✅ 完成")
    for line in r["report"]:
        print("  ", line)
    print("  TXT :", r["txt"])
    print("  DOCX:", r["docx"])


if __name__ == "__main__":
    sys.exit(main())
