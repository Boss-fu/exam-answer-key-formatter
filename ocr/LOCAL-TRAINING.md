# 本機考卷 OCR：資料、評測與自有模型訓練

## 現況

目前**尚未訓練自有模型權重**。正式本機 OCR 管線是
`ocr/exam_to_word.py` → `pyexam.py` → `ai_judge.py` → `to_zizi_word.py`：
`pyexam.py` 使用 PP-OCRv5 ONNX，`ai_judge.py` 只處理題目邊界的人工覆核輔助。
`local-app/ocr-preflight.js` 則維持為 Tesseract 的輕量預檢工具，不是訓練或正式 OCR 評測器。

## 第一階段：建立可信的資料與基準（已可執行）

1. `python3 ocr/dataset_catalog.py <國中段考卷資料夾> --out output/ocr-lab/catalog.json`
   - 只讀來源，依考試資料夾分組；跨資料夾的完全相同檔案也合組。
   - 固定分出 train / validation / test 候選，避免原稿與重製版跨組。
   - 檔名含「乾淨」或「答案」只算候選，`label_verified=false`；不會自動當作標準答案。
2. 先以 `~/.venvs/zizi-exam/bin/python ocr/eval_gt.py --tag <tag> --fresh` 建立開發集基準。
   - `eval_gt.py` 是主評測：量文字、閱讀順序、題數與 Word 是否可開啟；不可用訓練集成績宣稱驗收。
3. 修改切題規則後，用 `~/.venvs/zizi-exam/bin/python ocr/qcount.py <tag>` 快速比較題數。
   - 它讀取 `eval_gt.py` 的本機快取，不重跑 OCR；總誤差變小且沒有單卷退步才保留規則。
4. 需要 AI 協助判題目邊界時，使用 `ai_judge.py`（例如 `qcount.py <tag> --ai claude`）；AI 回傳的是候選，仍須人工核對。

OCR 文字信心不等於準確率；題目框、文字、圖表和 Word 都須由 `eval_gt.py` 與人工標註共同確認。
最後驗收只用未參與調整的保留集：`eval_gt.py --set holdout --tag final --fresh`。
`acceptance_audit.py` 讀取人工確認的驗收 JSON，額外阻擋 train/validation 樣本、未確認標註與缺少 Word 版面檢查的宣稱；它是 `eval_gt.py --set holdout` 的驗收證據檢核，不能取代評測本身。

## 第二階段：做出真正可訓練的標註集（下一步）

每頁至少人工確認：題號錨點與完整題框、題幹/選項、表格/圖片區域、要忽略的手寫區域，
以及可辨識印刷字的正確轉錄。自動預標可節省時間，但未核對的預標不得用於正式驗收。
同場考試若有可編輯 DOCX，可拿來預填文字標註，但需逐題和原始掃描卷核對；
DOCX 本身也可能有錯字、漏題或特殊字型導致的顯示問題。
可先執行 `python3 ocr/docx_question_candidates.py <清稿.docx> --out output/ocr-lab/<名稱>-docx-candidates.json`，
查看 `missing_numbers` 與 `verified=false` 的候選；這個檔案不能直接餵給正式訓練或當驗收標準。
相同考試的原稿、教師版、清稿、Word 都留在同一 split；另查近似複本，防止資料洩漏。

## 第三階段：訓練模型（資料標註後才啟動）

優先微調「題目起點與題框/圖表區域」的版面模型，因為目前主要錯誤是漏題、誤把圖內數字當題號、
圖表內容混入題幹。若獨立量測發現印刷字辨識本身是主要瓶頸，再用人工核對的文字裁圖微調辨識模型。
去筆跡模型最後才訓練，且必須檢查是否誤刪印刷文字或原圖。

所有版本都必須在固定 test 組比較題號召回、誤報、逐字錯誤、圖表歸屬、筆跡殘留、Word 可用性與處理時間；
只保留不退步且能重現的版本。不能以「一直跑」取代人工標準答案，也不能只看訓練資料上的成績。

## 資料保護

原始 Google Drive 資料夾唯讀。私人考卷、頁圖、標註與模型輸出放在 repo 根目錄的 `output/`，已被 `.gitignore` 排除；
未經使用者明確要求，不上傳雲端 AI 或 GitHub。
