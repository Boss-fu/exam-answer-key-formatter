#!/bin/bash
# 一輪做完：讓 Codex（唯讀）看最新數據想 2 個新任務，寫進 tasks/ 與 order.txt 最前面。不用 Claude token。
cd "$(dirname "$0")/../.."
R=ocr/results/night; P=~/.venvs/zizi-exam/bin/python
ctx="$(tail -40 $R/log.md)
$($P ocr/results/losscat.py 2>/dev/null | grep -v '^ok' | cut -c1-200)
$($P ocr/results/groupeval.py 2>/dev/null | grep '^{')
$(cat $R/pipeline_r8.json 2>/dev/null | grep rate)"
out=$(ocr/codex_role.sh supervisor "你是 OCR 專案的規劃者。目標：題庫 Excel 同題號正確率（ocr/qbank_eval.py --xlsx）≥97%、去筆跡整條流程（ocr/dehand/pipeline_eval.py）筆跡清除率 ≥97% 且印刷／圖誤擦低、OCR 4 頁 ≤25 秒。下面是夜間佇列 log 與最新錯誤統計。請提出 2 個『還沒做過、預期收益最大、可在單一 Codex 任務內完成並可自動驗收』的新任務，每個用一行輸出，格式嚴格為：TASK|英數短名|任務說明（含要改哪些檔、怎麼驗證、規則：執行期不可用生成式 AI、不要 commit）。不要輸出其他 TASK 行。
$ctx" 2>&1)
rep=$(ls -t ocr/results/codex/*_supervisor_codex.md 2>/dev/null | head -1)
n=0
grep "^TASK|" "$rep" 2>/dev/null | head -2 | while IFS='|' read -r _ short desc; do
  short=$(echo "$short" | tr -cd 'A-Za-z0-9_' | cut -c1-30); [ -z "$short" ] && continue
  f="tools/night/tasks/9${1}_${short}.txt"; echo "$desc" > "$f"
  { echo "9${1}_${short}"; cat tools/night/order.txt; } > /tmp/order.new && mv /tmp/order.new tools/night/order.txt
  echo "- $(date '+%m/%d %H:%M') 新任務 9${1}_${short}：$(echo "$desc" | cut -c1-80)" >> $R/log.md
done
