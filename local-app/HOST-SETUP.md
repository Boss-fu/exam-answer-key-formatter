# 主機設定指南（Windows）— 一次設好，其他裝置用 Tailscale 連

目標：在**一台常開機的電腦**上把詳解應用跑起來，之後你在任何裝置（筆電、手機）
用 Tailscale 連進去，開網址就能用，不必再開 Claude app、不必碰指令。

> 下面步驟 1–3 是**一次性**的。設好之後，日常只要「開網址」。
> 如果你不想自己弄，可以開一個「連到這台電腦的 Claude Code 工作」，叫它「照 local-app/HOST-SETUP.md 幫我設好」，它能代勞。

---

## 一次性設定（在主機那台做一次）

### 1. 裝 Node.js
到 https://nodejs.org/ 下載 **LTS 版**，一路下一步安裝完成。

### 2. 準備解題引擎（二選一）
- **（建議，免金鑰）Claude Code CLI**：沿用你現有的 Claude Code。
  開「命令提示字元」執行：
  ```
  npm install -g @anthropic-ai/claude-code
  claude            （第一次會要你登入）
  ```
- **（替代）API 金鑰**：如果不想裝 CLI，可用 API 金鑰。編輯 `start.bat`，
  依裡面的註解填入 `ANTHROPIC_API_KEY`。

### 3. 裝 Tailscale
到 https://tailscale.com/ 下載 Windows 版，安裝並用你的帳號登入。
在**其他要連進來的裝置**（手機/筆電）也裝 Tailscale、登入**同一個帳號**。

---

## 每次使用

1. 在主機那台，進到這個資料夾，**雙擊 `start.bat`**（會出現一個黑視窗，別關）。
2. 主機本機測試：瀏覽器開 `http://localhost:8787`。
3. 別台裝置（已連 Tailscale）：開 `http://主機名稱:8787`。
   - 主機名稱怎麼查：在主機命令提示字元執行 `tailscale status`，或 `tailscale ip -4` 拿到像 `100.x.x.x` 的位址，用 `http://100.x.x.x:8787`。
   - 把這個網址在手機/筆電**加到書籤**，以後點一下就到。

> 第一次啟動時 Windows 防火牆可能跳出詢問，勾選允許在**私人網路**存取即可。

---

## 想「開機自動啟動」（免每次雙擊）

把 `start.bat` 的捷徑放進開機資料夾：
1. 對 `start.bat` 按右鍵 →「建立捷徑」。
2. 按 `Win + R`，輸入 `shell:startup`，Enter，會開啟「啟動」資料夾。
3. 把剛剛的捷徑丟進去。之後開機就會自動跑起來。

## 想「自動更新」（在別台電腦維護、主機自己拉新版）

`local-app\auto-update.ps1` 每次執行會：檢查 GitHub `main` 有沒有新版 → 有就快轉更新並重開服務；服務沒在跑就把它叫起來。
用工作排程器設成「登入時＋每 5 分鐘」執行，就能取代上面的開機捷徑（兩者擇一，別同時用）：

```powershell
$ps1 = "<repo 路徑>\local-app\auto-update.ps1"
$act = New-ScheduledTaskAction -Execute "conhost.exe" -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$ps1`""
$t1 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "詳解本機應用 自動更新" -Action $act -Trigger $t1,$t2 -Settings (New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable)
```

- 主機的 repo 請各自 `git clone` 在 Dropbox/OneDrive **以外**的資料夾，只由這個腳本更新，不要在主機上直接改檔。
- 紀錄在 `local-app\output\auto-update.log`。

---

## 安全注意

- Tailscale 是你**私有裝置之間**的網路，把 `8787` 埠開在 tailnet 上是安全的。
- **不要**把這個埠轉發到公開網際網路，也不要用 `tailscale funnel` 對外公開。
- 這個應用沒有帳號密碼，只靠 Tailscale 的私有網路保護。

---

## 卡住時

- 黑視窗顯示「找不到 Node.js」→ 回步驟 1 裝 Node。
- 產詳解時報「無法啟動 Claude Code」→ 主機沒裝或沒登入 CLI，回步驟 2。
- 別台連不進來 → 兩台是否都登入**同一個 Tailscale 帳號**？主機 `start.bat` 是否還開著？
