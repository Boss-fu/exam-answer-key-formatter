# 專案操作指引

- 製作或整理理化／數學考卷的簡答與詳解時，先完整閱讀 `exam-answer-key-formatter/SKILL.md`。
- 製作國文、社會的「學生詳解本」（黃金模擬考精簡版）時，先完整閱讀 `docs/學生詳解本規則_國文社會.md`；理化、數學仍照上一條的原規則，兩套並存。
- 修改本機應用（`local-app/`）、設定主機或處理自動更新前，先完整閱讀 `Codex維護與主機設定說明.md`、`local-app/README.md`、`local-app/HOST-SETUP.md`。
- 修改詳解系統（`local-app/public/answer.html`、`answer-key.js`、`ensemble.js`、`auth.js`、`quota.js`、Word 格式）前，先完整閱讀 `docs/詳解系統說明.md`；每次修改都要在同一個 commit 內：在 `local-app/public/answer-changelog.json` 最前面加一則白話更新公告，並更新 `docs/詳解系統說明.md` 對應段落。
- 優化本地 OCR（`ocr/` 的 pyexam／to_zizi_word／評測）前，先完整閱讀 `docs/OCR交接_Codex.md`（目標、限制、上線標準、做過什麼、待辦），並用 `bash ocr/setup_env.sh` 準備環境。
- 一律在 `main` 分支工作；開始前先 `git pull --ff-only`，禁止 force push、改寫已推送的歷史。
- 推送到 `main` 後，主機會在 5 分鐘內自動更新並重開服務；推送前務必先在本機測過。
- 程式請 clone 在 Dropbox／OneDrive 以外的資料夾，不要讓雲端硬碟同步 `.git`。
- 未經使用者明確要求，不自行推送、強制覆蓋遠端、刪除分支或丟棄本機修改。

## 測試／監督角色（Claude 與 Codex 共用）
- `.claude/agents/ocr-tester.md`：只測不改，跑 testset、逐份比較、親眼看原稿與 Word，回報通過／不通過。
- `.claude/agents/ocr-supervisor.md`：只審不改，推 main 前審查 diff（重複實作、評測誠信、學生資料、測試證據）。
- 在 Codex 跑：`ocr/codex_role.sh tester|supervisor|worker "任務"`，報告在 `ocr/results/codex/`。
- 在 Codex App 裡也可以直接說「照 .claude/agents/ocr-tester.md 的角色，驗證…」。
- 別的 AI 寫好詳解內容、要轉成茲茲 Word：照 `docs/詳解_別的AI寫內容轉Word.md`（轉換程式 `tools/詳解轉word.js`）。
