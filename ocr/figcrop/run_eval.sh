#!/bin/bash
# 跑一輪切圖評測，和基準比較：ocr/figcrop/run_eval.sh <標籤>
# 結果在 ocr/results/figcrop_runs/<標籤>（不進 git）。通過閘門不會自動提交，也不會自動更新基準。
set -u
cd "$(dirname "$0")/../.."
OUT="ocr/results/figcrop_runs/$1"; BASE=ocr/results/figcrop_gt/baseline.json
P="${OCR_PY:-$HOME/.venvs/zizi-exam/bin/python}"
mkdir -p "$OUT"
"$P" -m unittest ocr.figcrop.test_refine 2>&1 | tail -1
"$P" ocr/figcrop/sync_gt.py ${FIGCROP_SRC:+"$FIGCROP_SRC"} > "$OUT/sync.log" 2>&1
"$P" ocr/figcrop/gt_eval.py --gt-dir ocr/results/figcrop_gt --out "$OUT" > "$OUT/eval.log" 2>&1 || { tail -5 "$OUT/eval.log"; exit 1; }
if [ -f "$BASE" ]; then
  "$P" tools/night/gate_figcrop.py "$BASE" "$OUT/gt_eval.json"
  "$P" ocr/figcrop/compare_eval.py "$BASE" "$OUT/gt_eval.json"
else
  echo "還沒有基準；確認分數合理後：cp $OUT/gt_eval.json $BASE"
  "$P" -c "import json,sys; print(json.load(open(sys.argv[1]))['summary'])" "$OUT/gt_eval.json"
fi
