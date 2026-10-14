#!/usr/bin/env python3
"""從 Google Drive 取回最新 Colab 去筆跡模型，不覆寫既有 run。"""
import argparse
import json
from pathlib import Path
import shutil
import sys


HERE = Path(__file__).resolve().parent
DEFAULT_MODELS = Path("/Users/chiafuchang/Library/CloudStorage/GoogleDrive-sandy880507@gmail.com/My Drive/OCR訓練資料/models")


def model_dirs(root: Path):
    required = ("dehand.onnx", "dehand.json")
    # Colab 用 YYYYMMDD-HHMMSS 建版本目錄，名稱即為可重現的訓練時間；不要受後續下載、
    # 同步等操作改動 mtime 影響而取錯版本。
    return sorted((p for p in root.iterdir() if p.is_dir() and all((p / f).is_file() for f in required)),
                  key=lambda p: p.name)


def pct(value):
    return "無資料" if value is None else f"{float(value):.2%}"


def main():
    parser = argparse.ArgumentParser(description="取回最新 Colab 訓練的去筆跡模型")
    parser.add_argument("--models", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--runs", type=Path, default=HERE / "runs")
    args = parser.parse_args()
    root = args.models.expanduser()
    if not root.is_dir():
        parser.error(f"找不到 Drive models 資料夾：{root}")
    candidates = model_dirs(root)
    if not candidates:
        parser.error("models/ 找不到同時有 dehand.onnx 與 dehand.json 的版本")
    source = candidates[-1]
    destination = args.runs / f"colab-{source.name}"
    if destination.exists():
        parser.error(f"目的地已存在，為免覆寫而停止：{destination}")
    destination.mkdir(parents=True)
    for path in source.iterdir():
        if path.is_file() and (path.name in {"dehand.onnx", "dehand.onnx.data", "dehand.json"}):
            shutil.copy2(path, destination / path.name)
    try:
        info = json.loads((destination / "dehand.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        shutil.rmtree(destination)
        sys.exit(f"dehand.json 無法讀取，已刪除不完整目的地：{exc}")
    print(f"已取回：{destination}")
    print(f"召回：{pct(info.get('recall'))}；印刷誤擦：{pct(info.get('print_erase'))}")
    print(f"請 Claude 用 DEHAND_MODEL_DIR='{destination}' 跑驗收。")


if __name__ == "__main__":
    main()
