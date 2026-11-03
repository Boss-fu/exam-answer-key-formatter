#!/bin/bash
# 先等目前 27 份重跑（驗收 Codex 的 pyexam 圖表文字改動）結束、判定提交或還原，再跑夜間佇列
cd "$(dirname "$0")/../.."
P=~/.venvs/zizi-exam/bin/python; R=ocr/results/night; mkdir -p $R
while pgrep -f "ocr/results/rebench.sh" >/dev/null; do sleep 60; done
{ $P ocr/qbank_eval.py --xlsx 2>/dev/null | grep -E "^\||Excel 合計"; $P ocr/results/groupeval.py 2>/dev/null | grep "^{"; } > $R/00.before
{ $P ocr/qbank_eval.py --xlsx --bench ocr/results/qbank_bench_new 2>/dev/null | grep -E "^\||Excel 合計"; $P ocr/results/groupeval.py 2>/dev/null | grep "^{"; } > $R/00.after
v=$($P tools/night/gate.py $R/00.before $R/00.after)
if [[ "$v" == PASS* ]] && git diff --quiet -- ocr/to_qbank_xlsx.py ocr/to_zizi_word.py; then
  git add ocr/pyexam.py ocr/test_figure_text.py ocr/test_pyexam_table_rules.py 2>/dev/null
  git commit -q -m "OCR：確認的圖框／表格內文字改輸出〔含圖〕、轉正抽樣再精簡（$v）

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" && git push -q
  rm -rf ocr/results/qbank_bench_prev; mv ocr/results/qbank_bench ocr/results/qbank_bench_prev; mv ocr/results/qbank_bench_new ocr/results/qbank_bench
  echo "- $(date '+%m/%d %H:%M') ✅ 00 pyexam 圖表文字：$v" >> $R/log.md
else
  git checkout -- ocr/pyexam.py; echo "- $(date '+%m/%d %H:%M') ❌ 00 pyexam 圖表文字還原：$v" >> $R/log.md
fi
exec tools/night/run.sh
