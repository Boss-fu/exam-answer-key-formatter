#!/bin/zsh
# 茲茲 OCR 系統：Mac 測試伺服器啟動器。
# 雙擊後保持此終端機開啟；Tailscale 裝置可連線至 Mac 的 8787 埠。
set -e
cd "$(dirname "$0")/.."

if launchctl print "gui/$(id -u)/com.tzutzu.ocr" >/dev/null 2>&1; then
  echo "茲茲 OCR 背景服務已註冊。操作頁面：https://tzutzu-ocr.vercel.app"
  open "https://tzutzu-ocr.vercel.app"
  exit 0
fi

if ! command -v node >/dev/null 2>&1; then
  echo "找不到 Node.js。請先安裝 Node LTS。"
  read "?按 Enter 結束"
  exit 1
fi

if [[ ! -d node_modules ]]; then
  echo "首次啟動：正在安裝網站相依套件…"
  npm install
fi

export HOST=0.0.0.0
export PORT=8787
echo "茲茲 OCR Mac 測試伺服器啟動中…"
echo "本機： http://localhost:8787"
echo "Tailscale：請以本機 Tailscale IP 加上 :8787 連線"
npm start
