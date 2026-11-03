#!/usr/bin/env python3
"""量測乾淨卷整條流程的去筆跡效果，不只量模型輸出的筆跡機率。

資料與 ``finetune_jw_mask.py`` 相同：r8 JSON 指定的 validation_exams，
並沿用它所用的 ``load`` / ``read`` 標註。每頁實際走 clean_pdf 的色筆
處理後再走 ``dehand.apply.erase``；執行期不呼叫生成式 AI。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OCR_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, OCR_ROOT)

from finetune_jw import load, read
from pairs_build import ink
from pyexam import figure_candidates, remove_color_ink


def _remaining_ink(cleaned: np.ndarray) -> np.ndarray:
    """把清理輸出轉成與標註一致的「仍是墨跡」遮罩。"""
    return ink(cv2.cvtColor(cleaned, cv2.COLOR_BGR2GRAY))


def page_metrics(q: dict, cleaned: np.ndarray, figure_boxes: list[tuple[int, int, int, int]]) -> dict:
    """由已知筆跡／印刷標註和流程輸出算一頁的分子、分母。"""
    if cleaned.shape[:2] != q['x'].shape:
        raise ValueError('清理輸出尺寸與標註不一致')
    remaining = _remaining_ink(cleaned)
    hand = q['hand'].astype(bool)
    printed = ink(q['c']).astype(bool)
    figures = np.zeros_like(printed, dtype=bool)
    h, w = printed.shape
    for x0, y0, x1, y1 in figure_boxes:
        figures[max(0, y0):min(h, y1), max(0, x0):min(w, x1)] = True
    return {
        'hand_removed': int((hand & ~remaining).sum()),
        'hand_total': int(hand.sum()),
        'print_erased': int((printed & ~remaining).sum()),
        'print_total': int(printed.sum()),
        'figure_erased': int((printed & figures & ~remaining).sum()),
        'figure_total': int((printed & figures).sum()),
    }


def merge_metrics(total: dict, one: dict) -> None:
    for key, value in one.items():
        total[key] = total.get(key, 0) + value


def ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def configure(args: argparse.Namespace) -> None:
    """對齊 clean_pdf 預設值，也讓候選模型／門檻可由 CLI 覆寫。"""
    os.environ.setdefault('DEHAND_I2I_OUT', 'hand')
    if args.img2img:
        os.environ['DEHAND_IMG2IMG'] = args.img2img
    elif 'DEHAND_IMG2IMG' not in os.environ:
        r8 = os.path.join(OCR_ROOT, 'models', 'jw', 'jwmask-r8.onnx')
        if os.path.exists(r8):
            os.environ['DEHAND_IMG2IMG'] = r8
            os.environ.setdefault('DEHAND_HAND_TH', '.10')
    for argument, variable in (
        ('mode', 'DEHAND_MODE'), ('hand_th', 'DEHAND_HAND_TH'),
        ('hand_fig_th', 'DEHAND_HAND_FIG_TH'), ('hand_dilate', 'DEHAND_HAND_DILATE'),
    ):
        value = getattr(args, argument)
        if value is not None:
            os.environ[variable] = str(value)
    os.environ.setdefault('DEHAND_HAND_TH', '.35')
    os.environ.setdefault('DEHAND_HAND_DILATE', '3')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--std', default='ocr/dehand/data/std')
    parser.add_argument('--r8-json', default='ocr/models/jw/jwmask-r8.json')
    parser.add_argument('--limit', type=int, help='只跑前 N 張驗證頁，供 smoke test 使用')
    parser.add_argument('--dpi', type=int, default=150)
    parser.add_argument('--img2img', help='指定 DEHAND_IMG2IMG 候選 ONNX')
    parser.add_argument('--mode', choices=('img2img', 'model', 'rule', 'both'))
    parser.add_argument('--hand-th', type=float)
    parser.add_argument('--hand-fig-th', type=float)
    parser.add_argument('--hand-dilate', type=int)
    parser.add_argument('--out', help='另存 JSON 結果的路徑；未指定時輸出到 stdout')
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error('--limit 必須大於 0')
    configure(args)
    # 設定環境後才載入，確保 apply 的首次模型 session 使用指定候選模型。
    from apply import erase

    held = {os.path.basename(os.path.dirname(p)) for p in open(os.path.join(HERE, 'bench_jw_pages.txt'), encoding='utf8').read().split()}
    pages = load(args.std, held, os.path.join(HERE, 'data', 'std_qa.csv'))
    with open(args.r8_json, encoding='utf8') as handle:
        validation_exams = set(json.load(handle)['validation_exams'])
    validation = [(p, q) for p, q in ((p, read(p)) for p in pages if p['exam'] in validation_exams) if q is not None][:40]
    if args.limit:
        validation = validation[:args.limit]
    if not validation:
        raise RuntimeError('沒有可評測的 r8 驗證頁')

    total: dict[str, int] = {}
    for index, (page, q) in enumerate(validation, start=1):
        source = cv2.cvtColor(q['x'], cv2.COLOR_GRAY2BGR)
        color_cleaned = remove_color_ink(source, restore_text=True)[0]
        cleaned, _ = erase(color_cleaned, dpi=args.dpi)
        boxes = figure_candidates(cv2.cvtColor(q['c'], cv2.COLOR_GRAY2BGR), dpi=args.dpi)
        merge_metrics(total, page_metrics(q, cleaned, boxes))
        print(f'[{index}/{len(validation)}] {page["exam"]}', file=sys.stderr, flush=True)

    result = {
        'pages': len(validation),
        'validation_exams': sorted(validation_exams),
        'handwriting_removal_rate': ratio(total['hand_removed'], total['hand_total']),
        'print_accidental_erasure_rate': ratio(total['print_erased'], total['print_total']),
        'figure_accidental_erasure_rate': ratio(total['figure_erased'], total['figure_total']),
        'pixels': total,
        'settings': {key: os.environ.get(key) for key in (
            'DEHAND_I2I_OUT', 'DEHAND_IMG2IMG', 'DEHAND_MODE', 'DEHAND_HAND_TH',
            'DEHAND_HAND_FIG_TH', 'DEHAND_HAND_DILATE',
        )},
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, 'w', encoding='utf8') as handle:
            handle.write(text + '\n')
    print(text)


if __name__ == '__main__':
    main()
