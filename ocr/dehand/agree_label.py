#!/usr/bin/env python3
"""以 v10 與 r8 一致的筆跡機率建立低風險偽標註（全程本機推論）。

輸出相容於 ``finetune_jw_mask.load_pseudo``：每頁有 ``pN_x.png``、
``pN_m.png`` 與 manifest。另有 ``pN_w.png``，255 是可監督像素、0 是
兩模型不一致而應忽略的像素，供日後支援逐像素權重的訓練器使用。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
import pymupdf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from bench_vs_jw import JW
from pairs_build import ink

TILE, OVERLAP = 512, 48
HAND_THRESHOLD, PRINT_THRESHOLD = .80, .05


def safe_name(path: Path) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', '_', path.stem)[:60]


def starts(size: int) -> list[int]:
    positions = list(range(0, max(size - TILE, 0) + 1, TILE - OVERLAP)) or [0]
    return positions if positions[-1] == size - TILE else positions + [size - TILE]


def _blend_probability(gray: np.ndarray, predict_tile) -> np.ndarray:
    """以和 apply.py 相同的重疊 tile 合併第 1 通道筆跡機率。"""
    height, width = gray.shape
    x = np.repeat((gray.astype(np.float32) / 255)[None], 3, axis=0)
    padded_h, padded_w = max(height, TILE), max(width, TILE)
    x = np.pad(x, ((0, 0), (0, padded_h - height), (0, padded_w - width)), mode='edge')
    yy, xx = np.mgrid[0:TILE, 0:TILE]
    weight = np.minimum(np.minimum(yy + 1, TILE - yy), np.minimum(xx + 1, TILE - xx))
    weight = np.minimum(weight, OVERLAP).astype(np.float32)
    total = np.zeros((padded_h, padded_w), np.float32)
    weights = np.zeros((padded_h, padded_w), np.float32)
    for y0 in starts(padded_h):
        for x0 in starts(padded_w):
            prob = predict_tile(x[:, y0:y0 + TILE, x0:x0 + TILE])
            total[y0:y0 + TILE, x0:x0 + TILE] += prob * weight
            weights[y0:y0 + TILE, x0:x0 + TILE] += weight
    return (total / weights)[:height, :width]


def hand_probability(session: ort.InferenceSession, gray: np.ndarray) -> np.ndarray:
    input_name = session.get_inputs()[0].name
    def predict(tile):
        logits = session.run(None, {input_name: np.ascontiguousarray(tile[None])})[0][0, 1]
        return 1 / (1 + np.exp(-np.clip(logits, -30, 30)))
    return _blend_probability(gray, predict)


def checkpoint_probability(path: str, gray: np.ndarray) -> np.ndarray:
    """直接用 r8 checkpoint，方便尚未匯出 ONNX 的訓練輪次。"""
    import torch
    from finetune_jw_mask import model_from
    model = model_from(path, 'cpu').eval()
    def predict(tile):
        with torch.no_grad():
            logits = model(torch.from_numpy(np.ascontiguousarray(tile[None])))[0, 1]
        return torch.sigmoid(logits).cpu().numpy()
    return _blend_probability(gray, predict)


def agreed_labels(gray: np.ndarray, v10: np.ndarray, r8: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """回傳筆跡 mask 與監督權重；未達共識者的權重必須是 0。"""
    if gray.shape != v10.shape or gray.shape != r8.shape:
        raise ValueError('影像與兩個機率圖的尺寸必須相同')
    handwritten = (v10 > HAND_THRESHOLD) & (r8 > HAND_THRESHOLD)
    printed = (v10 < PRINT_THRESHOLD) & (r8 < PRINT_THRESHOLD) & ink(gray)
    mask = (handwritten.astype(np.uint8) * 255)
    weight = ((handwritten | printed).astype(np.uint8) * 255)
    return mask, weight


def load_sessions(r8_model: str) -> tuple[ort.InferenceSession, ort.InferenceSession | None]:
    """載入 v10 與 r8 ONNX；r8 checkpoint 在每頁載入，避免常駐 GPU/記憶體。"""
    if not r8_model.endswith(('.onnx', '.pt')):
        raise ValueError('--r8 必須是 .onnx 或 .pt')
    for path in (JW, r8_model):
        if not os.path.exists(path):
            raise FileNotFoundError(f'找不到模型：{path}')
    return (ort.InferenceSession(JW, providers=['CPUExecutionProvider']),
            ort.InferenceSession(r8_model, providers=['CPUExecutionProvider']) if r8_model.endswith('.onnx') else None)


def write_page(out: Path, pair: str, page: int, source: str, gray: np.ndarray,
               mask: np.ndarray, weight: np.ndarray, manifest: list[dict]) -> None:
    directory = out / pair
    directory.mkdir(parents=True, exist_ok=True)
    stem = f'p{page}'
    cv2.imwrite(str(directory / f'{stem}_x.png'), gray)
    cv2.imwrite(str(directory / f'{stem}_m.png'), mask)
    cv2.imwrite(str(directory / f'{stem}_w.png'), weight)
    manifest[:] = [r for r in manifest if not (r.get('pair') == pair and str(r.get('page')) == stem)]
    manifest.append({'pair': pair, 'page': stem,
                     'group': 'agree:' + hashlib.sha256(source.encode()).hexdigest()[:12],
                     'source_pdf': source, 'hand_ratio': round(float((mask > 0).mean()), 5),
                     'labeled_ratio': round(float((weight > 0).mean()), 5), 'source': 'v10-r8-agree'})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roots', nargs='*', help='額外掃描 PDF 的資料夾')
    parser.add_argument('--out', default='ocr/dehand/data/_agree')
    parser.add_argument('--r8', default='ocr/models/jw/jwmask-r8.onnx')
    parser.add_argument('--limit', type=int, help='最多處理幾頁（smoke test 用）')
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error('--limit 必須大於 0')
    roots = [Path.home() / 'Desktop' / '待匯入', *(Path(p) for p in args.roots)]
    pdfs = sorted({pdf for root in roots if root.is_dir() for pdf in root.rglob('*.pdf')})
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf8')) if manifest_path.exists() else []
    v10_session, r8_session = load_sessions(args.r8)
    count = 0
    for pdf in pdfs:
        pair = safe_name(pdf)
        with pymupdf.open(pdf) as document:
            for page_number, page in enumerate(document, 1):
                if args.limit is not None and count >= args.limit:
                    break
                pix = page.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
                gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w).copy()
                r8_prob = hand_probability(r8_session, gray) if r8_session else checkpoint_probability(args.r8, gray)
                mask, weight = agreed_labels(gray, hand_probability(v10_session, gray), r8_prob)
                write_page(out, pair, page_number, str(pdf), gray, mask, weight, manifest)
                count += 1
                print(f'{pdf.name} p{page_number}: 手寫 {(mask > 0).mean():.3%}，已標 {(weight > 0).mean():.3%}', flush=True)
            if args.limit is not None and count >= args.limit:
                break
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n', encoding='utf8')
    print(f'完成 {count} 頁；{manifest_path}')


if __name__ == '__main__':
    main()
