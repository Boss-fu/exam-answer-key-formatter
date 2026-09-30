# 詳解產生器（本機應用）

在**你自己的電腦**上跑的小網頁工具：丟考卷文字 → 產生詳解 → 產出 Word(.docx) 並存到本機。
因為是本機伺服器，才有權限讀寫你的檔案；claude.ai 上的 artifact 是沙箱網頁，碰不到本機，做不到這件事。

## 為什麼不是用 artifact

| | artifact（claude.ai 沙箱網頁） | 本機應用（這個） |
|---|---|---|
| 讀寫本機檔案 | ❌ 不行 | ✅ 可以 |
| 叫本機 Claude Code | ❌ 不行 | ✅ 可以（`claude -p`） |
| 存 .docx 到你的資料夾 | ❌（下載被沙箱擋） | ✅ 存到 `output/` |
| 用途 | 只適合檢視／驗證準確度 | 真正的「丟檔案→出檔案」管道 |

## 安裝與啟動

在 repo 根目錄（不是這個子資料夾）：

```bash
npm install          # 安裝 docx 套件
npm start            # 啟動，預設 http://localhost:8787
```

瀏覽器打開 `http://localhost:8787`，貼上考卷文字，按「產生詳解」→「產出 Word 並存本機」。

## 選擇解題引擎

用環境變數 `ENGINE` 決定誰來解題：

- **`claude-code`（預設）** — 呼叫本機已安裝的 Claude Code CLI（`claude -p`）。
  沿用你現有的 Claude Code，不需另外的 API 金鑰。
  需要本機能在終端機直接執行 `claude`；若指令名稱不同，用 `CLAUDE_BIN` 指定。
  ```bash
  npm start
  ```
- **`api`** — 呼叫 Anthropic Claude API，需要金鑰。
  ```bash
  ENGINE=api ANTHROPIC_API_KEY=sk-ant-... npm start
  # 可選：ANTHROPIC_MODEL=claude-sonnet-4-5
  ```
- **`codex`** — 呼叫主機已登入的 Codex CLI。可處理文字、拍照詳解與掃描考卷 Word／CSV 工作流；Codex 會在每次工作建立的隔離資料夾內讀寫產物。
  ```bash
  ENGINE=codex npm start
  # 若 codex 指令不在 PATH：CODEX_BIN=/完整/路徑/codex npm start
  ```

## 產出規則

- 詳解格式、欄寬、字體、行高、分頁等一律沿用 repo 的 `理化數學詳解與簡答產出規範.md` 與
  `exam-answer-key-formatter/SKILL.md`（引擎會把這兩份當系統規則）。
- 數學公式為單一錢號 `$...$` 純文字；開檔後在 Word 用 MathType 的 Toggle TeX（Alt+\ / Option+\）批次轉換。
- 產出的 Word 會同時：在瀏覽器下載、並存一份到 `local-app/output/`。

## 目前範圍與限制

- **可上傳「已打字排版」的 PDF / Word(.docx)，或直接貼文字。** 上傳後伺服器會抽出純文字放進考卷欄位，
  你確認後再產詳解。**掃描成圖片的 PDF／照片**沒有文字層，讀不到字——那種先用 `exam-to-word`
  技能整理成文字/乾淨 Word，再上傳。
- 產出的答案由引擎判斷，**務必用「官方答案」欄位對照命中率**再採用（見 Step 2）。
- 這是本機開發用途，沒有做身分驗證，不要對外網開放這個埠。

## 檔案

- `server.js` — 本機 HTTP 伺服器（`/api/generate` 產詳解、`/api/render` 產 Word）
- `engine.js` — 解題引擎抽象層（claude-code / codex / api）
- `public/index.html` — 前端頁面
- `output/` — 產出的 .docx（自動建立，已被 .gitignore 忽略）
- 共用產檔邏輯在 `../code/render.js`（包裝 `../code/lib3.js`）

## 省 token / 省成本的用法

分清楚兩條路，成本差很多：

| 做什麼 | 走哪 | 為什麼 |
|---|---|---|
| 日常做考卷（純文字乾淨考卷、詳解生成） | **這個網頁 app** | 每份是一次乾淨、聚焦的呼叫，最省 |
| 改功能／修程式 | 找 Claude 對話 | 只在必要時；長對話會累積上下文，越後面越貴 |

其他省法：

- **圖片自己在題庫系統框選截圖貼上** → 完全不花 token（純文字模式的〔含圖：…〕就是貼圖定位點）。
- **少貼大截圖給 AI**（圖片很吃 token）；能用文字描述就打字，非貼不可就裁小、只留關鍵區域。
- **換主題就開新對話**；別在同一條長對話裡一直接不同的事。
- 引擎走 `claude-code`、用的是 Claude 訂閱額度。**訂閱制（Pro/Max）下用這網頁跑考卷不是按 token 計費**，只要別短時間狂跑撞到用量上限即可。
