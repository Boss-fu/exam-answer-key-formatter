#!/usr/bin/env python3
"""評測用磁碟快取：只給 gt_eval.py／wild_run.py 用，正式切圖（crop.py --json、server）不會載入這支。

只快取「純模型輸出」，規則每次重算，所以調 crop.py／refine.py 的規則不會讀到舊結果：
  ppocr_engine.ocr_lines   OCR 文字行（text_lines、page_rotation 都靠它，兩者的規則照跑）
  crop.layout_raw          版面模型原始框（detect 的門檻／類別過濾照跑）
  pyexam.figure_candidates 連通塊備援候選
鍵＝頁面像素雜湊＋參數＋相關程式／模型版本。快取在 ocr/results/figcrop_cache（不進 git）。
改了 ppocr_engine.py、pyexam.py 或換模型，鍵會跟著變；要整個重來就刪資料夾或加 --no-cache。
"""
from __future__ import annotations

import functools
import hashlib
import os
import pickle
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "ocr/results/figcrop_cache"
STATS = {"hit": 0, "miss": 0}


def _file_sig(path) -> str:
    try:
        return hashlib.blake2b(Path(path).read_bytes(), digest_size=8).hexdigest()
    except OSError:
        return "missing"


def _key(kind: str, img, extra: str) -> str:
    """img 可以是一張陣列或多張（tuple）；每張都把形狀、型別、像素一起雜湊。"""
    h = hashlib.blake2b(digest_size=16)
    h.update(f"{kind}|{extra}|".encode())
    for a in (img if isinstance(img, tuple) else (img,)):
        a = np.ascontiguousarray(a)
        h.update(f"{a.shape}|{a.dtype}|".encode())
        h.update(memoryview(a).cast("B"))
    return h.hexdigest()


def cached(kind, img, extra, compute):
    """通用入口：gt_eval 的樣板定位也用這個（評測專用，和切圖結果無關）。"""
    return _cached(kind, img, extra, compute)


def _cached(kind, img, extra, compute):
    key = _key(kind, img, extra)
    path = DIR / kind / key[:2] / (key + ".pkl")
    if path.exists():
        try:
            value = pickle.loads(path.read_bytes())
            STATS["hit"] += 1
            return value
        except Exception:
            pass                        # 壞檔就重算覆蓋
    value = compute()
    STATS["miss"] += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_bytes(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL))
    os.replace(tmp, path)
    return value


def enable(crop) -> None:
    """把 crop 用到的三個模型呼叫換成有快取的版本（只影響這個行程）。重複呼叫無害。"""
    ppocr_engine, pyexam = crop.ppocr_engine, crop.pyexam
    if getattr(crop.layout_raw, "_evalcache", False):
        return
    ocr_dir = Path(ppocr_engine.__file__).resolve().parent
    ocr_sig = "|".join([_file_sig(ppocr_engine.__file__), str(getattr(ppocr_engine, "ENGINE", "")),
                        str(getattr(ppocr_engine, "TEXT_SCORE", "")),
                        _file_sig(ocr_dir / "models/det.onnx"), _file_sig(ocr_dir / "models/rec.onnx")])
    pyexam_sig = _file_sig(pyexam.__file__)
    try:
        from importlib.metadata import version
        layout_sig = version("rapid-layout")
    except Exception:
        layout_sig = "?"

    ocr_lines, layout_raw, figure_candidates = ppocr_engine.ocr_lines, crop.layout_raw, pyexam.figure_candidates

    @functools.wraps(ocr_lines)
    def cached_ocr_lines(img):
        return _cached("ocr", img, ocr_sig, lambda: ocr_lines(img))

    @functools.wraps(layout_raw)
    def cached_layout_raw(img, model):
        return _cached("layout", img, f"{model}|{layout_sig}", lambda: layout_raw(img, model))

    @functools.wraps(figure_candidates)
    def cached_figure_candidates(bgr, dpi=300, merge_gap=60):
        return _cached("figcand", bgr, f"{dpi}|{merge_gap}|{pyexam_sig}",
                       lambda: list(figure_candidates(bgr, dpi=dpi, merge_gap=merge_gap)))

    for f in (cached_ocr_lines, cached_layout_raw, cached_figure_candidates):
        f._evalcache = True
    ppocr_engine.ocr_lines = cached_ocr_lines
    crop.layout_raw = cached_layout_raw
    pyexam.figure_candidates = cached_figure_candidates


def summary() -> str:
    return f"評測快取：命中 {STATS['hit']}、重算 {STATS['miss']}（{DIR}）"
