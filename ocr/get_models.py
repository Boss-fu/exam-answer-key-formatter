#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""準備我們自己 OCR 系統要用的「公開」模型到 ocr/models/（不依賴任何他人 vendored 的檔）。

原則（對應使用者要求：不全用別人的工具，參考並做自己的）：
  * OCR 偵測/辨識模型＝PaddleOCR 公開釋出（Apache-2.0）。任何人都能下載，不是誰的私有資產。
      - rapidocr_onnxruntime 內建一套 PP-OCRv4（det/rec/cls），離線即可用，當作保底預設。
      - 想升級到 PP-OCRv5（準確度更好），把官方釋出的 det/rec onnx 放進 ocr/models/det.onnx、rec.onnx，
        字典放 ocr/models/ppocrv5_dict.txt（官方來源見下方 SOURCES）。本腳本偵測到就會沿用、不覆蓋。
  * 去手寫模型＝**不下載、也不沿用任何別人的 dehand.onnx**。這塊我們走自己的路：
    用我們自己的乾淨版庫存，照 ocr/dehand_train.py（階段 3 會建）訓練「我們自己的」模型。
  * 公式模型（階段 2）＝MFD(Pix2Text, AGPL-3.0，僅限內部) + RapidLaTeXOCR(Apache-2.0)，官方來源見下。

用法：
    python3 ocr/get_models.py            # 準備 OCR 模型（沒有就用 rapidocr 內建的 PP-OCRv4）
    python3 ocr/get_models.py --check    # 只檢查目前 ocr/models/ 有哪些、分別是什麼來源
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODELS = HERE / "models"

# 官方公開來源（放在這裡當文件；要升級 v5 時照這些手動下載即可，不經任何第三方的私有副本）
SOURCES = {
    "PP-OCRv5 det/rec/dict": "PaddleOCR 官方（github.com/PaddlePaddle/PaddleOCR）與 RapidAI/RapidOCR 的公開 onnx 釋出",
    "MFD 公式偵測": "huggingface.co/breezedeus/pix2text-mfd（mfd-*.onnx，AGPL-3.0，僅限內部）",
    "RapidLaTeXOCR 公式辨識": "pip install rapid-latex-ocr（首次執行自動下載 encoder/decoder/image_resizer + tokenizer，Apache-2.0）",
}


def rapidocr_bundled():
    """回傳 rapidocr_onnxruntime 內建模型資料夾（PP-OCRv4，公開）。"""
    try:
        import rapidocr_onnxruntime as r
    except ImportError:
        return None
    d = Path(r.__file__).resolve().parent / "models"
    return d if d.exists() else None


def ensure_ocr_models():
    """確保 ocr/models/ 有可用的 det/rec/dict。已存在就不動；否則用 rapidocr 內建 PP-OCRv4 補上。"""
    MODELS.mkdir(parents=True, exist_ok=True)
    det, rec = MODELS / "det.onnx", MODELS / "rec.onnx"
    have_det, have_rec = det.exists(), rec.exists()
    if have_det and have_rec:
        print("OCR 模型已就緒（沿用 ocr/models/ 內現有 det/rec，視為你放的公開模型）")
        return True
    bundled = rapidocr_bundled()
    if not bundled:
        print("找不到 rapidocr 內建模型；請先 pip install -r ocr/requirements.txt，"
              "或手動把官方 PP-OCRv5 det/rec 放進 ocr/models/。", file=sys.stderr)
        return False
    picks = {
        "det.onnx": next(iter(sorted(bundled.glob("*det*.onnx"))), None),
        "rec.onnx": next(iter(sorted(bundled.glob("*rec*.onnx"))), None),
    }
    for name, src in picks.items():
        dst = MODELS / name
        if not dst.exists() and src:
            shutil.copy2(src, dst)
            print(f"補上公開模型 {name} ← rapidocr 內建 {src.name}")
    # rapidocr 內建模型自帶字典；若我們沒有 v5 字典就不強求，引擎用內建即可
    ok = (MODELS / "det.onnx").exists() and (MODELS / "rec.onnx").exists()
    print("OCR 模型" + ("就緒（PP-OCRv4 公開內建；要更準可換官方 PP-OCRv5）" if ok else "準備失敗"))
    return ok


def check():
    print("ocr/models/ 現況：")
    if not MODELS.exists():
        print("  （還沒有 models 資料夾，先跑 python3 ocr/get_models.py）")
    else:
        for p in sorted(MODELS.iterdir()):
            print(f"  {p.name:22} {p.stat().st_size // 1024}KB")
    print("\n去手寫：不使用任何他人的 dehand.onnx；改由 ocr/dehand_train.py 以我們自己的資料自訓（階段 3）。")
    print("\n公開模型官方來源：")
    for k, v in SOURCES.items():
        print(f"  - {k}：{v}")


def main():
    ap = argparse.ArgumentParser(description="準備我們 OCR 系統要用的公開模型")
    ap.add_argument("--check", action="store_true", help="只檢查現況與來源，不下載/不複製")
    args = ap.parse_args()
    if args.check:
        check()
        return 0
    ok = ensure_ocr_models()
    check()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
