# 專案操作指引

- 製作或整理理化／數學考卷的簡答與詳解時，先完整閱讀 `exam-answer-key-formatter/SKILL.md`。
- 製作國文、社會的「學生詳解本」（黃金模擬考精簡版）時，先完整閱讀 `docs/學生詳解本規則_國文社會.md`；理化、數學仍照上一條的原規則，兩套並存。
- 修改本機應用（`local-app/`）、設定主機或處理自動更新前，先完整閱讀 `Codex維護與主機設定說明.md`、`local-app/README.md`、`local-app/HOST-SETUP.md`。
- 優化本地 OCR（`ocr/` 的 pyexam／to_zizi_word／評測）前，先完整閱讀 `docs/OCR交接_Codex.md`（目標、限制、上線標準、做過什麼、待辦），並用 `bash ocr/setup_env.sh` 準備環境。
- 一律在 `main` 分支工作；開始前先 `git pull --ff-only`，禁止 force push、改寫已推送的歷史。
- 推送到 `main` 後，主機會在 5 分鐘內自動更新並重開服務；推送前務必先在本機測過。
- 程式請 clone 在 Dropbox／OneDrive 以外的資料夾，不要讓雲端硬碟同步 `.git`。
- 未經使用者明確要求，不自行推送、強制覆蓋遠端、刪除分支或丟棄本機修改。
