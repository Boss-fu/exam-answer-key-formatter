# Mac 測試伺服器設定

目標：Mac 是開發與驗證環境；確認功能、OCR 品質與輸出結果後，才推送至 GitHub `main`，再讓公司 Windows 主機更新為正式運算節點。

## 目前 Mac 測試網址

```text
本機：http://localhost:8787
Tailscale：http://100.98.1.41:8787
```

Tailscale 裝置必須登入相同 tailnet。請勿把 8787 做公開 Port Forwarding 或 Tailscale Funnel。

## 一鍵啟動

在 Finder 對 `local-app/start-mac.command` 按右鍵→打開；或在終端機執行：

```zsh
cd "<repo 根目錄>"
./local-app/start-mac.command
```

首次若 macOS 阻擋執行，執行：

```zsh
chmod +x local-app/start-mac.command
```

啟動終端機必須保持開啟。停止服務按 `Control + C`。

## Mac 目前所需工具

- Node.js：啟動網站。
- Tesseract：含 `chi_tra`、`eng`，供 OCR 預檢。
- Poppler：提供 `pdftoppm`／`pdftotext`，供 PDF 轉圖與文字型 PDF 擷取。
- Ollama（選用）：Qwen3-VL 僅處理低信心單題裁圖；Qwen2.5 僅整理 OCR 文字。

## 測試→正式的發布規則

1. 在 Mac 的 `http://localhost:8787` 測試一份考卷。
2. 驗證題目切分、OCR 覆核、Word/CSV 與詳解結果。
3. 提交並推送通過測試的變更至 GitHub `main`。
4. 公司主機自動快轉更新；公司主機是正式環境，Mac 仍保留作下一輪測試。

不要在公司主機直接修改程式，也不要把未驗證改動直接當成正式流程使用。
