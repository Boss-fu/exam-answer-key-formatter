#!/usr/bin/env python3
"""img2img 去筆跡速度與輸出相容性基準。

只讀 bench_jw_pages.txt 的第一張頁面，等比放到 300dpi，連跑三次 erase。
另以修改前的 CPU／逐 tile 演算法重算一次「模型擦除遮罩」；這個比較刻意
關閉題號保護，避免把本次刻意縮小的 OCR 保護範圍誤算成模型輸出差異。
"""
import os
import sys
import time

import cv2
import numpy as np
import onnxruntime as ort

HERE = os.path.dirname(os.path.abspath(__file__))
OCR_ROOT = os.path.dirname(HERE)
sys.path.insert(0, OCR_ROOT)
from dehand import apply
from ppocr_engine import det_boxes


def legacy_i2i_mask(gray):
    """套用修改前固定 CPU、逐 512 tile 的模型遮罩路徑。"""
    path = os.environ.get("DEHAND_IMG2IMG") or os.path.join(OCR_ROOT, "models", "jw", "handwriting-eraser-v10.onnx")
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    H, W = gray.shape
    small = cv2.resize(gray, (1240, max(16, round(H * 1240 / W))), interpolation=cv2.INTER_AREA)
    h, w = small.shape; tile, overlap = 512, 48
    x = np.repeat((small.astype(np.float32) / 255)[None], 3, 0)
    hp, wp = max(h, tile), max(w, tile)
    x = np.pad(x, ((0, 0), (0, hp - h), (0, wp - w)), mode="edge")
    yy, xx = np.mgrid[0:tile, 0:tile]
    wt = np.minimum(np.minimum(np.minimum(yy + 1, tile - yy), np.minimum(xx + 1, tile - xx)), overlap).astype(np.float32)
    starts = lambda n: (lambda s: s if s[-1] == n - tile else s + [n - tile])(list(range(0, n - tile + 1, tile - overlap)) or [0])
    acc = np.zeros((hp, wp), np.float32); ws = np.zeros((hp, wp), np.float32)
    name = sess.get_inputs()[0].name
    for y0 in starts(hp):
        for x0 in starts(wp):
            out = sess.run(None, {name: np.ascontiguousarray(x[None, :, y0:y0 + tile, x0:x0 + tile])})[0][0, 0]
            acc[y0:y0 + tile, x0:x0 + tile] += out * wt
            ws[y0:y0 + tile, x0:x0 + tile] += wt
    clean = np.clip(((acc / ws)[:h, :w] - .25) / .63, 0, 1)
    threshold = float(os.environ.get("DEHAND_I2I_TH", ".75"))
    erased = ((small < 200) & (clean > threshold)).astype(np.uint8)
    return cv2.resize(erased, (W, H), interpolation=cv2.INTER_NEAREST)


def main():
    pages = os.path.join(HERE, "bench_jw_pages.txt")
    src = open(pages, encoding="utf-8").read().split()[0]
    image = cv2.imread(src)
    if image is None:
        raise SystemExit(f"無法讀取：{src}")
    # bench 的標準圖是 150dpi；放大為目標管線的 300dpi。
    image = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    # pyexam 的 fix_orientation 已取得這批框；模擬實際呼叫端重用，而不把它算進 erase。
    boxes = det_boxes(image)
    old = {k: os.environ.get(k) for k in ("DEHAND_MODE", "DEHAND_I2I_OUT", "DEHAND_PROTECT_QNUM")}
    os.environ.update(DEHAND_MODE="img2img", DEHAND_I2I_OUT="mask", DEHAND_PROTECT_QNUM="1")
    try:
        for run in range(1, 4):
            t0 = time.perf_counter()
            _, ratio = apply.erase(image, dpi=300, detected_boxes=boxes)
            total = time.perf_counter() - t0
            print(f"第 {run} 次：{total:.3f}s（模型 {apply.LAST_TIMINGS['model']:.3f}s，題號保護 {apply.LAST_TIMINGS['qnum']:.3f}s，擦除 {ratio:.2%}）")

        # 比的是修改前後 img2img 模型輸出的二值擦除遮罩，而非題號保護策略。
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        reference = legacy_i2i_mask(gray)
        current, _ = apply._img2img_erased(gray, 300)
        diff = float(np.count_nonzero(reference != current)) / reference.size
        print(f"遮罩差異：{diff:.4%}（門檻 < 0.1%） {'通過' if diff < .001 else '不通過'}")
        if diff >= .001:
            raise SystemExit(1)
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    main()
