#!/bin/bash
# 使用者在網站轉檔（exam_to_word）時，暫停背景的評測／訓練／資料產生；轉完自動恢復。
# 用法：nohup tools/yield_to_user.sh > /tmp/yield.log 2>&1 &
BG="ocr/eval_gt.py|import train|train_img2img|backfill_clean|dehand/synth.py|dehand/pseudo_label.py"
paused=0
while true; do
  if pgrep -f "^[^ ]*/bin/python[0-9.]* [^ ]*ocr/exam_to_word.py" >/dev/null; then
    [ $paused = 0 ] && { pkill -STOP -f "$BG"; paused=1; echo "$(date +%T) 使用者轉檔中，暫停背景工作"; }
  else
    [ $paused = 1 ] && { pkill -CONT -f "$BG"; paused=0; echo "$(date +%T) 恢復背景工作"; }
  fi
  sleep 2
done
