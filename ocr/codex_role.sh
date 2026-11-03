#!/bin/bash
# 讓 Codex 扮演 .claude/agents/ 裡的角色跑一次，報告寫到 ocr/results/codex/（gitignore，含考卷內容）。
# Claude 只讀最後的短報告，省 Claude 額度；Codex 用 ChatGPT 訂閱額度做跑測試、讀長輸出這種粗活。
#
#   ocr/codex_role.sh tester     "驗證去筆跡：DEHAND=0 vs 有模型，全部 testset"
#   ocr/codex_role.sh supervisor "審查 HEAD~3..HEAD 的改動能不能推"
#   ocr/codex_role.sh worker     "依 ocr-supervisor 報告修好 ocr/to_zizi_word.py 的問題"   ← 會改檔，不會 commit
#
# 換成 Gemini（Antigravity CLI agy）：AI=gemini ocr/codex_role.sh tester "…"（預設模型 gemini-4-argon-high，可用 GEMINI_MODEL 換）
#   適合：看圖（原稿 vs 輸出、介面截圖）、第二意見審查、讀長文件。Codex 額度用完時也可以頂上。
# tester／supervisor：唯讀沙箱（tester 需要寫快取，所以給 workspace-write，但角色規則禁止改程式）
# AI 與它跑的測試一律 nice 15（低優先）：使用者在網站轉檔永遠優先，不會被測試拖慢。
# 結束時印出報告路徑；報告最後一段是給人看的結論。
set -euo pipefail
ROLE="${1:?用法：ocr/codex_role.sh tester|supervisor|worker \"任務\"}"
TASK="${2:?缺任務說明}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CODEX="${CODEX_BIN:-/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex}"
OUT="$ROOT/ocr/results/codex"
mkdir -p "$OUT"
STAMP="$(date +%m%d-%H%M%S)-$$"
AI="${AI:-codex}"
REPORT="$OUT/${STAMP}_${ROLE}_${AI}.md"

case "$ROLE" in
  tester)     SPEC="$ROOT/.claude/agents/ocr-tester.md";     SANDBOX=workspace-write ;;
  supervisor) SPEC="$ROOT/.claude/agents/ocr-supervisor.md"; SANDBOX=read-only ;;
  worker)     SPEC="";                                       SANDBOX=workspace-write ;;
  *) echo "未知角色：$ROLE（tester｜supervisor｜worker）" >&2; exit 2 ;;
esac

if [ -n "$SPEC" ]; then
  # 去掉 YAML 開頭，只留角色說明
  ROLE_TEXT="$(awk 'BEGIN{n=0} /^---$/{n++; next} n>=2' "$SPEC")"
else
  ROLE_TEXT="你是 OCR 專案的工程師。先讀 AGENTS.md 與 docs/OCR交接_Codex.md。只改任務需要的檔案，改完在本機跑相關測試。
你的沙箱不能寫 .git：不要 commit／push，改完列出改了哪些檔案、跑了什麼測試、結果如何，由 Claude 審查後提交。"
fi

PROMPT="$ROLE_TEXT

## 這次的任務
$TASK

## 交付
最後輸出一段「報告」，照角色說明的報告格式，20 行以內，繁體中文。"

echo "$AI（$ROLE）開始：$(date '+%H:%M:%S') → $REPORT"
LOG="${REPORT%.md}.log"
if [ "$AI" = "gemini" ]; then
  AGY="${AGY_BIN:-$HOME/.local/bin/agy}"
  MODE_ARGS=(--dangerously-skip-permissions)
  [ "$SANDBOX" = "read-only" ] && MODE_ARGS+=(--sandbox)
  (cd "$ROOT" && nice -n 15 "$AGY" -p "$PROMPT" --model "${GEMINI_MODEL:-gemini-4-argon-high}" "${MODE_ARGS[@]}" < /dev/null > "$REPORT" 2> "$LOG") \
    || echo "（Gemini 結束碼 $?，細節看 $(basename "$LOG")）"
else
  nice -n 15 "$CODEX" exec -s "$SANDBOX" -C "$ROOT" --skip-git-repo-check -o "$REPORT" "$PROMPT" < /dev/null > "$LOG" 2>&1 \
    || echo "（Codex 結束碼 $?，細節看 $(basename "$LOG")）"
fi
echo "完成：$(date '+%H:%M:%S')"
echo "REPORT=$REPORT"
