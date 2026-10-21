#!/usr/bin/env python3
"""把既有去筆跡資料轉為 std/<考卷名>/NNN_{in,gt,info} 三檔格式。"""
import argparse
from pathlib import Path
import re

import cv2

from std_format import write_page


def page_number(stem, fallback):
    match = re.search(r"(?:^|_)p(\d+)", stem)
    return int(match.group(1)) if match else fallback


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path(__file__).parent / "data")
    ap.add_argument("--limit", type=int, default=0, help="只轉換前 N 頁（煙霧測試用）")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(); data = args.data; output = data / "std"; converted = skipped = 0
    for x_path in sorted(data.rglob("*_x.png")):
        if "std" in x_path.relative_to(data).parts or "_strokes" in x_path.relative_to(data).parts:
            continue
        stem = x_path.name[:-6]; parent = x_path.parent
        parts = x_path.relative_to(data).parts
        source = "合成" if any(part.startswith("_synth") for part in parts) else ("人工" if "_manual" in parts else "配對")
        clean_path = parent / (stem + "_c.png")
        mask_path = parent / (stem + "_m.png")
        x = cv2.imread(str(x_path), cv2.IMREAD_GRAYSCALE)
        if source == "人工":
            mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
            if x is None or mask is None: skipped += 1; continue
            clean = x.copy(); clean[mask > 127] = 255; repaired = True
        else:
            clean = cv2.imread(str(clean_path), cv2.IMREAD_GRAYSCALE)
            if x is None or clean is None: skipped += 1; continue
            repaired = False
        exam = parent.name if source != "人工" else re.sub(r"_[0-9a-f]{4}_p\d+$", "", stem)
        page = page_number(stem, converted + 1)
        _, in_path, gt_path, info_path = __import__("std_format").paths(output, exam, page)
        exists = all(path.exists() for path in (in_path, gt_path, info_path))
        write_page(output, exam, page, x, clean, repaired=repaired, source=source, overwrite=args.overwrite)
        converted += not exists or args.overwrite
        if args.limit and converted >= args.limit: break
    print(f"完成：{converted} 頁；略過：{skipped} 頁；輸出：{output}")


if __name__ == "__main__": main()
