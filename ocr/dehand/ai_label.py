#!/usr/bin/env python3
"""用視覺 AI 產生去筆跡「偽標註」，供訓練候選模型使用。

這是開發資料工具，不是正式 OCR 路徑：正式掃描完全不呼叫 AI。
AI 只看到單頁影像，輸出手寫筆跡的矩形候選；本程式再以本機 OCR 的
高信心印刷字框保護印刷內容，並把結果存到 data/_pseudo/（訓練權重較低）。

用法：
  python ocr/dehand/ai_label.py 考卷.pdf --pages 1,2
  python ocr/dehand/ai_label.py 考卷.pdf --all --engine codex

需要已登入的 Codex CLI。產生完先用 --preview 看三聯圖；偽標註不可直接
晉升正式模型，仍須通過 train.py 的印刷誤擦門檻和端到端驗收。
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import cv2
import numpy as np
import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
OCR_ROOT = os.path.dirname(HERE)
sys.path.insert(0, OCR_ROOT)
from dehand.pairs_build import ink  # noqa: E402

DPI = 150
PROMPT = """Inspect the attached school exam page and return ONLY valid JSON. The image may be high-resolution; use its actual pixel coordinates. Identify every handwritten student pencil/pen mark: answer brackets/letters, circles, calculations, marginal notes, underlines, and crossing-out. Never include printed exam text, printed diagrams, printed tables, page numbers, or normal scan noise. Schema: {\"width\":integer,\"height\":integer,\"handwriting\":[{\"x\":integer,\"y\":integer,\"w\":integer,\"h\":integer,\"kind\":\"answer|circle|calculation|note|mark\",\"confidence\":number}]}. Use tight boxes plus 4 pixels. If uncertain, omit."""


def safe_name(path):
    return re.sub(r"[\\/:*?\"<>|\s]+", "_", os.path.splitext(os.path.basename(path))[0])[:60]


def group_for(path):
    return "ai:" + hashlib.md5(os.path.basename(path).encode()).hexdigest()[:8]


def render(page):
    pix = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w).copy()


def codex_boxes(gray, codex_bin):
    """Ask a separate, read-only visual session for rectangles, never for file edits."""
    with tempfile.TemporaryDirectory(prefix="dehand-ai-") as d:
        image = os.path.join(d, "page.png")
        answer = os.path.join(d, "answer.json")
        cv2.imwrite(image, gray)
        cmd = [codex_bin, "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only",
               "-i", image, "-o", answer, "--", PROMPT]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode or not os.path.exists(answer):
            raise RuntimeError("Codex vision failed: " + (result.stderr[-500:] or result.stdout[-500:]))
        raw = open(answer, encoding="utf-8").read().strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Codex did not return JSON: {raw[:200]!r}") from e


def gemini_boxes(gray, agy_bin):
    """Use Antigravity's Gemini CLI in an isolated image directory, like codex_boxes."""
    with tempfile.TemporaryDirectory(prefix="dehand-ai-") as d:
        image = os.path.join(d, "page.jpg")
        cv2.imwrite(image, gray, [cv2.IMWRITE_JPEG_QUALITY, 95])
        result = subprocess.run(
            [agy_bin, "--model", "gemini-3.8-flash-high", "--print-timeout", "300s", "-p", f"{PROMPT}\nThe image is {gray.shape[1]}x{gray.shape[0]} pixels (width x height); estimate coordinates visually. "
             "Do not run shell commands or scripts.\n@page.jpg"],
            cwd=d, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode:
            raise RuntimeError("Gemini vision failed: " + (result.stderr[-500:] or result.stdout[-500:]))
        raw = result.stdout.strip()
    # Gemini may wrap an otherwise identical object in a Markdown fence.
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Gemini did not return JSON: {raw[:200]!r}") from e


def printed_mask(gray):
    """Protect only high-confidence printed text. Low-confidence handwriting stays eligible."""
    try:
        from ppocr_engine import ocr_lines
        lines = ocr_lines(gray)
    except Exception as e:
        print(f"  warning: printed-text protection unavailable: {e}", file=sys.stderr)
        return np.zeros_like(gray, np.uint8)
    out = np.zeros_like(gray, np.uint8)
    for line in lines:
        if float(line.get("conf", 0)) < .80:
            continue
        pts = np.asarray(line["box"], dtype=np.int32)
        cv2.fillConvexPoly(out, pts, 255)
    # OCR 文字框本來就比字形寬；再膨脹會把緊貼選項的手寫括號、答案
    # 一併吃掉。僅保護 OCR 實際框出的範圍，模型訓練的印刷誤擦門檻仍會
    # 攔住不安全的候選。
    return out


def mask_from_boxes(gray, report, *, min_conf=.93, review_conf=.80):
    """AI boxes -> training/review masks.

    Only high-confidence boxes become pseudo-training pixels.  Mid-confidence boxes
    are exported separately for a quick human review, never silently used as truth.
    """
    h, w = gray.shape
    rw, rh = max(1, int(report.get("width", w))), max(1, int(report.get("height", h)))
    candidate = np.zeros_like(gray, np.uint8)
    review = np.zeros_like(gray, np.uint8)
    used = []
    for b in report.get("handwriting", []):
        confidence = float(b.get("confidence", 0))
        if confidence < review_conf:
            continue
        x = int(round(float(b["x"]) * w / rw)); y = int(round(float(b["y"]) * h / rh))
        bw = int(round(float(b["w"]) * w / rw)); bh = int(round(float(b["h"]) * h / rh))
        # A little expansion catches pencil edges but avoids swallowing neighbouring printed lines.
        x0, y0 = max(0, x - 3), max(0, y - 3)
        x1, y1 = min(w, x + bw + 3), min(h, y + bh + 3)
        if x1 > x0 and y1 > y0:
            target = candidate if confidence >= min_conf else review
            target[y0:y1, x0:x1] = 255
            used.append({"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0,
                         "kind": b.get("kind", "mark"), "confidence": confidence,
                         "tier": "train" if confidence >= min_conf else "review"})
    # 配對資料用的自適應 ink 在非常淡、紙色很白的掃描上會得到空集合；
    # AI 已把範圍縮到小框，故補一個保守的原始灰階門檻，後面仍會排除
    # OCR 高信心印刷內容。
    ink_pixels = ink(gray) | (gray < 215)
    raw = (ink_pixels.astype(np.uint8) * 255) & candidate
    protected = printed_mask(gray)
    raw[protected > 0] = 0
    # Remove isolated scanner specks, but keep thin pencil strokes.
    count, labels, stats, _ = cv2.connectedComponentsWithStats((raw > 0).astype(np.uint8), 8)
    keep = np.zeros(count, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= 3
    train_mask = (keep[labels].astype(np.uint8) * 255)
    # review mask uses the same print protection but does not affect training.
    review_mask = (ink_pixels.astype(np.uint8) * 255) & review
    review_mask[protected > 0] = 0
    return train_mask, review_mask, used


def save_pseudo(data, pdf, page_no, gray, mask, review_mask, boxes):
    pair = safe_name(pdf)
    out = os.path.join(data, "_pseudo", pair)
    os.makedirs(out, exist_ok=True)
    stem = f"p{page_no}"
    cv2.imwrite(os.path.join(out, stem + "_x.png"), gray)
    cv2.imwrite(os.path.join(out, stem + "_m.png"), mask)
    cv2.imwrite(os.path.join(out, stem + "_review.png"), review_mask)
    with open(os.path.join(out, stem + "_ai.json"), "w", encoding="utf-8") as f:
        json.dump({"source": "codex-vision-boxes", "boxes": boxes,
                   "trainBoxes": sum(b["tier"] == "train" for b in boxes),
                   "reviewBoxes": sum(b["tier"] == "review" for b in boxes)}, f, ensure_ascii=False, indent=1)
    manifest_path = os.path.join(data, "_pseudo", "manifest.json")
    manifest = json.load(open(manifest_path, encoding="utf-8")) if os.path.exists(manifest_path) else []
    manifest = [m for m in manifest if not (m.get("pair") == pair and str(m.get("page")) == stem)]
    manifest.append({"pair": pair, "page": stem, "group": group_for(pdf), "source": "codex-vision-boxes"})
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages", help="1-based pages, e.g. 1,3-5")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--data", default=os.path.join(HERE, "data"))
    ap.add_argument("--codex-bin", default=shutil.which("codex") or "codex")
    ap.add_argument("--engine", choices=("codex", "gemini"), default="codex")
    ap.add_argument("--agy-bin", default=shutil.which("agy") or "agy")
    ap.add_argument("--min-confidence", type=float, default=.93,
                    help="達此信心才自動作為訓練標籤")
    ap.add_argument("--review-confidence", type=float, default=.80,
                    help="介於此值和訓練門檻的候選只輸出供人工快速確認")
    ap.add_argument("--preview", help="write original/mask/clean triptychs here")
    a = ap.parse_args()
    doc = pymupdf.open(a.pdf)
    if a.all:
        wanted = list(range(1, len(doc) + 1))
    elif a.pages:
        wanted = []
        for part in a.pages.split(","):
            lo, _, hi = part.strip().partition("-")
            wanted += list(range(int(lo), int(hi or lo) + 1))
    else:
        ap.error("choose --pages or --all")
    if a.preview:
        os.makedirs(a.preview, exist_ok=True)
    for page_no in wanted:
        if not 1 <= page_no <= len(doc):
            raise ValueError(f"page {page_no} outside 1..{len(doc)}")
        print(f"page {page_no}/{len(doc)}: asking vision labeler…", flush=True)
        gray = render(doc[page_no - 1])
        report = codex_boxes(gray, a.codex_bin) if a.engine == "codex" else gemini_boxes(gray, a.agy_bin)
        mask, review_mask, boxes = mask_from_boxes(gray, report, min_conf=a.min_confidence, review_conf=a.review_confidence)
        if not np.any(mask):
            print(f"  warning: page {page_no} has no usable handwriting pixels; excluded from training", flush=True)
        else:
            save_pseudo(a.data, a.pdf, page_no, gray, mask, review_mask, boxes)
        print(f"  {sum(b['tier']=='train' for b in boxes)} train / {sum(b['tier']=='review' for b in boxes)} review boxes, {(mask > 0).mean():.3%} labelled pixels", flush=True)
        if a.preview:
            # Red is what the proposed data will erase; this is the required visual audit artifact.
            clean = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR); clean[mask > 0] = 255
            overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR); overlay[mask > 0] = (40, 40, 230)
            cv2.imwrite(os.path.join(a.preview, f"p{page_no}_original.png"), gray)
            cv2.imwrite(os.path.join(a.preview, f"p{page_no}_mask.png"), mask)
            cv2.imwrite(os.path.join(a.preview, f"p{page_no}_clean.png"), clean)
            cv2.imwrite(os.path.join(a.preview, f"p{page_no}_marked.png"), overlay)
            cv2.imwrite(os.path.join(a.preview, f"p{page_no}_review.png"), review_mask)


if __name__ == "__main__":
    main()
