#!/bin/bash
# 夜間狀態（給排程檢查用）：佇列是否活著、最近 log。佇列沒在跑就重啟（只會有一條）。
cd "$(dirname "$0")/../.."
if pgrep -f "tools/night/run.sh" >/dev/null; then echo "佇列：運作中"; else
  nohup tools/night/run.sh > ocr/results/night/run.out 2>&1 &
  echo "- $(date '+%m/%d %H:%M') 佇列不在，已重啟" >> ocr/results/night/log.md; echo "佇列：已重啟"; fi
pgrep -f "codex exec -c service_tier" >/dev/null && echo "Codex：工作中" || echo "Codex：待命"
git ls-files --others --exclude-standard ocr | grep -E "\.py$" | grep -v "promote.py\|report_vs_zizi.py\|test_pyexam_columns.py" | sed 's/^/未提交新檔：/'
tail -8 ocr/results/night/log.md | cut -c1-160
