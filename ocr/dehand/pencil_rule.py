"""國中鉛筆筆跡的保守規則遮罩（輸入為 300dpi 灰階）。

規則刻意只挑比本頁印刷核心明顯淺的連通塊；PP-OCR 偵測到的文字行
用來保護印刷字。這不是取代模型，而是模型在淡鉛筆卷上的補強。
"""
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from ppocr_engine import det_boxes  # noqa: E402

# 可依卷面調整：印刷核心（灰階 < INK_MAX）的中位亮度加此值才算淡筆跡。
INK_MAX = 200
# 印刷核心只取較深的字幹，避免大量淡鉛筆把頁面基準抬高。
PRINT_CORE_MAX = 150
BRIGHTNESS_OFFSET = 7
MIN_AREA = 6
# 文字行內、靠近深色印刷字的候選塊視為印刷部件而保留。
NEAR_PRINT_GAP = 5
QUESTION_LEFT_PAD = 5


def _rect(box):
    a = np.asarray(box)
    return int(a[:, 0].min()), int(a[:, 1].min()), int(a[:, 0].max()) + 1, int(a[:, 1].max()) + 1


def _inside(r, line):
    x0, y0, x1, y1 = r; a, b, c, d = line
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return a <= cx <= c and b <= cy <= d


def _paren_like(r):
    """括號是窄、高且帶彎曲的淡筆畫；用於放行其相鄰的作答字母。"""
    x0, y0, x1, y1 = r
    return (x1 - x0) <= max(8, (y1 - y0) * .48) and (y1 - y0) >= 8


def detect(gray300):
    """回傳 uint8 0/255 遮罩。呼叫端可直接和模型遮罩聯集。"""
    if gray300.ndim != 2:
        gray300 = cv2.cvtColor(gray300, cv2.COLOR_BGR2GRAY)
    ink = gray300 < INK_MAX
    core = gray300[gray300 < PRINT_CORE_MAX]
    if core.size == 0:
        return np.zeros(gray300.shape, np.uint8)
    threshold = float(np.median(core)) + BRIGHTNESS_OFFSET
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), 8)
    lines = [_rect(b) for b in det_boxes(gray300)]
    candidates = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < MIN_AREA:
            continue
        vals = gray300[lab == i]
        if np.percentile(vals, 20) > threshold:
            candidates.append((i, (x, y, x + w, y + h)))

    # 淡的括號及其間的字母／數字是作答；同行中間的候選塊也一併擦掉。
    answer_ids = set()
    for i, r in candidates:
        # 題號在手寫作答括號後；不要把印刷數字當作作答區的一部分。
        if 160 <= (r[0] + r[2]) / 2 <= min(320, gray300.shape[1] * .12) or not _paren_like(r):
            continue
        for j, q in candidates:
            if abs((q[1] + q[3]) / 2 - (r[1] + r[3]) / 2) <= max(r[3] - r[1], q[3] - q[1]) and abs(q[0] - r[0]) < 80:
                answer_ids.add(j)

    out = np.zeros(gray300.shape, np.uint8)
    for i, r in candidates:
        in_lines = [line for line in lines if _inside(r, line)]
        protected = False
        for line in in_lines:
            x0, y0, x1, y1 = r; lx0, ly0, lx1, ly1 = line
            # 行框左緣的題號（1.~40.）不擦；偵測框常正好從題號起點開始。
            if x0 <= lx0 + QUESTION_LEFT_PAD and (x1 - x0) <= (ly1 - ly0) * 1.1:
                protected = True
                break
            # 字行中的小部件、且緊貼深色印刷，保留；括號作答例外。
            if (y1 - y0) < (ly1 - ly0) * 1.1:
                halo = cv2.dilate((gray300 < threshold).astype(np.uint8), np.ones((NEAR_PRINT_GAP * 2 + 1,) * 2, np.uint8))
                if halo[y0:y1, x0:x1].any():
                    protected = True
                    break
        # 左欄題號前是學生選答「(A~D)」的位置，不能因貼近題號而誤當印刷部件。
        if (r[0] + r[2]) / 2 < 160:
            protected = False
        if i in answer_ids:
            protected = False
        if not protected:
            out[lab == i] = 255
    return out
