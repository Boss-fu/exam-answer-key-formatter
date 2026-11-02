#!/usr/bin/env python3
"""低優先權檢查 std 去筆跡訓練資料，必要時產生不覆寫原檔的修補版。"""
import argparse
import csv
import os
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

try:
    from .std_format import iter_pages
except ImportError:  # 直接執行 python ocr/dehand/qa_std.py
    from std_format import iter_pages


def ink(image):
    """保守地把深色掃描墨跡轉成二值圖。"""
    return (image < min(220, int(cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0] + 35))).astype(np.uint8)


def residual_shift(source, target):
    """回傳把 source 平移到 target 的 (x, y)，使用模糊印刷墨跡以抗筆跡雜訊。"""
    a = cv2.GaussianBlur(ink(source).astype(np.float32), (0, 0), 2)
    b = cv2.GaussianBlur(ink(target).astype(np.float32), (0, 0), 2)
    # 邊界常有裝訂陰影，會令 phase correlation 失真。
    h, w = a.shape; margin = max(12, min(h, w) // 40)
    shift, response = cv2.phaseCorrelate(a[margin:h-margin, margin:w-margin], b[margin:h-margin, margin:w-margin])
    return float(shift[0]), float(shift[1]), float(response)


def translate(image, dx, dy):
    return cv2.warpAffine(image, np.float32([[1, 0, dx], [0, 1, dy]]), (image.shape[1], image.shape[0]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=255)


def component_mask(mask, labels, stats, keep):
    out = np.zeros_like(mask)
    for label in keep:
        out[labels == label] = 1
    return out


def printed_residual(in_ink, gt_ink):
    """找 in 有、gt 無且大小/行高像印刷字的連通塊。"""
    near_gt = cv2.dilate(gt_ink, np.ones((7, 7), np.uint8))
    extra = (in_ink & (1 - near_gt)).astype(np.uint8)
    count, labels, stats, cents = cv2.connectedComponentsWithStats(extra, 8)
    gc, _, gs, _ = cv2.connectedComponentsWithStats(gt_ink, 8)
    heights = gs[1:, cv2.CC_STAT_HEIGHT]
    heights = heights[(heights >= 4) & (heights <= 80)]
    typical = float(np.median(heights)) if len(heights) else 12.0
    keep = []
    for label in range(1, count):
        x, y, w, h, area = stats[label]
        if area < 8 or area > 1500 or h < max(3, typical * .45) or h > typical * 2.4:
            continue
        # 筆跡常很長或很細；保留常見字塊和數學符號大小。
        if w > max(typical * 4.5, h * 5):
            continue
        # 同一文字基線附近另有 gt 字，才當成遺漏印刷字。
        band = gt_ink[max(0, y - int(typical)):min(gt_ink.shape[0], y + h + int(typical)), :]
        if band.sum() >= max(8, area // 3):
            keep.append(label)
    return component_mask(extra, labels, stats, keep), extra


def handwriting_score(gt, gt_ink):
    """以遠大於印刷字的連通塊作保守手寫警訊。

    掃描的印刷字也可能灰、粗，因此不能把灰度或筆畫寬度本身當作手寫；
    只計算與頁內常見字高明顯不符的孤立筆劃面積。
    """
    if not gt_ink.any():
        return 0.0
    count, _, stats, _ = cv2.connectedComponentsWithStats(gt_ink, 8)
    heights = stats[1:, cv2.CC_STAT_HEIGHT]
    common = heights[(heights >= 4) & (heights <= 80)]
    typical = float(np.median(common)) if len(common) else 12.0
    suspicious = 0
    for x, y, w, h, area in stats[1:]:
        # 排除表格格線（極長且極細），它不是手寫。
        line = (w > h * 12 and h <= 4) or (h > w * 12 and w <= 4)
        if not line and area >= 20 and h > typical * 3.2 and w < typical * 12:
            suspicious += area
    return float(min(1.0, suspicious / max(1, gt_ink.sum())))


def assess(in_image, gt_image):
    """計算指標；回傳可直接供測試與 CLI 使用的字典。"""
    dx, dy, response = residual_shift(gt_image, in_image)
    aligned_gt = translate(gt_image, dx, dy)
    ii, gi = ink(in_image), ink(aligned_gt)
    near_in = cv2.dilate(ii, np.ones((7, 7), np.uint8))
    missing_mask = (gi & (1 - near_in)).astype(np.uint8)
    missing = float(missing_mask.sum() / max(1, gi.sum()))
    printed_mask, extra = printed_residual(ii, gi)
    printed = float(printed_mask.sum() / max(1, extra.sum())) if extra.any() else 0.0
    hand = handwriting_score(aligned_gt, gi)
    return {"dx": dx, "dy": dy, "shift": float(np.hypot(dx, dy)), "phase_response": response,
            "missing": missing, "printed_residual": printed, "handwriting": hand,
            "aligned_gt": aligned_gt, "printed_mask": printed_mask, "missing_mask": missing_mask}


def repair(in_image, assessment):
    """只在可驗證的微位移與印刷型殘留時修補，原 gt 永不覆寫。"""
    fixed = assessment["aligned_gt"].copy()
    if assessment["shift"] <= 3.05:
        mask = assessment["printed_mask"].astype(bool)
        fixed[mask] = in_image[mask]
    return fixed


def verdict(metrics, fixing=False):
    # 位移可自動微調；大位移/明顯手寫仍交由人工複審。
    bad = metrics["shift"] > 3.05 or metrics["missing"] > .025 or metrics["handwriting"] > .18
    needs_fix = metrics["shift"] > .35 or metrics["printed_residual"] > .015
    return "bad" if bad else ("fixed" if fixing and needs_fix else "good")


def triptych(in_image, gt, metrics):
    diff = np.full((*gt.shape, 3), 255, np.uint8)
    diff[metrics["missing_mask"] > 0] = (0, 0, 255)       # gt 多出的墨：紅
    diff[metrics["printed_mask"] > 0] = (0, 160, 0)       # 疑似漏字：綠
    return np.hstack((cv2.cvtColor(in_image, cv2.COLOR_GRAY2BGR), cv2.cvtColor(gt, cv2.COLOR_GRAY2BGR), diff))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, default=Path(__file__).parent / "data" / "std")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--exam", help="卷名子字串，只檢查該卷")
    ap.add_argument("--fix", action="store_true", help="寫 *_gt_fixed.png，絕不覆蓋 *_gt.png")
    args = ap.parse_args()
    cv2.setNumThreads(1)
    try:
        os.nice(10)  # 低優先權；程式本身沒有 worker / thread pool。
    except PermissionError:
        # 受限沙箱不能調整 priority；正常主機仍會套用，且始終單執行緒。
        pass
    out_csv = args.data.parent / "std_qa.csv"; result_dir = Path(__file__).parents[1] / "results" / "std_qa"
    rows, counts = [], Counter()
    for exam, page, in_path, gt_path, _, _ in iter_pages(args.data):
        if args.exam and args.exam not in exam:
            continue
        source = cv2.imread(str(in_path), cv2.IMREAD_GRAYSCALE); gt = cv2.imread(str(gt_path), cv2.IMREAD_GRAYSCALE)
        if source is None or gt is None or source.shape != gt.shape:
            continue
        metrics = assess(source, gt); state = verdict(metrics, args.fix)
        if args.fix and state == "fixed":
            cv2.imwrite(str(gt_path.with_name(gt_path.stem + "_fixed.png")), repair(source, metrics))
        if state == "bad":
            result_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(result_dir / f"{exam}_{page:03d}.png"), triptych(source, gt, metrics))
        row = {"卷": exam, "頁": page, **{key: round(metrics[key], 5) for key in ("dx", "dy", "shift", "phase_response", "missing", "printed_residual", "handwriting")}, "判定": state}
        rows.append(row); counts[state] += 1
        if args.limit and len(rows) >= args.limit: break
    fields = ["卷", "頁", "dx", "dy", "shift", "phase_response", "missing", "printed_residual", "handwriting", "判定"]
    with out_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    print(f"檢查 {len(rows)} 頁：good={counts['good']} fixed={counts['fixed']} bad={counts['bad']}")
    print(f"CSV：{out_csv}；bad 三聯圖：{result_dir}")


if __name__ == "__main__":
    main()
