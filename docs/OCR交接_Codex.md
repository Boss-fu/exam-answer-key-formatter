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

## 9. 分工與檔案歸屬（2026-10-03 主管審查後訂定，避免工具重疊）

**同一件事只准有一份實作。** 要改善某件事，就改下面那支「唯一負責」的檔案；要新增功能，先確認不屬於表裡任何一列。

| 功能 | 唯一負責的檔案 | 不要再另外做 |
|---|---|---|
| OCR 引擎（PP-OCRv5） | `ocr/ppocr_engine.py`（rapidocr＋ONNX，輕量、CPU） | 不要再裝 paddlepaddle／paddleocr 第二套（`local-app/ocr-runtime/`、`.venv-ocr`） |
| Python 環境 | `ocr/setup_env.sh`（Mac）＋要補的 `ocr/setup_env.ps1`（Windows），共用 `~/.venvs/zizi-exam` | 不要再建 `.venv-ocr` 等第二個虛擬環境 |
| 掃描卷 → 茲茲 Word | `ocr/exam_to_word.py` → `pyexam.py` → `ai_judge.py` → `to_zizi_word.py`；網頁經 `local-app/py-ocr.js` | 不要另寫 Word 預覽產生器（`local-app/tools/build_ocr_word_*.py`） |
| 題號／題目邊界 | `ocr/to_zizi_word.py`（規則）＋`ocr/ai_judge.py`（AI） | `local-app/ocr-preflight.js` 的 `buildQuestions` 不要再加新規則；覆核頁之後改吃 pyexam 結果 |
| 準確度評測 | `ocr/eval_gt.py`（文字／順序／題數／Word）、`ocr/qcount.py`（快速題數） | 不要再寫第二套題號評測（`ocr/question_anchor_benchmark.py` 併進 qcount 或刪除） |
| 資料分組、標註 | `ocr/dataset_catalog.py`、`ocr/docx_question_candidates.py`（Codex 新增，**保留**） | — |

### Codex 未提交工作的審查結論（2026-10-03）

| 項目 | 判定 | 理由 |
|---|---|---|
| `local-app/extract.js` 改用 Poppler `pdftotext` | ✅ 採用 | pdf-parse 跟新版 Node（目前 v26）不相容，是真的 bug；Mac 已有 `/opt/homebrew/bin/pdftotext`，Windows 主機要補裝 Poppler |
| `ocr/setup_env.sh` 計數改用 `find` | ✅ 採用 | 原本 `ls \| grep -c` 在資料夾為空時回傳 1，配合 `set -e` 會讓腳本中斷 |
| `.gitignore` 加 `local-app/.venv-ocr/` | ⚠️ 隨 Paddle 一起移除 | 若不採用第二套環境就不需要 |
| `ocr/dataset_catalog.py`＋`ocr/test_local_benchmark.py` | ✅ 採用 | 依考試資料夾分 train／validation／test，避免同一份卷的原稿與重製版跨組，之後訓練去筆跡模型用得到；跟現有工具不重疊 |
| `ocr/docx_question_candidates.py` | ✅ 採用 | 從同場考試的 DOCX 產生待核對題目，做正解標註省時間 |
| `ocr/acceptance_audit.py`、`acceptance-gold.template.json` | 🟡 可採用 | 驗收把關（訓練集不得當驗收、標註須人工確認），跟 `eval_gt.py --set holdout` 互補；請在文件註明兩者關係 |
| `ocr/LOCAL-TRAINING.md` | 🟡 修改後採用 | 內容寫成好像沒有本機 OCR 評測（只提到舊的 `run_eval.py`），要補上 `eval_gt.py`／`qcount.py`／`ai_judge.py` 現況 |
| `local-app/ocr-runtime/`、`ocr-preflight.js` 的 PaddleOCR 分支、`setup-ocr-mac.command` | ❌ 不採用 | 跟 `ocr/ppocr_engine.py` 是同一家族模型的第二套執行環境（paddlepaddle 約 1GB），重複且增加主機安裝負擔 |
| `local-app/setup-ocr-windows.ps1` | 🔁 改寫後採用 | Windows 主機正缺安裝腳本；改成安裝 `ocr/requirements.txt`＋PP-OCRv5 ONNX 模型到 `~/.venvs/zizi-exam`，改名 `ocr/setup_env.ps1` |
| `ocr/question_anchor_benchmark.py` | 🔁 合併 | 跟 `qcount.py` 量同一件事，擇一保留 |
| `local-app/tools/build_ocr_word_*.py`、`run_paddle_ocr_audit.py` | ❌ 不採用 | 跟 `to_zizi_word.py` 重疊／依賴 Paddle |
| `local-app/test/ocr-preflight.test.js` | 依 ocr-preflight 去留決定 | — |

### 環境注意：不要把 repo 放在 iCloud 同步的資料夾

`~/Documents` 開了 iCloud「最佳化 Mac 儲存空間」，檔案會被換成雲端空殼（`dataless`），背景服務讀不到就當掉
（2026-10-03 Mac 的 8787 服務因此 `Unknown system error -11` 起不來；原本 `~/exam-answer-key-formatter` 也在同一時間消失）。
跟 AGENTS.md「不要放在 Dropbox／OneDrive」同理。**2026-10-04 已把整個 Codex 資料夾搬出 iCloud，現在位置是 `~/vibe-coding/Codex`**（labcourse、science-qbank 也在 `~/vibe-coding/` 底下），LaunchAgent 已更新；請在 Codex App 改開 `~/vibe-coding/Codex/OCR＋answer key/exam-answer-key-formatter`。
（更正：原本 `~/exam-answer-key-formatter` 是 10/3 被移到垃圾桶 `~/.Trash/exam-answer-key-formatter-20261003`，不是 iCloud 造成；裡面的測試卷、模型、評測快取仍可從垃圾桶救回。）

## 10. 雲端 Claude 交接（2026-10-03，給本地 Codex session）

這段由雲端 session（claude.ai/code）寫。雲端摸不到 Mac 檔案與私密測試卷，只能靠 git＋本文件接力（兩個 session 的 Remote Control 沒連，無法即時互傳）。分工：**Mac 側（部署、主機、環境、OCR 評測迴圈、一鍵管線、切題規則）由本地 session 主導；雲端只做不需要 Mac 的開發/測試/審查。**

### A. 使用者（bossfu）本輪拍板，但與現行文件不一致——請本地 session 與主管確認後對齊

1. **✅ 2026-10-03 使用者決定：Word 一律用 `pre_tpl.docx`（課前考範本）出。** 來源 `ljmath-glitch/science-qbank` 的 `templates/pre_tpl.docx`，使用者指定「這是我們的 word 排版 skill」。
   - **結論（覆蓋第 9 節分工表該列）**：「掃描卷→茲茲 Word」的**格式輸出層改為 `pre_tpl.docx`**。`to_zizi_word.py` 的**切題/卷頭/結構化**邏輯保留，但**輸出改走 pre_tpl.docx**（把它切出來的 sections/items 餵進下面的 context），不再用 `exam-to-word/template.docx`。
   - 契約（已查明）：docxtemplater（paragraphLoop/linebreaks），context = `{subject, book, quiz_label, running_header, chapter, unit, sections:[{title, items:[{no, text, imgs, passage}]}]}`；題號「一、/1.」由範本原生清單自動編號、選項各自 `\n` 換行；配分佔位「  /每題＿＿分，共＿＿分」在 title 有配分時移除。等同 science-qbank `index.html` 的 `downloadWordViaTemplate('pre')`。
   - 雲端已把填範本這層包成 **`ocr/fill_prequiz.js`**（node docxtemplater，見 B）——建議當唯一 Word 輸出層，`to_zizi_word.py` 改成產出 context（JSON）後交給它。pre_tpl.docx 需放進本 repo 或由主機取得（目前在 science-qbank）。

2. **AI 引擎：使用者要 Claude 與 Gemini。** Claude 已在用（`ai_judge.py` 的 host CLI，不算外部 API key）。
   - **✅ 2026-10-03 使用者（bossfu）決定「Gemini 鬆綁」**，即放寬第 1 節「不呼叫任何外部 API」鐵律，允許 Gemini API 當 OCR AI 大腦之一。此條原為老闆否決，屬公司政策層級變更；**請主管知悉、可追溯可回退**。
   - **⚠️ 個資代價**：Gemini 會把考卷內容送到 Google。考卷含學生個資（班級／座號／姓名／作答）。實作時**必須先遮除個資再送**（沿用現有卷頭／作答清除），或明確接受該批資料外流。這點未經主管二次確認前，先以「遮個資後再送」為預設。
   - 實作歸屬：加到 `ocr/ai_judge.py`（本地 session 的 lane），與既有 Claude／Codex 接力並列為可選大腦。雲端不代做。

### B. 雲端已推上 main 的東西——請審查是否與分工表重疊、決定去留

| 檔案 | 說明 | 建議 |
|---|---|---|
| `ocr/fill_prequiz.js` | 用 pre_tpl.docx 填課前考 Word（node docxtemplater） | **✅ 依 A.1 決定保留為唯一 Word 輸出層**；`to_zizi_word.py` 改成輸出 context 交給它，不再用 exam-to-word 範本 |
| `ocr/eval_ppocr.py` | 早期 PP-OCR vs Tesseract 的無 GT 評測 | 與 `eval_gt.py`（有 GT、更完整）重疊，建議以 eval_gt.py 為主，**eval_ppocr.py 可刪** |
| `ocr/get_models.py` | 用公開 PP-OCRv5／rapidocr 內建模型自給自足 | 與 `setup_env.sh` 的模型準備可能重疊，擇一 |
| `local-app/`：`/api/py-ocr` 端點＋按鈕、早期五分頁 index.html | 已被本地 session 三步版取代 | 五分頁版無需理會；`/api/py-ocr` 若與一鍵流程重疊可清掉 |
| `docs/卷土重來-創造邏輯拆解與我們的優化藍圖.md` | 拆解俊瑋方法的筆記 | 參考用，保留 |

### C. 雲端不碰、留給本地 session
任何需要 Mac／私密測試卷／主機的事：部署（Vercel＋主機 git pull）、主機環境、eval 迴圈、搬 iCloud、一鍵管線調校、切題規則（`to_zizi_word.py`／`ai_judge.py` owner 為本地）。雲端若要這些結果，從本文件或 main 取，不自行在雲端重做。

### D. 使用者已回覆（2026-10-03，兩題都定）
- **A.1 Word 範本：用 `pre_tpl.docx`（課前考）。** `to_zizi_word.py` 輸出改走它（經 `fill_prequiz.js`）。
- **A.2 Gemini：鬆綁。** 加到 `ai_judge.py`，送 Gemini 前先遮個資（預設）。

### E. 下一步（本地 session 主導）
1. `to_zizi_word.py` 的輸出端改接 `fill_prequiz.js`＋pre_tpl.docx（把 pre_tpl.docx 放進本 repo 或主機取得）；用 `eval_gt.py` 確認 Word 仍能開、版面對。
2. `ai_judge.py` 增加 Gemini 選項（遮個資後再送），和 Claude/Codex 並列。
3. 部署管線：Vercel deploy＋主機 git pull／自動更新；repo 搬出 iCloud、重啟 8787。
4. 逐顆 portal 按鈕在主機端到端測過，給主管測試。
