#!/bin/bash
# 夜間佇列：依序把 tools/night/tasks/*.txt 交給 Codex（一次一個、一般速率），每個做完自動驗收：
# 任務做完一輪就自動再開下一輪（任務內容是看最新錯誤統計找最大宗，所以每輪會修不同的東西）。
# 任務順序看 tools/night/order.txt（每輪重讀）。
# 單元測試全過 + 27 份題庫 Excel 不退步（改到 pyexam 就重跑 27 份 OCR）→ commit＋push；不過就還原。
# 不用 Claude token。到 END_AT（HHMM）或 Codex 額度用完就停。log：tools/night/log.md（不進 git 的結果在 ocr/results/night/）
cd "$(dirname "$0")/../.."
END_AT="${END_AT:-2359}"; P=~/.venvs/zizi-exam/bin/python; R=ocr/results/night; mkdir -p $R
LOG=$R/log.md
log(){ echo "- $(date '+%m/%d %H:%M') $*" | tee -a $LOG; }
measure(){ # $1=輸出檔 $2=bench
  { $P ocr/qbank_eval.py --xlsx --bench "${2:-ocr/results/qbank_bench}" 2>/dev/null | grep -E "^\||Excel 合計"; $P ocr/results/groupeval.py 2>/dev/null | grep "^{"; } > "$1"; }
tests(){ for t in ocr/test_to_qbank_xlsx.py ocr/test_qbank_latex.py ocr/test_to_zizi_options.py ocr/test_to_zizi_headers.py ocr/test_pyexam_header.py; do
    $P -m unittest $t >/dev/null 2>&1 || { echo "$t"; return 1; }; done; }
round=1; stop=""
while [ -z "$stop" ]; do
for base in $(cat tools/night/order.txt); do task=tools/night/tasks/$base.txt; [ -f "$task" ] || continue
  [ "$(date +%H%M)" -ge "$END_AT" ] && { log "到 $END_AT，停止"; stop=1; break; }
  name="$(basename "$task" .txt)_r$round"; [ -f "$R/$name.done" ] && continue
  git pull -q --rebase 2>/dev/null
  measure $R/$name.before
  pre=$(git ls-files --others --exclude-standard ocr | grep -E "test_.*\.py$")
  prepy=$(git ls-files --others --exclude-standard ocr | grep -E "\.py$" | sort)
  log "開始 $name（$(grep 'Excel 合計' $R/$name.before | grep -o '正確率 [0-9.]*%')）"
  ocr/codex_role.sh worker "$(cat "$task")" >/dev/null 2>&1
  lg=$(ls -t ocr/results/codex/*_worker_codex.log | head -1); rep="${lg%.log}.md"
  if grep -q "routing discovery failed\|Reconnecting\|stream error\|connection" "$lg" 2>/dev/null && [ ! -s "$rep" ]; then
    git checkout -- ocr 2>/dev/null; log "Codex 連線失敗（$name），30 分鐘後重試"; sleep 1800; continue
  fi
  if grep -q "usage limit" "$lg" 2>/dev/null; then
    git checkout -- ocr 2>/dev/null
    at=$(grep -o "try again at [0-9:]* [AP]M" "$lg" | tail -1 | sed 's/try again at //')
    wake=$(date -j -f "%I:%M %p" "$at" "+%H%M" 2>/dev/null || echo "")
    if [ -z "$wake" ] || [ "$wake" -ge "$END_AT" ]; then log "Codex 額度用完（$at 恢復），已超過 $END_AT，停止"; stop=1; break; fi
    log "Codex 額度用完，等到 $at 恢復再繼續"
    until [ "$(date +%H%M)" -ge "$wake" ]; do sleep 60; done; sleep 120
    continue                                  # 這個任務沒做成，不標 done，下一輪會再做
  fi
  newpy=$(comm -13 <(echo "$prepy") <(git ls-files --others --exclude-standard ocr | grep -E "\.py$" | sort) | grep -v "test_")
  changed=$(echo $(git diff --name-only -- ocr) $newpy); newtests=$(comm -13 <(echo "$pre" | sort) <(git ls-files --others --exclude-standard ocr | grep -E "test_.*\.py$" | sort))
  if [ -z "$changed" ]; then log "$name：沒有改動"; touch $R/$name.done; continue; fi
  tok=$(grep -A1 "tokens used" "$lg" | tail -1 | tr -d ','); log "$name Codex 用量 ${tok:-?} tokens"
  tracked=$(git diff --name-only -- ocr)
  if [ -n "$changed" ] && { [ -z "$tracked" ] || { ! echo "$changed" | tr ' ' '\n' | grep -v "^ocr/dehand/" | grep -q . && ! echo "$changed" | grep -q "ocr/dehand/apply.py"; }; }; then
    # 只改去筆跡的訓練程式／新工具（不動正式流程 apply.py）：驗收＝能編譯、單元測試過、改到訓練就小量訓練跑得完（Excel 不受影響）
    rm -rf /tmp/night_smoke; smoke=true
    echo "$changed" | grep -q finetune_jw_mask && smoke="$P ocr/dehand/finetune_jw_mask.py --out /tmp/night_smoke --limit 1 --epochs 1 --steps 1"
    if $P -m py_compile $changed && { [ -z "$newtests" ] || $P -m unittest $newtests >/dev/null 2>&1; } && $smoke >/tmp/night_smoke.log 2>&1; then
      git add $changed $newtests; git commit -q -m "夜間 Codex：$name（訓練程式，smoke test 通過）

$(tail -12 "$rep" 2>/dev/null)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" && git push -q 2>/dev/null; log "✅ $name 已提交（smoke test 通過）"
    else git checkout -- $(git diff --name-only -- ocr) 2>/dev/null; rm -f $newpy $newtests; log "❌ $name 還原：smoke test 失敗"; fi
    touch $R/$name.done; sleep "${COOLDOWN:-900}"; continue
  fi
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
    git checkout -- $(git diff --name-only -- ocr) 2>/dev/null; rm -f $newpy $newtests; log "❌ $name 還原：$verdict"
  fi
  touch $R/$name.done
  sleep "${COOLDOWN:-900}"          # 任務之間休息（預設 15 分鐘），把 Codex 用量攤平
done
[ -z "$stop" ] && { tools/night/plan_new.sh "$round"; log "第 $round 輪做完，開始第 $((round + 1)) 輪"; round=$((round + 1)); }
done
log "佇列結束"
