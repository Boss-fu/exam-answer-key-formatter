茲茲 OCR 主機（桌面版 2.0）
=========================

使用：雙擊 ZiziOCR.exe。沒有黑視窗，右下角（可能在「^」隱藏圖示裡）會出現紅色「茲」小圖示。
第一次會問「這台主機要在網站上顯示什麼名稱」（例如：劍潭；可留空用電腦名稱）。

- 這台電腦自己用：匯卷頁「OCR 運算主機」選「本機」。
- 讓其他老師也能用：這台電腦安裝 Tailscale 並登入，程式會自動把 https 入口開好，
  並自動登錄到網站的主機選單，不需要傳網址給任何人。
  （Tailscale 後台要開啟 MagicDNS 與 HTTPS 憑證；要分享給別人請在 Machines 頁用 Share。）
- AI 分類：用這台電腦上已登入的 agy／claude／codex（至少一個）。沒有的話 OCR 仍可用。

右下角圖示：雙擊看狀態；右鍵可複製網址、設定顯示名稱、開機自動啟動、開啟紀錄資料夾、重新啟動、
結束（要確認才會停止）。

進階設定：%LOCALAPPDATA%\ZiziOCR\config.json
  {"displayName":"劍潭","ocrConcurrency":2,"aiConcurrency":3}
  ocrConcurrency＝OCR／切圖同時處理幾份（預設 1，建議最多 2）；aiConcurrency＝AI 分類同時幾題。
紀錄：%LOCALAPPDATA%\ZiziOCR\ziziocr.log
