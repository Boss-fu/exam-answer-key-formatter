#!/bin/bash
# 詳解本機應用 — Mac 一鍵啟動。放在 local-app/ 底下，雙擊即可（或終端機執行）。
cd "$(dirname "$0")/.." || exit 1

# 已有背景服務時不再啟動第二份搶占 8787。
if launchctl print "gui/$(id -u)/com.tzutzu.ocr" >/dev/null 2>&1; then
  echo "茲茲 OCR 背景服務已註冊。操作頁面：https://tzutzu-ocr.vercel.app"
  open "https://tzutzu-ocr.vercel.app"
  exit 0
fi

if ! command -v node >/dev/null 2>&1; then
  echo "[錯誤] 找不到 Node.js。請先安裝 LTS 版：https://nodejs.org/"
  echo "（或在終端機執行： brew install node ）"
  read -r -p "按 Enter 關閉…" _; exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "[提醒] 找不到 Claude Code CLI（claude）。產詳解/一鍵包需要它。"
  echo "安裝： npm install -g @anthropic-ai/claude-code   然後執行一次 claude 登入。"
  echo "（先繼續啟動，純截圖工具不需要 claude。）"
fi

if [ ! -d node_modules ]; then
  echo "首次啟動，安裝相依套件（docx / mammoth / pdf-parse）…"
  npm install || { echo "npm install 失敗"; read -r -p "按 Enter 關閉…" _; exit 1; }
fi

echo ""
echo "啟動中…  本機開：  http://localhost:8787"
echo "其他裝置（Tailscale）：  http://<這台Mac的Tailscale名稱>:8787"
echo "共用操作頁面： https://tzutzu-ocr.vercel.app （選擇我的 Mac）"
echo "關閉此視窗即停止服務。"
echo ""
exec npm start
