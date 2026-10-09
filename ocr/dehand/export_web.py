#!/usr/bin/env python3
"""Export the in-repo dehand model as browser-friendly, single-file ONNX.

Run with the OCR virtualenv: ~/.venvs/zizi-exam/bin/python ocr/dehand/export_web.py
The float copy is retained for WebGPU; dynamic INT8 is the WASM fallback.
"""
from __future__ import annotations

import json
from pathlib import Path

import onnx
from onnxruntime.quantization import QuantType, quantize_dynamic

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "ocr" / "models" / "dehand.onnx"
OUT = ROOT / "local-app" / "public" / "models"


def merge(source: Path, target: Path) -> None:
    """Load external tensors, then save every tensor into one .onnx file."""
    model = onnx.load(source, load_external_data=True)
    onnx.save_model(model, target, save_as_external_data=False)


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"model not found: {SOURCE}")
    OUT.mkdir(parents=True, exist_ok=True)
    web = OUT / "dehand-web.onnx"
    int8 = OUT / "dehand-web-int8.onnx"
    merge(SOURCE, web)
    quantize_dynamic(str(web), str(int8), weight_type=QuantType.QInt8)
    # Quantizers can emit external data for very large tensors; canonicalize again.
    merge(int8, int8)
    config = {
        "version": 1,
        "page_width": 1240,
        "dpi": 150,
        "tile": 512,
        "overlap": 48,
        "input": "x",
        "output": "logits",
        "pencil_rule": {"print_core_max": 150, "brightness_offset": 7, "ink_max": 200},
        "models": {"webgpu": web.name, "wasm_int8": int8.name},
    }
    (OUT / "dehand-web.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for file in (web, int8):
        size = file.stat().st_size / 1024 / 1024
        print(f"{file.relative_to(ROOT)}: {size:.2f} MB" + (" (over 25 MB; retained)" if size > 25 else ""))


if __name__ == "__main__":
    main()
