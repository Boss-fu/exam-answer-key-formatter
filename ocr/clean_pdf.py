#!/usr/bin/env python3
"""本機考卷清理器：用現有 dehand 模型／鉛筆規則產生可列印的乾淨 PDF。

這條路徑刻意不呼叫任何外部 AI。輸出是清理後的影像 PDF，原始檔不會被覆蓋；
題庫與 Word 仍走既有 OCR 流程。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import cv2
import numpy as np
import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dehand.apply import erase  # noqa: E402
from pyexam import fix_orientation, remove_color_ink  # noqa: E402


def clean_page(img: np.ndarray, dpi: int) -> tuple[np.ndarray, float]:
    # 彩色進來：先清紅藍筆與校徽浮水印（跟 OCR 同一套 remove_color_ink），再交給去筆跡模型
    bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else remove_color_ink(img, restore_text=True)[0]
    # 橫掃／倒放的頁先轉正：去筆跡模型與題號保護都是在正向考卷上學的，轉 90° 的頁手寫幾乎擦不掉
    bgr, _ = fix_orientation(bgr)
    cleaned, ratio = erase(bgr, dpi=dpi)
    return cv2.cvtColor(cleaned, cv2.COLOR_BGR2GRAY), ratio


def clean_pdf(src: str, dst: str, dpi: int = 200) -> dict:
    source = pymupdf.open(src)
    output = pymupdf.open()
    ratios: list[float] = []
    for page in source:
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        cleaned, ratio = clean_page(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), dpi)
        ok, encoded = cv2.imencode(".png", cleaned)
        if not ok:
            raise RuntimeError("清理後的頁面無法編碼")
        h, w = cleaned.shape[:2]          # 轉正後寬高可能對調，頁面跟著清稿的方向
        out_page = output.new_page(width=w * 72 / dpi, height=h * 72 / dpi)
        out_page.insert_image(out_page.rect, stream=encoded.tobytes())
        ratios.append(ratio)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    output.save(dst, garbage=4, deflate=True)
    output.close()
    source.close()
    return {"pages": len(ratios), "cleared_ratio": sum(ratios) / len(ratios) if ratios else 0.0}


def clean_image(src: str, dst: str, dpi: int = 200) -> dict:
    image = cv2.imread(src, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("讀不到圖片")
    cleaned, ratio = clean_page(image, dpi)
    doc = pymupdf.open()
    width = image.shape[1] * 72 / dpi
    height = image.shape[0] * 72 / dpi
    page = doc.new_page(width=width, height=height)
    ok, encoded = cv2.imencode(".png", cleaned)
    if not ok:
        raise RuntimeError("清理後的圖片無法編碼")
    page.insert_image(page.rect, stream=encoded.tobytes())
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    doc.save(dst, garbage=4, deflate=True)
    doc.close()
    return {"pages": 1, "cleared_ratio": ratio}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("--dpi", type=int, default=200)
    args = parser.parse_args()
    ext = os.path.splitext(args.source)[1].lower()
    stats = clean_pdf(args.source, args.output, args.dpi) if ext == ".pdf" else clean_image(args.source, args.output, args.dpi)
    print(json.dumps({"ok": True, **stats}, ensure_ascii=False))


if __name__ == "__main__":
    main()
