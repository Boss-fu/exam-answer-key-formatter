#!/usr/bin/env bash
# OCR 開發環境一鍵準備（給接手的 Codex／新電腦用）。在 repo 根目錄執行：bash ocr/setup_env.sh
# 1) Python 3.11 虛擬環境 ~/.venvs/zizi-exam（rapidocr_onnxruntime 1.x 不支援 3.13+）
# 2) PP-OCRv5 官方模型 → ocr/models/（mobile 版預設；server 版放 ocr/models/server/ 供比較）
# 3) 測試卷：ocr/testset/（開發用）、ocr/holdout/（最後驗收用）——含學生作答，gitignore，不進 git
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${OCR_VENV:-$HOME/.venvs/zizi-exam}"
SRC="${OCR_DATA_SRC:-$HOME/exam-answer-key-formatter}"   # 已經準備好資料的那份 checkout（同一台 Mac）

# ---- 1. Python ----
if [ ! -x "$VENV/bin/python" ]; then
  PY311="$(command -v python3.11 || echo "$HOME/.pyenv/versions/3.11.9/bin/python")"
  "$PY311" -m venv "$VENV"
  grep -v rapid-latex-ocr ocr/requirements.txt > /tmp/ocr_req.txt
  "$VENV/bin/pip" install -q -r /tmp/ocr_req.txt python-docx rapidfuzz
fi
"$VENV/bin/python" -c "import rapidocr_onnxruntime, cv2, pymupdf, docx, rapidfuzz, opencc; print('Python 套件 OK')"

# ---- 2. 模型 ----
mkdir -p ocr/models/server
B=https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5
get() { [ -s "$2" ] && return 0; if [ -s "$SRC/$2" ]; then cp "$SRC/$2" "$2"; else curl -sSfL -o "$2" "$B/$1"; fi; }
get det/ch_PP-OCRv5_det_mobile.onnx ocr/models/det.onnx
get rec/ch_PP-OCRv5_rec_mobile.onnx ocr/models/rec.onnx
get det/ch_PP-OCRv5_det_server.onnx ocr/models/server/det.onnx
get rec/ch_PP-OCRv5_rec_server.onnx ocr/models/server/rec.onnx
cp -n ocr/models/ppocrv5_dict.txt ocr/models/server/ 2>/dev/null || true
echo "模型 OK：$(ls ocr/models)"

# ---- 3. 測試卷 ----
for d in testset holdout; do
  mkdir -p "ocr/$d"
  if [ "$(ls "ocr/$d" | wc -l)" -lt 2 ] && [ -d "$SRC/ocr/$d" ] && [ "$SRC" != "$PWD" ]; then
    cp -n "$SRC/ocr/$d/"* "ocr/$d/"
  fi
  echo "ocr/$d：$(ls "ocr/$d" | grep -c '\.pdf$') 份 PDF、$(ls "ocr/$d" | grep -c '\.gt\.docx$') 份正解"
done
echo "完成。先跑：$VENV/bin/python ocr/qcount.py <tag>（要先有 eval_gt 的快取），或 $VENV/bin/python ocr/eval_gt.py --tag v9 --fresh"
