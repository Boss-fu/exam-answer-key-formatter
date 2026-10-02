# OCR 優化交接（給 Codex）

> 2026-10-02 由 Claude 交接。目標：全本地 OCR 把學校掃描考卷轉成**茲茲格式 Word**，準確度達到上線標準，並且**贏過俊瑋的「卷土重來」（zizi-exam）**。
> 使用者（bossfu）的工作習慣：沒回應＝在忙，繼續做；每改 3～5 次、有突破才回報，附一張簡單的前後對照（表或圖）；要上線前用保留集驗收。

## 1. 不可違反的限制

- **全本地、只用 CPU、不呼叫任何外部 API**（老闆否決過 API）。本機 Ollama 模型可以用（不算外部 API）。
- 寧可少擦、不可傷印刷內容；不確定的地方列進「確認清單」，不要猜。
- 學生考卷含個資：`ocr/testset/`、`ocr/holdout/`、`ocr/results/` 都在 .gitignore，**不准 commit 考卷或辨識內容**。
- repo 規則照 `AGENTS.md`：只在 main、先 `git pull --ff-only`、推之前本機測過、不 force push。推上 main 後主機 5 分鐘內自動更新。

## 2. 架構（都在 `ocr/`）

| 檔案 | 作用 |
|---|---|
| `pyexam.py` | OCR 主引擎：PDF→300~350dpi→**自動轉正**（`fix_orientation`）→**清紅藍筆**（`remove_color_ink`）→**偵測框找欄界**（`text_layout`：雙欄、A3 橫式四欄、頁首整行讀）→PP-OCRv5→**表格**（`detect_tables`/`fill_table`，空白格補認）→**附圖**（`figure_candidates`/`split_figure_text`，圖框含短標籤與軸名、夾到的正文塗白、手寫圈字不算圖）→輸出 `.txt`（〔含圖 k〕〔表格 k〕佔位）、`*_fig*.png`、`_tables.json`、`_lines.json`（每行 col／indent／margin_ink） |
| `ppocr_engine.py` | PP-OCRv5 封裝。`det_boxes`（只偵測）、`rec_only`（只辨識）、`ocr_lines`。**送進 onnxruntime 前一定要 `np.ascontiguousarray`**（否則 A3 卷 segfault）。簡繁只轉 Big5 以外的字（干擾、游離不能被改） |
| `to_zizi_word.py` | `.txt` → 切題 → 茲茲範本 Word（`exam-to-word/scripts/buildlib.py` + `assets/template.docx`）＋確認清單。選項排法、否定詞粗體底線、橫幅（年級／冊別／段次／學校，檔名格式補欄位）都在這 |
| `eval_gt.py` | **主評測**：跟人工正解比 漏字／順序（LCS）／多字／CER／題數／Word 能否打開，並同時評分試卷寶。`--fresh` 重跑 OCR，`--set holdout` 驗收 |
| `qcount.py` | **快速量題數**（用 eval 快取，不重跑 OCR）。改切題規則後先跑它 |
| `vs_zizi.py` | 跟卷土重來對打（同一批卷、同一套評分）。要設 `ZIZI_DIR` 指向 zizi-exam 程式資料夾 |
| `vlm_check.py` | 用本機 Ollama `qwen3-vl:8b` 看原卷數題號，抽查漏題／黏題（每塊 20~50 秒，只用來抽查） |
| `report_gt.py` + `report_gt.html` | 改前／改後／試卷寶 的報告頁 |

## 3. 環境

```bash
bash ocr/setup_env.sh        # Python 3.11 venv（~/.venvs/zizi-exam）、PP-OCRv5 模型、測試卷
```
- Python 一律用 `~/.venvs/zizi-exam/bin/python`（系統 python3 是 3.14，沒有套件）。
- 測試卷來源（同一台 Mac）：`~/exam-answer-key-formatter/ocr/{testset,holdout}`。原始出處是 Google Drive「S輔導老師福大/題庫系統建置/歷屆考題整理/國中段考卷」（三件組：原卷 PDF＋試卷寶 docx＋茲茲人工完成 docx）與「許力歷屆考題考卷」（原卷＋試卷寶，無正解）。
- 卷土重來的程式：`~/Library/CloudStorage/GoogleDrive-sandy880507@gmail.com/My Drive/茲茲考卷/zizi-exam`（**不要改它、不要搬它的程式或 dehand.onnx 進我們的產品**，只拿來對打；Mac 上跑它也要 `np.ascontiguousarray` 的問題，113-1-2 會 segfault）。
- 檢查 Word 版面：`qlmanage -t -s 1400 -o <資料夾> <檔.docx>` 產生第一頁 PNG。**不要用 AppleScript 操作 Word**（曾經把使用者開著的文件關掉沒存）。

## 4. 上線標準（開發集 13 份，排除 113-1-1_G8_陽明國中）

| 指標 | 門檻 | 目前（v8） |
|---|---|---|
| 漏字率 | ≤ 3% | ✅ 約 2.7%（試卷寶 8.1%） |
| 閱讀順序（LCS） | 加權 ≥ 95%、每份 ≥ 90% | 🟡 93.6%（試卷寶 89.5%）；只剩百齡 112-2-2 88.5% < 90% |
| 題數 | 每份都對 | 🔴 v8：4/11 全對、總誤差 25（`ocr/qcount.py v8`；歷程 46→42→31→25） |
| 個資 | 0 | ✅ 班級／座號／姓名（含表格式卷頭）都會清掉 |
| Word | 全部能開、表格與圖在、符合 exam-to-word 技能 | ✅ 已用 Quick Look 看過第 1 頁 |
| 保留集 | 同上標準 | ⬜ 還沒跑（`eval_gt.py --set holdout`，**平常不准拿來調規則**） |

注意：`112-1-1_G9_石牌國中`、`112-1-1_G9_陽明國中` 的正解只收理化（掃描卷還有地科），題數不計；`113-1-1_G8_陽明國中` 正解對不上，全部排除。

對打卷土重來（舊數字，有幾份是在切題規則壞掉時跑的，要重跑）：我們的卷上 順序 92.5% vs 81.6%、漏字 2.9% vs 6.9%；他的卷上 順序 84.7% vs 88.0%、漏字 5.8% vs 6.3%；**題數兩邊都輸他**（1/12 vs 4~5/12）。

## 5. 做過什麼（有效 ✅／失敗 ❌）

- ✅ 欄界改用 PP-OCR 偵測框覆蓋數（相對門檻 < 兩側中位數 35%；一邊空、中間全白也算；只用「印刷字高度」的框，過濾手寫）。順序 64.6% → 93%。
- ✅ 自動轉正：先看偵測框是橫是直決定 0/180 或 90/270，再比兩個方向的高信心字數。
- ✅ 頁首整行讀，且**頁首不能深過右欄第一行正文**（v8，百齡 112-2-2 題數 31→38）。
- ✅ 清紅藍筆（HSV：色相 0~10／160~179／95~135、飽和度 ≥45，往外擴 2px）：救回被紅勾蓋住的題號與選項字母。
- ✅ 表格重建：形態學取格線，投影前把線上下加粗 3px（掃描微歪）；直線最短 0.2 吋（避免字的直筆畫）；空白格去雜點後 `rec_only` 補認，單獨 z/Z → 乙。
- ✅ 附圖：用圖的**垂直中心**排序（放在題目右邊的圖才不會掛到上一題）；「成句」中文（≥4 字的框）才算非圖；軸名／刻度串（不像句子、落在圖水平範圍內）併進圖；框內大半是高信心印刷字＝學生圈字，不算圖。
- ✅ 切題：題號容錯（`RE_Q` 吃作答括號、`RE_Q_LOOSE` 吃手寫前綴但號碼要 +1）；作答括號開頭的長句＋後面接 (A) 開頭選項＝新題；**同一題出現第二個 (A) 就拆兩題**（42→31，零退步）；1~2 字的雜訊題丟掉。
- ❌ 「選項組」重寫（每題一組 ABCD 決定題數）：總誤差 56～82，比規則版差，已還原（實驗版不在 repo）。
- ❌ 「題號位置墨跡」(`margin_ink ≥ 0.065` 就切)：學生在左邊空白寫字 → 士林 73/40 大量多切。目前 `MARGIN_INK = 9.9` 停用；資料仍輸出，可以跟其他條件一起用。
- 教訓：**每改一次就用 `qcount.py`（切題）或 `eval_gt.py`（OCR）量總分，變差就還原**；同一個問題改超過兩次還沒好，就停下來重新設計，不要再調門檻。

## 6. 待辦（依優先順序）

1. **題數**（最大缺口）：剩下多為「題號被黑筆蓋掉、又沒有第二個 (A) 可以拆」或多切（蘭雅 112-2-1 43/40）。建議方向：
   - 題號序列全域對齊（動態規劃：卷面上看到的題號＋位置，容許缺號與手寫雜訊）。
   - `margin_ink` 只在「上一題選項已完整」且「這行 indent 跟上一題題幹開頭一致」時才當訊號。
   - 規則解不了的頁，用 `vlm_check.py`（qwen3-vl）列題號開頭，跟我們的切法對齊。
2. 閱讀順序 < 90%：百齡 112-2-2（88.5%，看第 2 頁以後的欄界／頁首與手寫）。
3. 化學式上下標（KClO3、C3H6O3 被拆壞）、鉛筆計算過程混進題文。
4. 兩張相鄰的圖被合成一張（`figure_candidates` 的 merge_gap）、圖邊緣標籤被切。
5. 用 `vs_zizi.py A` / `B` 重跑對打，兩個主場都贏才算贏。
6. 達標後 `eval_gt.py --set holdout --tag final --fresh` 驗收，通過再跟使用者說「可以上線」。
7. local-app 還沒接 `to_zizi_word.py`（純 Python OCR 按鈕目前只產 pyexam 的簡易 docx）。

## 7. 使用者已決定

- **九年級卷全部都收：理化、生物、地科都要**（2026-10-02 使用者確認）。橫幅維持「國九自然」、保留所有題目，不要只收理化。
  茲茲舊的正解有些只收理化（`112-1-1_G9_石牌國中`、`112-1-1_G9_陽明國中`），那是正解不完整，不是我們多切；這兩份題數不計。
- 推 main：本機測過、確定有進步才推（使用者 2026-10-02 同意「繼續優化並且推上 main」）。

## 8. 2026-10-02 新增（AI 大腦、一鍵流程、Apple Vision）

- **AI 大腦**（`ocr/ai_judge.py`）：Python 做完 OCR 後只問 AI 一次（行號＋文字草稿，約 6k token），回傳題目開頭行號／雜訊行／短錯字。
  預設 `auto`＝Claude → Codex 接力，全部失敗退回規則。開發集題數總誤差：規則 25 → 規則＋Claude 8。
  Codex 單份實測比 Claude 準（39/40 vs 42/40）但慢 4 倍；Ollama qwen2.5 7B 嚴重錯誤（40 題抓 4~13 個），不要用。
  `python ocr/qcount.py v8 --ai claude|codex` 比較；11 份的 Codex 完整比較還沒跑完。
- **一鍵流程**：`ocr/exam_to_word.py`（OCR → AI → 茲茲 Word），local-app 第 1 步「掃描卷轉成茲茲格式 Word」已接上（`local-app/py-ocr.js` 自動找 `~/.venvs/zizi-exam`）。
  **Windows 主機還不能用**：要補 Windows 版的 Python 3.11＋套件＋模型安裝說明（`setup_env.sh` 只適用 Mac）。
- **Apple Vision**（`OCR_ENGINE=vision`，只限 Mac）：一頁辨識 1.5 秒、幾乎不吃 CPU，但整體 順序 85.3%（PP-OCRv5 93.6%）、題數總誤差 76（25），**不當預設**。
  推測 Vision 的行框會跨欄，欄界偵測失準。下一步可試混合：`det_boxes`（版面）用 PP-OCRv5、`ocr_lines`（認字）用 Vision。
- 多頁平行（`OCR_WORKERS`）在 M2 Pro 反而較慢，預設 1。
