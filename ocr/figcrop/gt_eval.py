#!/usr/bin/env python3
"""以使用者手動截圖評測 figcrop（完全本機、300 dpi）。

手動 PNG 沒有座標；本程式用 SIFT 樣板特徵在原始頁面定位。資料來源可直接是
figcrop_gt 的工作副本，也可用 --source-dir 從「PDF + *_圖表素材.zip」建立副本。
ZIP 的中文檔名依 Info-ZIP 的 cp437→utf-8 慣例還原，不使用系統 unzip。
"""
from __future__ import annotations

import argparse, json, shutil, sys, zipfile
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import pymupdf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ocr"))
try:
    from . import crop, evalcache
except ImportError:
    import crop, evalcache


def zip_name(name: str) -> str:
    """還原未設 UTF-8 flag 的中文 ZIP 名稱。"""
    try:
        return name.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def prepare(source: Path, output: Path) -> list[Path]:
    """把資料夾中的 PDF + 素材 ZIP 展成可重跑的工作副本。"""
    made = []
    for archive in sorted(source.glob("*_圖表素材.zip")):
        stem = archive.name.removesuffix("_圖表素材.zip")
        pdfs = list(source.glob(stem + ".pdf")) or list(source.glob(stem + "*.pdf"))
        if not pdfs:
            print(f"略過（找不到 PDF）：{archive.name}", file=sys.stderr); continue
        dest = output / stem
        manual = dest / "manual"
        manual.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdfs[0], dest / "exam.pdf")
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                name = zip_name(info.filename)
                if name.endswith("/"):
                    continue
                # 只允許素材檔名，避免 ZIP 路徑穿越。
                target = manual / Path(name).name
                with zf.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        made.append(dest)
    return made


def imread_gray(path):
    """cv2.imread 在 Windows 碰到中文路徑會回 None（手動圖檔名都是中文），改成先讀檔再解碼。"""
    try:
        data = np.fromfile(str(path), np.uint8)
    except OSError:                 # 和 cv2.imread 一樣回 None：缺檔只記「樣板定位失敗」，不中斷整輪
        return None
    return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE) if data.size else None


def render(page):
    pix = page.get_pixmap(dpi=300, alpha=False)
    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def locate(template: np.ndarray, page_gray: np.ndarray, page_features):
    """SIFT 樣板定位；手動截圖通常是 150dpi、頁面則固定 300dpi。"""
    sift = cv2.SIFT_create()
    keys, desc = sift.detectAndCompute(template, None)
    pkeys, pdesc = page_features
    if desc is None or pdesc is None:
        return None
    pairs = cv2.BFMatcher().knnMatch(desc, pdesc, k=2)
    good = [a for pair in pairs if len(pair) == 2 for a, b in [pair] if a.distance < .70 * b.distance]
    if len(good) < 5:
        return None
    a = np.float32([keys[m.queryIdx].pt for m in good])
    b = np.float32([pkeys[m.trainIdx].pt for m in good])
    h, mask = cv2.findHomography(a, b, cv2.RANSAC, 5.0)
    if h is None or int(mask.sum()) < 5:
        return None
    th, tw = template.shape[:2]
    corners = cv2.perspectiveTransform(np.float32([[[0, 0], [tw, 0], [tw, th], [0, th]]]), h)[0]
    x0, y0 = corners.min(axis=0); x1, y1 = corners.max(axis=0)
    # 對掃描圖而言，透視極端變形幾乎一定是錯配。
    # 手動截圖可能相對頁面轉了 90°（直放的圖），長寬要對調再檢查。
    fits = lambda w, h: .45 < (x1-x0) / max(1, w) < 3.5 and .45 < (y1-y0) / max(1, h) < 3.5
    if not (fits(tw, th) or fits(th, tw)):
        return None
    return tuple(map(int, (x0, y0, x1, y1))), int(mask.sum()), len(good)


USE_CACHE = False
_LOCATE_SIG = None


def find_template(templ, gray, page_features):
    """手動圖在頁面上的位置只跟兩張圖和 locate 本身有關，和切圖規則無關，所以評測時可以快取（占評測時間約 3/4）。"""
    global _LOCATE_SIG
    if not USE_CACHE:
        return locate(templ, gray, page_features())
    if _LOCATE_SIG is None:
        import hashlib, inspect
        _LOCATE_SIG = hashlib.blake2b(inspect.getsource(locate).encode(), digest_size=8).hexdigest() + "|" + cv2.__version__
    return evalcache.cached("locate", (templ, gray), _LOCATE_SIG, lambda: locate(templ, gray, page_features()))


def iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1]); x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1-x0) * max(0, y1-y0)
    return inter / max(1, (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter)


def write_review(path, manual, auto, label):
    a = cv2.cvtColor(manual, cv2.COLOR_GRAY2BGR)
    b = auto if auto is not None else np.full_like(a, 240)
    h = max(a.shape[0], b.shape[0]); scale = min(1.0, 700 / max(a.shape[1], b.shape[1]))
    a = cv2.copyMakeBorder(a, 28, h-a.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(255,255,255))
    b = cv2.copyMakeBorder(b, 28, h-b.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(255,255,255))
    cv2.putText(a, "manual", (4, 19), cv2.FONT_HERSHEY_SIMPLEX, .55, (0,0,180), 1)
    cv2.putText(b, label, (4, 19), cv2.FONT_HERSHEY_SIMPLEX, .55, (180,0,0), 1)
    joined = cv2.hconcat([a, np.full((h+28, 12, 3), 220, np.uint8), b])
    if scale < 1: joined = cv2.resize(joined, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    crop.pyexam._imwrite(str(path), joined)     # Windows 中文路徑


def exams(gt_dir: Path):
    if (gt_dir / "manual" / "manifest.json").exists(): return [gt_dir]
    return [p for p in sorted(gt_dir.iterdir()) if (p / "manual" / "manifest.json").exists()]


def run_exam(work: Path, review_dir: Path, only_pages=None):
    manifest = json.loads((work / "manual" / "manifest.json").read_text(encoding="utf-8"))
    # 同張題組圖在 manifest 會重複出現，評測時只保留一筆。
    assets = {(x["page"], x["file"]): x for x in manifest["assets"]}
    by_page = defaultdict(list)
    for asset in assets.values(): by_page[asset["page"]].append(asset)
    doc = pymupdf.open(work / "exam.pdf"); rows = []
    for pn, wanted in sorted(by_page.items()):
        if only_pages and pn not in only_pages: continue
        image = render(doc[pn-1]); gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        feats = []      # 整頁 SIFT 只在樣板定位沒命中快取時才算
        page_features = lambda: feats[0] if feats else feats.append(cv2.SIFT_create().detectAndCompute(gray, None)) or feats[0]
        items = crop.crop_items(image); boxes = [it["box"] for it in items]
        used = set()
        for asset in wanted:
            filename = asset["file"]; templ = imread_gray(work / "manual" / filename)
            found = find_template(templ, gray, page_features) if templ is not None else None
            if not found:
                rows.append({"exam": work.name, "page": pn, "file": filename, "qno": asset["primary_question"], "error": "樣板定位失敗"}); continue
            gt, inliers, matches = found
            # 使用者決定：手動框不框圖說都合格，所以含／不含圖說取高分；嚴格分另外記在 iou_strict。
            scores = [(max(iou(gt, b), iou(gt, items[j]["box_nocap"])), j, b) for j, b in enumerate(boxes)]
            score, j, auto_box = max(scores, default=(0, None, None))
            strict = max((iou(gt, b) for b in boxes), default=0)
            hit = score >= .8
            if hit: used.add(j)
            qno = items[j]["qno"] if auto_box else None
            auto_img = image[auto_box[1]:auto_box[3], auto_box[0]:auto_box[2]] if auto_box else None
            write_review(review_dir / f"{work.name}_{filename}", templ, auto_img, f"auto IoU={score:.3f} q={qno}")
            rows.append({"exam": work.name, "page": pn, "file": filename, "qno": asset["primary_question"], "gt": gt, "auto": auto_box, "iou": score, "iou_strict": strict, "hit": hit, "auto_qno": qno, "qno_ok": qno == asset["primary_question"] if hit else False, "offset": [gt[0]-auto_box[0], gt[1]-auto_box[1], auto_box[2]-gt[2], auto_box[3]-gt[3]] if auto_box else None, "sift": [inliers, matches]})
        for j, b in enumerate(boxes):
            if j not in used and not any(iou(b, r["gt"]) >= .1 for r in rows if r.get("page") == pn and r.get("gt")):
                rows.append({"exam": work.name, "page": pn, "extra": True, "auto": b})
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--gt-dir", default=str(ROOT / "ocr/results/figcrop_gt")); ap.add_argument("--source-dir")
    ap.add_argument("--out", default=str(ROOT / "ocr/results/figcrop_gt")); ap.add_argument("--pages", help="僅跑指定頁，如 1,3-5")
    ap.add_argument("--no-cache", action="store_true", help="不用評測快取（OCR／版面模型／備援候選／樣板定位全部重算）")
    a = ap.parse_args(); out = Path(a.out); gt = Path(a.gt_dir)
    global USE_CACHE
    if not a.no_cache: evalcache.enable(crop); USE_CACHE = True
    if a.source_dir: prepare(Path(a.source_dir), out); gt = out
    wanted = crop.page_numbers(a.pages, 9999) if a.pages else None
    review_dir = out / "review"; review_dir.mkdir(parents=True, exist_ok=True)
    all_rows = []
    for work in exams(gt): all_rows.extend(run_exam(work, review_dir, wanted))
    hits = [r for r in all_rows if "hit" in r]; matched = [r for r in hits if r["hit"]]; extras = sum(1 for r in all_rows if r.get("extra")); located = len(hits)
    offsets = np.array([r["offset"] for r in matched if r.get("offset")], float)
    summary = {"manual": located, "recall_iou80": round(len(matched)/located, 4) if located else None, "recall_strict": round(sum(r["iou_strict"] >= .8 for r in hits)/located, 4) if located else None, "mean_iou": round(float(np.mean([r["iou"] for r in hits])), 4) if hits else None, "extras": extras, "qno_accuracy": round(sum(r["qno_ok"] for r in matched)/len(matched), 4) if matched else None, "mean_offset_ltrb_px": [round(x,1) for x in offsets.mean(axis=0)] if len(offsets) else None}
    (out / "gt_eval.json").write_text(json.dumps({"summary": summary, "rows": all_rows}, ensure_ascii=False, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)), encoding="utf-8")   # numpy 整數
    print("手動圖  召回@.8  平均IoU  多框  題號一致  平均偏移(L/T/R/B px)")
    print(f"{located:>5}  {summary['recall_iou80']!s:>8}  {summary['mean_iou']!s:>7}  {extras:>4}  {summary['qno_accuracy']!s:>8}  {summary['mean_offset_ltrb_px']}")
    if not a.no_cache: print(evalcache.summary())


if __name__ == "__main__": main()
