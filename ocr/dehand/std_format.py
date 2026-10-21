"""去筆跡訓練資料的標準三檔格式讀寫。"""
from datetime import date
from pathlib import Path
import re

import cv2

STD_WIDTH = 1240  # 150 dpi A4 的寬度


def exam_name(value):
    """保留可讀卷名，但禁止路徑字元。"""
    value = Path(str(value)).stem.strip()
    return re.sub(r'[\\/:*?"<>|]+', "_", value) or "未命名考卷"


def page_stem(page):
    return f"{int(page):03d}"


def normalize(image, width=STD_WIDTH, interpolation=cv2.INTER_AREA):
    if image is None or image.ndim != 2:
        raise ValueError("標準格式只接受灰階影像")
    if image.shape[1] == width:
        return image
    height = max(1, round(image.shape[0] * width / image.shape[1]))
    return cv2.resize(image, (width, height), interpolation=interpolation)


def paths(root, exam, page):
    directory = Path(root) / exam_name(exam)
    stem = page_stem(page)
    return directory, directory / f"{stem}_in.jpg", directory / f"{stem}_gt.png", directory / f"{stem}_info.txt"


def write_page(root, exam, page, original, clean, *, repaired, source, when=None, overwrite=True):
    """寫入一頁標準資料，回傳三個檔案路徑。"""
    directory, in_path, gt_path, info_path = paths(root, exam, page)
    if not overwrite and all(path.exists() for path in (in_path, gt_path, info_path)):
        return in_path, gt_path, info_path
    original = normalize(original)
    clean = normalize(clean)
    if original.shape != clean.shape:
        raise ValueError("原稿與乾淨稿尺寸不符")
    directory.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(in_path), original, [cv2.IMWRITE_JPEG_QUALITY, 95]):
        raise OSError(f"無法寫入 {in_path}")
    if not cv2.imwrite(str(gt_path), clean, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise OSError(f"無法寫入 {gt_path}")
    info_path.write_text(
        f"{exam_name(exam)} 第{int(page)}頁\n修補過：{'是' if repaired else '否'}\n來源：{source}\n日期：{when or date.today().isoformat()}\n",
        encoding="utf-8",
    )
    return in_path, gt_path, info_path


def read_info(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "：" in line:
            key, value = line.split("：", 1)
            values[key] = value
    return values


def iter_pages(root):
    for info in sorted(Path(root).glob("*/*_info.txt")):
        stem = info.name[:-9]
        in_path, gt_path = info.with_name(stem + "_in.jpg"), info.with_name(stem + "_gt.png")
        if in_path.is_file() and gt_path.is_file():
            yield info.parent.name, int(stem), in_path, gt_path, info, read_info(info)
