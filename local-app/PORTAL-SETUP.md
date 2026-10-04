# 茲茲 OCR：同一頁面，選擇運算主機

正式操作入口：<https://tzutzu-ocr.vercel.app/>（`/raw.html` 亦可）。

Vercel 只部署 `local-app/public/` 的 HTML/JS，沒有 OCR 運算函式，也不儲存考卷。
Mac、Windows 和 Vercel 使用同一份 `index.html`。選單只改變新工作的運算端；
既有工作的影像、覆核及下載，固定使用建立工作時的主機網址。

| 選項 | 私有 HTTPS API | HTTP 服務 |
|---|---|---|
| 我的 Mac | `https://chias-macbook-pro.tail3d3c18.ts.net:8443` | 該 Mac 的 8787 |
| 公司電腦 | `https://ljm2222.tail3d3c18.ts.net:8443` | 該 Windows 的 8787 |

瀏覽器直接連選定主機，不經過另一台。不需要 Mac 開著才能使用公司電腦，反之亦然。
使用端與運算端都要連上允許存取的 Tailscale 網路；Vercel 頁面能開不代表主機已上線。
若 Chrome 詢問「區域網路存取」，需允許才能連私有主機。

## 主機更新

1. 各台更新本專案 `main`，安裝既有 Node 相依套件。
2. 啟動 `local-app/server.js`（Windows 使用 `start.bat`）。
3. 網站開始監聽後，`tailnet-https.js` 嘗試建立專用 Tailscale Serve 8443 入口。
   不使用 Funnel、不更動其他埠、不覆蓋已占用的 8443；設定失敗會留下診斷訊息，HTTP 服務仍可使用。
4. 首次 HTTPS 憑證申請可能較慢，稍後再按網頁「重新檢查連線」。
5. 在 Vercel 選主機，確認顯示主機名稱、Git 版本與 OCR 引擎。

若自動設定失敗，在公司 PowerShell 執行（只需要設定一次）：

```powershell
& "$env:ProgramFiles\Tailscale\tailscale.exe" serve status
& "$env:ProgramFiles\Tailscale\tailscale.exe" serve --bg --https=8443 http://127.0.0.1:8787
```

若 8443 已被其他服務使用，不要覆蓋；先選其他私有 HTTPS 埠，並更新 `public/server-config.js`。
Mac 的對應指令是 `/Applications/Tailscale.app/Contents/MacOS/Tailscale serve ...`。

## 本次 Mac 的背景服務

2026-10-02 已安裝使用者 LaunchAgent `com.tzutzu.ocr`，固定執行
`/Users/chiafuchang/Codex/許力講義詳解/exam-answer-key-formatter/local-app/server.js`。
登入 Mac 後啟動；程序結束會重新啟動，不依賴聊天或終端機保持開啟。
Mac 睡眠、關機或 Tailscale 斷線時，仍無法提供遠端運算。

- 設定：`~/Library/LaunchAgents/com.tzutzu.ocr.plist`
- 日誌：`local-app/output/mac-service.log`、`mac-service-error.log`
- 重啟：`launchctl kickstart -k gui/$(id -u)/com.tzutzu.ocr`
- 停用：`launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.tzutzu.ocr.plist`

2026-10-04 起整個 Codex 資料夾已從 iCloud 同步的 `~/Documents/Codex` 搬到 `~/Codex`（iCloud「最佳化儲存空間」會把檔案換成雲端空殼，背景服務讀不到就當掉）。
舊位置改名為 `~/Documents/Codex_舊位置_已搬走` 備份；不要再從 `~/Documents` 底下啟動服務。服務的 `PY_BIN` 改為 `~/.venvs/zizi-exam/bin/python`（掃描卷轉茲茲 Word 用）。

## 前端發布

```sh
cd local-app/public
vercel link --project tzutzu-ocr --scope boss-fus-projects
vercel deploy --prod --scope boss-fus-projects
```

只從 `public` 發布，禁止把整個 repository、`output`、考卷、模型或登入憑證上傳。
CLI 產生的 `.vercel/` 與 `.env*` 已排除。CLI 部署與 Git push 是兩個動作，
目前未將 Vercel 設為 GitHub 自動部署；修改前端後要重新執行上面的 deploy。

## 安全與版本

- `public/server-config.js` 統一主機 URL、service ID、API 版本與正式入口白名單。
- 主機僅接受確切白名單來源，不再反射任意 Origin；CORS 不是帳號認證，仍須私有網路隔離。
- 新的正式入口須設定 `OCR_ALLOWED_ORIGINS=https://完整網域`（多個以逗號隔開）；不要允許所有 `*.vercel.app`。
- HTTP POST 僅接受 JSON；回應包含必要的 CORS 及下載檔名標頭。
- 不使用公開埠轉發或 Funnel；Tailscale Serve 的 HTTPS 僅在 tailnet 可達。
- 狀態 API `/api/health` 只表示服務可連線，不保證 OCR 相依套件或辨識品質已通過驗收。

參考：[Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)、
[Chrome 區域網路權限](https://developer.chrome.com/blog/local-network-access)。

## 測試

`node --test local-app/test/api-client.test.js`：兩主機路由、工作歸屬、CORS、錯誤訊息與 HTTPS 設定保護。

`node local-app/test/portal-smoke.cjs <測試.pdf> mac`（或 `company`）：在真正的 Vercel 頁面用第 1 步轉檔、
確認附圖與下載連結，切換主機後仍能從原主機下載。需要 Playwright／Chrome；可指定 `NODE_PATH` 與 `BROWSER_BIN`。
此測試會在選定主機新增測試工作，不呼叫雲端 AI。

網站路由驗收與 OCR 品質驗收分開：能上傳並顯示結果，不代表題號、圖表或 Word 已符合需求。
