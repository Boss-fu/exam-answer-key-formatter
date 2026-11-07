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
    expected = q['x'].shape
    if cleaned.shape[:2] != expected or q['hand'].shape != expected or q['c'].shape != expected:
        raise ValueError('x、hand、c 標註與清理輸出的尺寸必須完全一致')
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


def mask_metrics(q: dict, hit: np.ndarray) -> dict:
    """量候選遮罩本身，定義與 ``finetune_jw_mask.score()`` 的前兩項相同。

    ``score()`` 問的是「模型是否命中 hand 標註」，不是底下仍有印刷筆畫時
    最終畫面是否已變白。因此這組數字不能用來宣稱實際清除效果，但可直接
    定位模型、閾值、膨脹與保護規則造成的差異。
    """
    expected = q['x'].shape
    if hit.shape != expected or q['hand'].shape != expected or q['c'].shape != expected:
        raise ValueError('x、hand、c 標註與候選遮罩的尺寸必須完全一致')
    hand = q['hand'].astype(bool)
    printed = ink(q['c']).astype(bool)
    hit = hit.astype(bool)
    return {
        'hand_hit': int((hit & hand).sum()),
        'hand_total': int(hand.sum()),
        'print_hit': int((hit & printed).sum()),
        'print_total': int(printed.sum()),
    }


def merge_metrics(total: dict, one: dict) -> None:
    for key, value in one.items():
        total[key] = total.get(key, 0) + value


def ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _erase_mask(source: np.ndarray, hit: np.ndarray, tone_paper=None) -> np.ndarray:
    """將某階段遮罩套回實際管線輸入，供所有階段使用相同指標。"""
    out = source.copy()
    out[hit] = 255
    return tone_paper(out) if tone_paper is not None else out


def stage_outputs(source: np.ndarray, dpi: int, detected_boxes=None) -> tuple[dict[str, np.ndarray], list[tuple[int, int, int, int]]]:
    """重現 hand 模式各步的輸出，讓評測可指出哪一步改變了遮罩。

    注意機率是在模型原生格點比較門檻，再最近鄰映回頁面；這與訓練的
    ``score()``（模型格點的 ``p > threshold``）可直接比較，不會把 resize
    的插值誤認成模型表現。
    """
    import apply
    from ppocr_engine import det_boxes

    gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
    # _img2img_erased 同時建立 LAST_HAND；其固定寬 1240 是實際 hand 管線的模型輸入。
    apply._img2img_erased(gray, dpi)
    if apply.LAST_HAND is None:
        raise RuntimeError('候選模型沒有輸出筆跡機率通道')
    h, w = gray.shape
    base_th = float(os.environ.get('DEHAND_HAND_TH', '.5'))
    thresholds = np.full((h, w), base_th, np.float32)
    # 必須用色筆處理後、真正交給 erase 的影像找圖框；先前用 q['c'] 是與流程不同的輸入。
    figures = figure_candidates(source, dpi=dpi)
    margin = int(.04 * w)
    figure_th = float(os.environ.get('DEHAND_HAND_FIG_TH', '.5'))
    for x0, y0, x1, y1 in figures:
        thresholds[max(0, y0 - margin):y1 + margin, max(0, x0 - margin):x1 + margin] = figure_th

    uniform = apply._hand_hit_at_page_size(apply.LAST_HAND, np.full((h, w), base_th, np.float32), (h, w))
    hit = apply._hand_hit_at_page_size(apply.LAST_HAND, thresholds, (h, w))
    stages = {'門檻': uniform, '圖框高門檻': hit.copy()}
    k = int(os.environ.get('DEHAND_HAND_DILATE', '5'))
    hit = cv2.dilate(hit.astype(np.uint8), np.ones((k, k), np.uint8)) > 0
    stages['膨脹'] = hit.copy()
    boxes = detected_boxes if detected_boxes is not None else det_boxes(source)
    if os.environ.get('DEHAND_PROTECT_QNUM', '1') != '0':
        hit[apply._qnum_protect_mask(source, detected_boxes=boxes, erased_mask=hit.astype(np.uint8)) > 0] = False
    stages['題號保護'] = hit.copy()
    if os.environ.get('DEHAND_PROTECT_PRINT', '1') != '0':
        hit[apply._printed_ink_protect_mask(source, hit.astype(np.uint8), detected_boxes=boxes) > 0] = False
    stages['同列印刷保護'] = hit.copy()
    return stages, figures


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
    parser.add_argument('--start', type=int, default=0, help='跳過前 N 張驗證頁；搭配 --limit 分批量測')
    parser.add_argument('--dpi', type=int, default=150)
    parser.add_argument('--img2img', help='指定 DEHAND_IMG2IMG 候選 ONNX')
    parser.add_argument('--mode', choices=('img2img', 'model', 'rule', 'both'))
    parser.add_argument('--hand-th', type=float)
    parser.add_argument('--hand-fig-th', type=float)
    parser.add_argument('--hand-dilate', type=int)
    parser.add_argument('--stages', action='store_true', help='列出色筆處理到打印版色階每階段的累計指標')
    parser.add_argument('--out', help='另存 JSON 結果的路徑；未指定時輸出到 stdout')
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error('--limit 必須大於 0')
    if args.start < 0:
        parser.error('--start 不可小於 0')
    configure(args)
    # 設定環境後才載入，確保 apply 的首次模型 session 使用指定候選模型。
    import apply
    from ppocr_engine import det_boxes

    held = {os.path.basename(os.path.dirname(p)) for p in open(os.path.join(HERE, 'bench_jw_pages.txt'), encoding='utf8').read().split()}
    pages = load(args.std, held, os.path.join(HERE, 'data', 'std_qa.csv'))
    with open(args.r8_json, encoding='utf8') as handle:
        validation_exams = set(json.load(handle)['validation_exams'])
    validation = [(p, q) for p, q in ((p, read(p)) for p in pages if p['exam'] in validation_exams) if q is not None][:40]
    validation = validation[args.start:]
    if args.limit:
        validation = validation[:args.limit]
    if not validation:
        raise RuntimeError('沒有可評測的 r8 驗證頁')

    total: dict[str, int] = {}
    stage_totals: dict[str, dict[str, int]] = {}
    stage_mask_totals: dict[str, dict[str, int]] = {}
    input_shapes: set[tuple[int, int]] = set()
    model_shapes: set[tuple[int, int]] = set()
    for index, (page, q) in enumerate(validation, start=1):
        source = cv2.cvtColor(q['x'], cv2.COLOR_GRAY2BGR)
        h, w = q['x'].shape
        input_shapes.add((h, w))
        model_shapes.add((max(16, round(h * 1240 / w)), 1240))
        color_cleaned = remove_color_ink(source, restore_text=True)[0]
        # 只偵測一次，所有保護與最終輸出共用相同 OCR 框，避免量到非決定性的重跑差異。
        boxes_for_protection = det_boxes(color_cleaned)
        if args.stages:
            stages, boxes = stage_outputs(color_cleaned, args.dpi, boxes_for_protection)
            staged_images = {'色筆處理': color_cleaned}
            staged_images.update({name: _erase_mask(color_cleaned, hit) for name, hit in stages.items()})
            staged_images['打印版色階'] = _erase_mask(color_cleaned, stages['同列印刷保護'], apply._tone_paper_only)
            for name, image in staged_images.items():
                target = stage_totals.setdefault(name, {})
                merge_metrics(target, page_metrics(q, image, boxes))
            # 色筆處理沒有模型候選遮罩；其餘階段同時列 score() 同義的命中率。
            for name, hit in stages.items():
                target = stage_mask_totals.setdefault(name, {})
                merge_metrics(target, mask_metrics(q, hit))
            cleaned = staged_images['打印版色階']
        else:
            cleaned, _ = apply.erase(color_cleaned, dpi=args.dpi, detected_boxes=boxes_for_protection)
            # 圖框必須以實際 erase 的輸入取樣；不是乾淨標註 q['c']。
            boxes = figure_candidates(color_cleaned, dpi=args.dpi)
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
        'geometry': {
            'pipeline_dpi': args.dpi,
            'annotation_shape_verified': True,
            'input_shapes_hw': sorted(input_shapes),
            'model_probability_shapes_hw': sorted(model_shapes),
            'probability_resize': '模型固定寬1240；門檻在模型格點後以最近鄰映回原圖；膨脹與保護在原圖座標',
        },
    }
    if args.stages:
        result['stages'] = {
            name: {
                # 實際輸出定義：標註位置在最後影像已不再被 ink() 視為墨跡。
                'handwriting_removal_rate': ratio(values['hand_removed'], values['hand_total']),
                'print_accidental_erasure_rate': ratio(values['print_erased'], values['print_total']),
                'figure_accidental_erasure_rate': ratio(values['figure_erased'], values['figure_total']),
                'pixels': values,
            }
            for name, values in stage_totals.items()
        }
        result['mask_stages'] = {
            name: {
                # 與 score() 的 got/fp 同義；不把白化結果或色階處理混進來。
                'handwriting_hit_rate': ratio(values['hand_hit'], values['hand_total']),
                'print_false_positive_rate': ratio(values['print_hit'], values['print_total']),
                'pixels': values,
            }
            for name, values in stage_mask_totals.items()
        }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, 'w', encoding='utf8') as handle:
            handle.write(text + '\n')
    print(text)


if __name__ == '__main__':
    main()
