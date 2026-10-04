"""人工去筆跡資料的共用命名與儲存規則。"""
import hashlib
import json
import os
import re

import cv2

from pairs_build import key


def manual_stem(pdf_path, page, *, absolute_hash=False):
    """回傳人工標註頁的既有檔名格式。"""
    source = os.path.abspath(pdf_path) if absolute_hash else pdf_path
    stem = re.sub(r'[\\/:*?"<>|\s]+', "_", os.path.splitext(os.path.basename(pdf_path))[0])[:50]
    digest = hashlib.md5(source.encode()).hexdigest()[:4]
    return f"{stem}_{digest}_p{page}"


def exam_group(pdf_path):
    """同一考卷的人工頁使用同一訓練分組。"""
    return hashlib.md5(key(os.path.basename(pdf_path)).encode()).hexdigest()[:8]


def save_manual_page(directory, pdf_path, page, gray, mask, *, absolute_hash=False):
    """寫入 _x/_m.png 與 train.py 所需的 manifest，回傳頁面 stem。"""
    os.makedirs(directory, exist_ok=True)
    stem = manual_stem(pdf_path, page, absolute_hash=absolute_hash)
    cv2.imwrite(os.path.join(directory, stem + "_x.png"), gray)
    cv2.imwrite(os.path.join(directory, stem + "_m.png"), mask)

    manifest_path = os.path.join(directory, "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as file:
            manifest = json.load(file)
    else:
        manifest = {}
    manifest[stem] = exam_group(pdf_path)
    with open(manifest_path, "w", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=1)
    return stem
