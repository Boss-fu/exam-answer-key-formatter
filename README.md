# exam-answer-key-formatter

國中理化／數學考卷「詳解＋簡答」自動產出工具。輸入考卷內容，輸出一份格式固定（有框線4欄合併表格、國中課綱內容限制、公式保留 `$...$` 供 MathType 轉換）的 Word (.docx) 檔案。

## 資料夾結構

```
docs/
  理化數學詳解與簡答產出規範.md   完整規則（人看的 SOP，一切以這份為準）
  理化數學詳解與簡答_使用說明.docx  操作說明（含 MathType Toggle TeX 截圖標註）
  gemini-content-prompt.md        給 Gemini 等其他 AI 產內容用的 CSV 輸出 prompt
skill/
  SKILL.md                        給 Claude 用的技能定義（觸發條件、步驟、驗證清單）
code/
  lib3.js                         docx-js 排版邏輯（4欄表格、gridSpan合併、字體、分頁）
  build_from_json.js              CLI：讀 JSON → 輸出 .docx，並自動核對答案一致性
  sample_input.json               範例輸入（7題光的折射，已驗證過排版與內容）
```

## 快速使用

```bash
cd code
npm install docx
node build_from_json.js sample_input.json output.docx
```

輸入 JSON schema 見 `code/build_from_json.js` 開頭註解。

## 規則異動流程

`docs/理化數學詳解與簡答產出規範.md` 是唯一權威版本。任何規則變動都要同步更新這份文件跟 `skill/SKILL.md`，兩者內容需保持一致。
