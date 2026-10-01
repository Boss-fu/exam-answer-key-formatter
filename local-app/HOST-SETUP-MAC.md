# Mac 主機設定（把詳解應用跑在一台 Mac 上）

目標：在一台**常開機的 Mac** 上把應用跑起來，其他裝置用 Tailscale 連進來、開網址就能用。
核心只需要 **Node ＋ Claude Code**，**不需要 Python**。

> 「跑不動」最常見的三個原因：① 沒裝 Node　② 沒裝 / 沒登入 Claude Code　③ 沒跑 `npm install`。下面照順序做一次就會動。

---

## 一次性設定（在這台 Mac 做一次）

### 1. 裝 Node.js
到 <https://nodejs.org/> 下載 **LTS 版**安裝；或在「終端機」執行（需先有 Homebrew）：
```bash
brew install node
```
確認：`node -v`（有印出版本就 OK）。

### 2. 裝並登入 Claude Code CLI
```bash
npm install -g @anthropic-ai/claude-code
claude            # 第一次會要你登入，用你的 Claude 帳號登入一次
```
確認：`which claude`（有印出路徑就 OK）。

### 3. 取得程式碼（clone 到非 iCloud/雲端同步的資料夾）
```bash
cd ~
git clone https://github.com/Boss-fu/exam-answer-key-formatter.git
cd exam-answer-key-formatter
```
> 不要放在 iCloud Drive／桌面同步資料夾，避免雲端一直同步 `.git`。

### 4. 裝 Tailscale（要讓別台裝置連進來才需要）
到 <https://tailscale.com/download> 下載 macOS 版，用你的帳號登入；其他要連進來的裝置也裝、登入**同一個帳號**。

---

## 每次使用
1. 到 `exam-answer-key-formatter/local-app/`，**雙擊 `start.command`**（會開終端機、跑起服務，別關那個視窗）。
   - 若雙擊沒反應，第一次先在終端機執行一次：`chmod +x local-app/start.command`
2. 本機測試：瀏覽器開 <http://localhost:8787>
3. 別台裝置（已連 Tailscale）：開 `http://<這台Mac的Tailscale名稱>:8787`
   - 名稱怎麼查：終端機執行 `tailscale status`，或 `tailscale ip -4` 拿到 `100.x.x.x`，用 `http://100.x.x.x:8787`

## 開機自動啟動（免每次雙擊）
系統設定 → 一般 → 登入項目 →「開機後開啟」加入 `start.command`。

## 自動更新（在別處改 GitHub，Mac 自己拉新版）
建一個 `~/Library/LaunchAgents` 的排程，或最簡單：每天手動在資料夾執行
```bash
git pull origin main
```
拉到新版後，把跑 `start.command` 的視窗關掉再雙擊一次即可。

---

## 卡住時
- 終端機顯示「找不到 Node.js」→ 回步驟 1。
- 產詳解/一鍵包報「無法啟動 Claude Code」→ 回步驟 2（沒裝或沒登入 claude）。
- 網頁打得開但按鈕沒反應 → 多半是沒 `npm install`，在資料夾執行 `npm install` 再重開。
- 別台連不到 → 兩邊的 Tailscale 都要開著、登入同一帳號。
