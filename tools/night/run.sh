#!/bin/bash
# 夜間佇列：依序把 tools/night/tasks/*.txt 交給 Codex（一次一個、一般速率），每個做完自動驗收：
# 任務做完一輪就自動再開下一輪（任務內容是看最新錯誤統計找最大宗，所以每輪會修不同的東西）。
# 單元測試全過 + 27 份題庫 Excel 不退步（改到 pyexam 就重跑 27 份 OCR）→ commit＋push；不過就還原。
# 不用 Claude token。到 END_AT（HHMM）或 Codex 額度用完就停。log：tools/night/log.md（不進 git 的結果在 ocr/results/night/）
cd "$(dirname "$0")/../.."
END_AT="${END_AT:-1300}"; P=~/.venvs/zizi-exam/bin/python; R=ocr/results/night; mkdir -p $R
LOG=$R/log.md
log(){ echo "- $(date '+%m/%d %H:%M') $*" | tee -a $LOG; }
measure(){ # $1=輸出檔 $2=bench
  { $P ocr/qbank_eval.py --xlsx --bench "${2:-ocr/results/qbank_bench}" 2>/dev/null | grep -E "^\||Excel 合計"; $P ocr/results/groupeval.py 2>/dev/null | grep "^{"; } > "$1"; }
tests(){ for t in ocr/test_to_qbank_xlsx.py ocr/test_qbank_latex.py ocr/test_to_zizi_options.py ocr/test_to_zizi_headers.py ocr/test_pyexam_header.py; do
    $P -m unittest $t >/dev/null 2>&1 || { echo "$t"; return 1; }; done; }
round=1; stop=""
while [ -z "$stop" ]; do
for task in tools/night/tasks/*.txt; do
  [ "$(date +%H%M)" -ge "$END_AT" ] && { log "到 $END_AT，停止"; stop=1; break; }
  name="$(basename "$task" .txt)_r$round"; [ -f "$R/$name.done" ] && continue
  git pull -q --rebase 2>/dev/null
  measure $R/$name.before
  pre=$(git ls-files --others --exclude-standard ocr | grep -E "test_.*\.py$")
  log "開始 $name（$(grep 'Excel 合計' $R/$name.before | grep -o '正確率 [0-9.]*%')）"
  out=$(ocr/codex_role.sh worker "$(cat "$task")" 2>&1); rep=$(echo "$out" | grep -o '/[^ ]*worker_codex.md' | tail -1)
  if grep -q "usage limit" "${rep%.md}.log" 2>/dev/null; then log "Codex 額度用完，停止"; git checkout -- ocr 2>/dev/null; stop=1; break; fi
  changed=$(git diff --name-only -- ocr); newtests=$(comm -13 <(echo "$pre" | sort) <(git ls-files --others --exclude-standard ocr | grep -E "test_.*\.py$" | sort))
  if [ -z "$changed" ]; then log "$name：沒有改動"; touch $R/$name.done; continue; fi
  bad=$(tests) ; bench=ocr/results/qbank_bench
  if [ -z "$bad" ] && echo "$changed" | grep -q "ocr/pyexam.py\|ocr/ppocr_engine.py\|ocr/dehand/apply.py"; then
    rm -rf ocr/results/qbank_bench_new; ocr/results/rebench.sh > $R/$name.rebench 2>&1; bench=ocr/results/qbank_bench_new
  fi
  measure $R/$name.after $bench
  verdict=$( [ -n "$bad" ] && echo "FAIL 測試失敗 $bad" || $P tools/night/gate.py $R/$name.before $R/$name.after )
  if [[ "$verdict" == PASS* ]]; then
    git add $changed $newtests
    git commit -q -m "夜間 Codex：$name（$verdict）

$(tail -12 "$rep" 2>/dev/null)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" && git push -q 2>/dev/null
    [ "$bench" != ocr/results/qbank_bench ] && { rm -rf ocr/results/qbank_bench_prev; mv ocr/results/qbank_bench ocr/results/qbank_bench_prev; mv $bench ocr/results/qbank_bench; }
    log "✅ $name 已提交：$verdict"
  else
    git checkout -- $changed; [ -n "$newtests" ] && rm -f $newtests; log "❌ $name 還原：$verdict"
  fi
  touch $R/$name.done
done
[ -z "$stop" ] && { log "第 $round 輪做完，依最新錯誤統計開始第 $((round + 1)) 輪"; round=$((round + 1)); }
done
log "佇列結束"
