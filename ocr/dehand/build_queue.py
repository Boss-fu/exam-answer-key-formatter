#!/usr/bin/env python3
"""離線建立人工去筆跡工作台的待修正清單。"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OCR = os.path.dirname(HERE)
sys.path.insert(0, OCR)

import cv2  # noqa: E402
import label_api  # noqa: E402

QUEUE = os.path.join(HERE, "data", "_queue.json")
LOCK = QUEUE + ".building"


def key(item):
    return (item["source"], item.get("pseudo") or item.get("job"), item["page"])


def pages():
    """列出目前所有可覆核頁；這段不做模型推論。"""
    if os.path.isdir(label_api.PSEUDO):
        for group in sorted(os.listdir(label_api.PSEUDO)):
            directory = os.path.join(label_api.PSEUDO, group)
            if not os.path.isdir(directory):
                continue
            for name in sorted(os.listdir(directory)):
                match = label_api.re.fullmatch(r"p(\d+)_x\.png", name)
                if match:
                    page = int(match.group(1))
                    yield {"source": "pseudo", "pseudo": f"{group}/p{page}", "name": group,
                           "page": page, "path": os.path.join(directory, name)}
    for job, _directory in label_api.cache_dirs()[:20]:
        try:
            pdf = label_api.get_job_source(job)
            with label_api.pymupdf.open(pdf) as document:
                count = len(document)
            for page in range(1, count + 1):
                yield {"source": "job", "job": job, "name": os.path.basename(pdf), "page": page,
                       "pdf": pdf}
        except Exception:
            continue


def image(item):
    if item["source"] == "pseudo":
        return cv2.imread(item.pop("path"), cv2.IMREAD_GRAYSCALE)
    gray, _ = label_api.page_gray(item.pop("pdf"), item["page"])
    return gray


def read_old():
    try:
        with open(QUEUE, encoding="utf-8") as file:
            return {key(item): item for item in json.load(file).get("items", [])}
    except (OSError, ValueError, TypeError):
        return {}


def write(value):
    os.makedirs(os.path.dirname(QUEUE), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="_queue.", suffix=".json", dir=os.path.dirname(QUEUE))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=1)
            file.write("\n")
        os.replace(temporary, QUEUE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="僅掃描前 N 個尚未快取頁面（測試用）")
    args = parser.parse_args()
    try:
        old = read_old()
        items, pending = [], 0
        state = None
        for item in pages():
            cached = old.get(key(item))
            if cached is not None:
                items.append(cached)
                continue
            if args.limit is not None and pending >= args.limit:
                continue
            pending += 1
            gray = image(item)
            if gray is None:
                continue
            if state is None:
                state = label_api.dehand._load()
            item.pop("path", None)
            item.pop("pdf", None)
            item["score"] = label_api.uncertainty(gray, state)
            items.append(item)
        items.sort(key=lambda item: item["score"], reverse=True)
        write({"generatedAt": datetime.now(timezone.utc).isoformat(), "complete": args.limit is None, "items": items})
        print(json.dumps({"ok": True, "items": len(items), "computed": pending}, ensure_ascii=False))
    finally:
        try:
            os.unlink(LOCK)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
