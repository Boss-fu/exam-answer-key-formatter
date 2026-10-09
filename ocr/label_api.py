#!/usr/bin/env python3
"""人工修正工作台的本機資料 API。所有輸出均為 JSON，學生資料只留在 gitignore 路徑。"""
import argparse
import base64
import hashlib
import json
import os
import re
import sys

import cv2
import numpy as np
import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "dehand"))

os.environ.pop("DEHAND", None)   # 工作台一律用模型當「建議」（人會再修）；服務的 DEHAND=0 只關 OCR 流程裡的自動擦除
import apply as dehand  # noqa: E402
import manual_store
import to_zizi_word as Z

MANUAL = os.path.join(HERE, "dehand", "data", "_manual")
QUESTIONS = os.path.join(HERE, "labels", "questions")
PYOCR = os.path.join(REPO, "local-app", "output", "py-ocr")


def emit(value):
    print(json.dumps(value, ensure_ascii=False))


def safe(value):
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", str(value))


def dataurl(image):
    encoded = cv2.imencode(".png", image)[1]
    return "data:image/png;base64," + base64.b64encode(encoded).decode()


def page_gray(pdf_path, page):
    with pymupdf.open(pdf_path) as document:
        pixmap = document[page - 1].get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
        gray = np.frombuffer(pixmap.samples, np.uint8).reshape(pixmap.h, pixmap.w).copy()
        return gray, len(document)


def mask_for(gray):
    state = dehand._load()
    if not state:
        return np.zeros_like(gray)
    return (dehand.predict(gray, state[0]) >= state[1]).astype(np.uint8) * 255


def cache_dirs():
    output = []
    if not os.path.isdir(PYOCR):
        return output
    for job in os.listdir(PYOCR):
        directory = os.path.join(PYOCR, job, "result")
        if os.path.isdir(directory) and any(name.endswith(".txt") for name in os.listdir(directory)):
            output.append((job, directory))
    return sorted(output, reverse=True)


def get_job_source(job):
    directory = cache(job)
    job_dir = os.path.dirname(directory)
    exts = (".pdf", ".jpg", ".jpeg", ".png")
    sources = [os.path.join(job_dir, f) for f in os.listdir(job_dir) if f.lower().endswith(exts) and os.path.isfile(os.path.join(job_dir, f))]
    if not sources:
        raise ValueError("找不到原始檔案")
    return sources[0]

def cache(job):
    directory = os.path.join(PYOCR, safe(job), "result")
    if not os.path.isdir(directory):
        raise ValueError("找不到 OCR 工作")
    return directory


def source_name(directory):
    return next(name for name in os.listdir(directory) if name.endswith(".txt"))


def stats():
    manual_pages = sum(1 for _root, _dirs, files in os.walk(MANUAL) for name in files if name.endswith("_m.png")) if os.path.isdir(MANUAL) else 0
    question_sets = len([name for name in os.listdir(QUESTIONS) if name.endswith(".json")]) if os.path.isdir(QUESTIONS) else 0
    return {"manualPages": manual_pages, "questionSets": question_sets}


def list_jobs():
    jobs = [{"id": job, "name": source_name(directory)[:-4]} for job, directory in cache_dirs()]
    return {"jobs": jobs, "stats": stats()}


def submitted_pages(pdf, pages):
    return [page for page in range(1, pages + 1)
            if os.path.exists(os.path.join(MANUAL, manual_store.manual_stem(pdf, page, absolute_hash=True) + "_m.png"))]


def render(args):
    pdf = get_job_source(args.job) if args.job else args.pdf
    gray, pages = page_gray(pdf, args.page)
    stem = manual_store.manual_stem(pdf, args.page, absolute_hash=True)
    mask_path = os.path.join(MANUAL, stem + "_m.png")
    source_path = os.path.join(MANUAL, stem + "_x.png")
    if os.path.exists(mask_path):
        gray = cv2.imread(source_path, cv2.IMREAD_GRAYSCALE)
        mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
        done = True
    else:
        mask = mask_for(gray)
        done = False
    clean = gray.copy()
    clean[mask > 127] = 255
    return {"pages": pages, "page": args.page, "done": done, "submittedPages": submitted_pages(pdf, pages), "name": os.path.basename(pdf), "x": dataurl(gray), "m": dataurl(mask), "clean": dataurl(clean), "stats": stats()}


def save_mask(args):
    pdf = get_job_source(args.job) if args.job else args.pdf
    gray, pages = page_gray(pdf, args.page)
    with open(args.mask, "rb") as file:
        raw = file.read()
    if raw.startswith(b"data:"):
        raw = base64.b64decode(raw.split(b",", 1)[1])
    mask = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
    if mask is None or mask.shape != gray.shape:
        raise ValueError("遮罩尺寸不符")
    stem = manual_store.save_manual_page(MANUAL, pdf, args.page, gray, (mask > 127).astype(np.uint8) * 255, absolute_hash=True)
    return {"saved": stem, "pages": pages, "submittedPages": submitted_pages(pdf, pages), "stats": stats()}


def job_page(args):
    directory = cache(args.job)
    job_dir = os.path.dirname(directory)
    exts = (".pdf", ".jpg", ".jpeg", ".png")
    sources = [os.path.join(job_dir, f) for f in os.listdir(job_dir) if f.lower().endswith(exts) and os.path.isfile(os.path.join(job_dir, f))]
    if not sources:
        raise ValueError("找不到原始檔案")
    source = sources[0]

    pages = 1
    if source.lower().endswith(".pdf"):
        with pymupdf.open(source) as document:
            pages = len(document)
            if args.page < 1 or args.page > pages:
                raise ValueError("頁數超出範圍")
            pixmap = document[args.page - 1].get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
            gray = np.frombuffer(pixmap.samples, np.uint8).reshape(pixmap.h, pixmap.w).copy()
    else:
        gray = cv2.imread(source, cv2.IMREAD_GRAYSCALE)
        if args.page != 1:
            raise ValueError("圖片只有一頁")

    if gray.shape[1] > 1400 or gray.shape[0] > 2000:
        scale = min(1400 / gray.shape[1], 2000 / gray.shape[0])
        gray = cv2.resize(gray, (int(gray.shape[1] * scale), int(gray.shape[0] * scale)), interpolation=cv2.INTER_AREA)

    if args.view == "dehand":
        mask = mask_for(gray)
        clean = gray.copy()
        clean[mask > 127] = 255
        img = clean
    else:
        img = gray

    encoded = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1]
    b64 = base64.b64encode(encoded).decode()
    return {"job": args.job, "page": args.page, "pages": pages, "image": "data:image/jpeg;base64," + b64}


def draft(args):
    directory = cache(args.job)
    text, _pages = __import__("ai_judge").draft(directory)
    _meta, lines = Z.read_lines(directory)
    human = os.path.join(directory, "ai_human.json")
    current = human if os.path.exists(human) else os.path.join(directory, "ai_auto.json")
    if os.path.exists(current):
        with open(current, encoding="utf-8") as file:
            decision = json.load(file)
    else:
        decision = {"q": [], "noise": [], "fix": []}
    return {"job": args.job, "draft": text, "lines": [{"line": index, "page": page, "text": text} for index, (page, text) in enumerate(lines)], "decision": decision, "stats": stats()}


def question_filename(directory):
    """以原卷 stem 保存題目標註；同名不同卷以工作路徑短 hash 區分。"""
    stem = safe(os.path.splitext(source_name(directory))[0]) or "exam"
    filename = stem + ".json"
    target = os.path.join(QUESTIONS, filename)
    if not os.path.exists(target):
        return filename
    with open(target, encoding="utf-8") as file:
        existing = json.load(file)
    if existing.get("_meta", {}).get("source_dir") == os.path.abspath(directory):
        return filename
    digest = hashlib.md5(os.path.abspath(directory).encode()).hexdigest()[:8]
    return f"{stem}_{digest}.json"


def save_questions(args):
    directory = cache(args.job)
    with open(args.data, encoding="utf-8") as file:
        value = json.load(file)
    line_count = len(Z.read_lines(directory)[1])
    valid_line = lambda number: isinstance(number, int) and 0 <= number < line_count
    output = {"q": sorted(set(number for number in value.get("q", []) if valid_line(number))), "noise": sorted(set(number for number in value.get("noise", []) if valid_line(number))), "fix": []}
    for fix in value.get("fix", []):
        if isinstance(fix, dict) and valid_line(fix.get("l")) and isinstance(fix.get("from"), str) and isinstance(fix.get("to"), str):
            output["fix"].append(fix)
    output["_meta"] = {"backend": "human", "source_dir": os.path.abspath(directory)}
    os.makedirs(QUESTIONS, exist_ok=True)
    with open(os.path.join(directory, "ai_human.json"), "w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=1)
    filename = question_filename(directory)
    with open(os.path.join(QUESTIONS, filename), "w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=1)
    return {"saved": filename, "stats": stats()}


def rebuild(args):
    directory = cache(args.job)
    __import__("ai_judge").judge(directory, "auto")
    docx, _review, status = Z.build(directory, os.path.join(directory, "word"), src_name=os.path.basename(directory), ai="human")
    return {"docx": os.path.relpath(docx, directory), "questions": status["questions"]}


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd", required=True)
    subparsers.add_parser("list")
    for command in ("render", "save-mask"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--pdf")
        subparser.add_argument("--job")
        subparser.add_argument("--page", type=int, required=True)
        if command == "save-mask":
            subparser.add_argument("--mask", required=True)
    subparser = subparsers.add_parser("draft")
    subparser.add_argument("--job", required=True)
    subparser = subparsers.add_parser("save-questions")
    subparser.add_argument("--job", required=True)
    subparser.add_argument("--data", required=True)
    subparser = subparsers.add_parser("rebuild")
    subparser.add_argument("--job", required=True)
    subparser = subparsers.add_parser("job-page")
    subparser.add_argument("--job", required=True)
    subparser.add_argument("--page", type=int, required=True)
    subparser.add_argument("--view", choices=["original", "dehand"], default="original")
    args = parser.parse_args()
    actions = {"render": render, "save-mask": save_mask, "draft": draft, "save-questions": save_questions, "rebuild": rebuild, "job-page": job_page}
    result = list_jobs() if args.cmd == "list" else actions[args.cmd](args)
    emit({"ok": True, **result})


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        emit({"ok": False, "error": str(error)})
        sys.exit(1)
