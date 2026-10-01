# 詳解本機應用：Codex 維護與主機設定說明

> 這份文件可以直接丟給 Codex 讀，例如跟 Codex 說：「請完整閱讀 `Codex維護與主機設定說明.md`，照第 X 部分幫我做。」
> GitHub repo：`https://github.com/Boss-fu/exam-answer-key-formatter`，分支一律用 **`main`**。

---

## 0. 整體架構

```
┌──────────────────────┐   git push (main)   ┌────────┐   每 5 分鐘檢查   ┌───────────────────────────┐
│ 你的電腦（Codex 維護）│ ──────────────────▶ │ GitHub │ ◀──────────────── │ 公司主機 C:\詳解應用        │
│ 改程式、測試、推送     │                     └────────┘   有新版→更新→重開 │ 跑 http://localhost:8787   │
└──────────────────────┘                                                    └───────────────────────────┘
                                                                              ▲
                              手機／筆電（Tailscale 同帳號）── http://ljm2222:8787 ─┘
```

- **Codex 只負責維護程式**（在你自己的電腦上改、測、推）。
- **產詳解的是主機上的 Claude Code**：主機必須安裝並登入 Claude Code CLI。
- 主機上的工作排程器「詳解本機應用 自動更新」每 5 分鐘執行 `local-app\auto-update.ps1`：
  GitHub `main` 有新版就快轉更新並重開服務；服務沒在跑就把它叫起來。
- 主機紀錄檔：`C:\詳解應用\local-app\output\auto-update.log`

### 三條鐵則
1. **程式一律放在 Dropbox/OneDrive 以外**，每台電腦各自 `git clone`。不要讓雲端硬碟同步 `.git`。
2. **只在你的電腦改，不要在主機上改檔**。主機上若有未提交修改，自動更新會停住，只寫紀錄不覆蓋。
3. **只推 `main`，不要 force push**。主機只接受「快轉」更新，歷史被改寫會導致主機停止更新。

---

## 第一部分：在你自己的電腦設定 Codex 維護環境（一次性）

### 1-1. 安裝工具（Windows，以 PowerShell 執行）
```powershell
winget install --id Git.Git -e --source winget
winget install --id OpenJS.NodeJS.LTS -e --source winget
```
裝完**關掉終端機重開**，確認：
```powershell
git --version
node -v
```
再安裝並登入 Codex（App 或 CLI 擇一，依 OpenAI 官方說明）。

### 1-2. 把程式 clone 到 Dropbox 以外
```powershell
git clone https://github.com/Boss-fu/exam-answer-key-formatter.git C:\dev\exam-answer-key-formatter
cd C:\dev\exam-answer-key-formatter
git config user.name "Boss-fu"
git config user.email "Boss-fu@users.noreply.github.com"
npm install
```
第一次 clone／push 會跳出 GitHub 登入視窗，選「Sign in with your browser」登入 Boss-fu 帳號。

### 1-3. 讓 Codex 知道專案規則
Codex 會自動讀 repo 根目錄的 `AGENTS.md`，裡面已寫好本專案規則，包括先讀本說明、只在 `main` 工作、禁止 force push、推送前先本機測試。clone 下來即可使用，不用另外設定。要調整規則時，改 `AGENTS.md` 再推上 `main`。

### 1-4.（選用）在你的電腦本機測試應用
應用本身需要 Claude 當解題引擎，兩種方式擇一：
- **A. 你的電腦也裝 Claude Code CLI（免金鑰）**
  ```powershell
  npm install -g @anthropic-ai/claude-code
  claude.cmd          # 第一次登入；PowerShell 要打 claude.cmd，不是 claude
  ```
  之後雙擊 `local-app\start.bat`，開 `http://localhost:8787`。
  **不要**加防火牆規則和工作排程，那是主機才需要的。
- **B. 只測介面／Word 產出**：不用引擎也能開頁面，產詳解那一步會報錯，屬於正常現象。

---

## 第二部分：日常維護流程（用 Codex）

1. **開始前先同步**：`git pull --ff-only`
2. **跟 Codex 說要改什麼**（見下方範例指令）
3. **本機測試**：`start.bat` → `http://localhost:8787` → 丟一小段題目試產詳解 → 產 Word
4. **提交並推送**：`git add …` → `git commit -m "描述改了什麼"` → `git push origin main`
5. **確認主機已更新（約 5 分鐘內）**：打開主機網址，看改動有沒有生效。
   也可以到主機看 `auto-update.log`，會出現「已更新 xxxxxxx -> yyyyyyy」「重開服務」。

### 給 Codex 的範例指令
- 「讀 `AGENTS.md` 和 `Codex維護與主機設定說明.md`。先 `git pull --ff-only`，然後把網頁上的『產生詳解』按鈕改成『開始產生』，本機測試後 commit 並 push 到 main。」
- 「詳解格式規範改了，更新 `理化數學詳解與簡答產出規範.md` 和 `exam-answer-key-formatter/SKILL.md`，確認 `local-app/engine.js` 有讀到，推上 main。」
- 「主機的 auto-update.log 出現『無法快轉』，請檢查 main 的歷史是否被改寫，說明狀況，先不要動任何東西。」

### 哪些檔案改了會影響什麼
| 檔案 | 作用 | 改完主機要做什麼 |
|---|---|---|
| `理化數學詳解與簡答產出規範.md`、`exam-answer-key-formatter/SKILL.md` | 每次產詳解時塞給 Claude 的規則 | 自動（更新後即生效） |
| `local-app/public/index.html` | 網頁介面 | 自動 |
| `local-app/server.js`、`engine.js`、`render.js`、`code/*.js` | 伺服器、引擎、Word 排版 | 自動（會重開服務） |
| `package.json` | 相依套件 | 自動（會先跑 `npm install`） |
| `local-app/auto-update.ps1` | 自動更新腳本本身 | 自動（下一輪就用新版） |
| `local-app/start.bat` | 啟動方式 | 自動（重開時生效） |
| 工作排程器設定、防火牆、Tailscale | 主機系統設定 | **不會自動**，要到主機處理（見第三部分） |

---

## 第三部分：設定一台新主機（從零開始，一次性）

> 目前主機：公司電腦 `ljm2222`（Tailscale IP `100.75.52.69`），程式在 `C:\詳解應用`。
> 要換主機或多設一台時，照這一節做。可以直接叫 Codex 或 Claude Code 在主機上執行。

### 3-1. 安裝 Git、Node.js
```powershell
winget install --id Git.Git -e --source winget
winget install --id OpenJS.NodeJS.LTS -e --source winget
```
裝完重開終端機，`git --version`、`node -v` 都要有版本號。

### 3-2. 安裝並登入 Claude Code CLI（解題引擎，免 API 金鑰）
```powershell
npm install -g @anthropic-ai/claude-code
claude.cmd
```
- **注意**：PowerShell 會擋 `claude`（顯示「已停用指令碼執行」），請改打 `claude.cmd`。不需要修改執行原則。
- 選「Claude 帳號（訂閱）」登入，完成後打 `/exit`。
- 驗證：`claude.cmd -p "reply with just OK"` 要回 `OK`。

### 3-3. clone 到 Dropbox 以外並安裝套件
```powershell
git clone https://github.com/Boss-fu/exam-answer-key-formatter.git C:\詳解應用
cd C:\詳解應用
npm install
```
第一次會跳 GitHub 登入視窗，登入 Boss-fu 帳號。登入資訊會存進 Windows 認證管理員，之後自動更新就不用再登入。

### 3-4. 註冊自動更新＋開機自動啟動（工作排程器）
用一般 PowerShell（不用系統管理員）執行：
```powershell
$ps1 = 'C:\詳解應用\local-app\auto-update.ps1'
$act = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$ps1`""
$t1  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$t2  = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName '詳解本機應用 自動更新' -Action $act -Trigger $t1,$t2 -Settings $set
Start-ScheduledTask -TaskName '詳解本機應用 自動更新'
```
- 這個排程**同時取代**「開機資料夾放 start.bat 捷徑」，兩者不要並用，否則會搶同一個埠。
- 服務以最小化的黑色視窗執行，不要關掉它。關掉的話最多 5 分鐘後會自動重開。
- 驗證：`http://localhost:8787` 能開，`local-app\output\auto-update.log` 出現「已啟動 start.bat」。

### 3-5. 防火牆（需系統管理員）
開始 → 搜尋 PowerShell → 右鍵「以系統管理員身分執行」：
```powershell
New-NetFirewallRule -DisplayName "詳解本機應用 8787" -Direction Inbound -Protocol TCP -LocalPort 8787 -Action Allow -Profile Private
```
只開放「私人網路」，Tailscale 屬於私人網路，所以只有 Tailscale 裝置連得進來。

### 3-6. Tailscale
主機和要連線的裝置都安裝 Tailscale，登入**同一個帳號**。查主機位址：
```powershell
& "$env:ProgramFiles\Tailscale\tailscale.exe" ip -4
```
其他裝置開 `http://主機名稱:8787` 或 `http://100.x.x.x:8787`，加入書籤。

### 3-7. 電源設定
設定 → 系統 → 電源 → 「插電時電腦進入睡眠」改成**永不**，不然睡眠時其他裝置連不到。

### 3-8. 最終驗收清單
- [ ] `claude.cmd -p "reply with just OK"` 回 `OK`
- [ ] `http://localhost:8787` 可開，丟一題能產詳解、能下載 Word
- [ ] 工作排程器有「詳解本機應用 自動更新」，上次執行結果 `0`
- [ ] 手機（Tailscale）開 `http://主機名稱:8787` 可用
- [ ] 從你的電腦推一個小改動到 main，5 分鐘內主機 log 出現「已更新」

---

## 第四部分：疑難排解

| 狀況 | 原因／處理 |
|---|---|
| 產詳解報「無法啟動 Claude Code」「spawn claude ENOENT」 | Windows 上 Node 叫不到 npm 的 `claude` 包裝。`start.bat` 已自動設定 `CLAUDE_BIN` 指向 `%APPDATA%\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe`。確認這個檔案存在，並且是用 `start.bat` 啟動 |
| 報「Not logged in」 | 主機 Claude 登入過期 → 主機打 `claude.cmd` 重新登入 |
| PowerShell 打 `claude` 顯示「已停用指令碼執行」 | 改打 `claude.cmd`，不需要改系統設定 |
| log：`git fetch 失敗` | 主機沒網路，或 GitHub 登入失效 → 在主機 `C:\詳解應用` 手動執行 `git fetch` 重新登入 |
| log：`本機有未提交修改，略過更新` | 有人在主機上改了檔 → 用 `git status` 查看；要保留就請 Codex 在你的電腦重做後推上去，再在主機 `git restore <檔案>` |
| log：`無法快轉`／`領先或分歧` | main 歷史被改寫或主機上有本地提交 → 停下來人工檢查，**不要** force |
| 推了新版，主機沒變 | 等 5 分鐘；查看 log；確認推的是 `main` |
| 別台連不進來 | 主機黑色視窗還在嗎？兩台 Tailscale 是否同帳號？主機是否睡眠？ |
| 黑色視窗停在「請按任意鍵繼續」 | 服務當掉了。關掉視窗，5 分鐘內會自動重開；或手動雙擊 `start.bat` |

---

## 附錄：如果想讓「產詳解」也改用 Codex／OpenAI

目前 `local-app/engine.js` 支援三種引擎：
- `claude-code`（預設，呼叫主機上的 `claude -p`）
- `api`（Anthropic API 金鑰）
- `codex`（主機上的 `codex exec --full-auto`）

若要改用 Codex CLI，主機先安裝並登入 Codex CLI，然後在 `local-app\start.bat` 的 `call npm start` 前加入 `set ENGINE=codex`。若 `codex` 不在 PATH，另設 `CODEX_BIN` 為其完整路徑。Codex 模式支援文字詳解、拍照詳解與掃描考卷的 Word／CSV 工作流。
