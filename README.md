# exam-answer-key-formatter

國中理化／數學考卷「詳解＋簡答」自動產出工具。輸入考卷內容，輸出一份格式固定（有框線4欄合併表格、國中課綱內容限制、公式保留 `$...$` 供 MathType 轉換）的 Word (.docx) 檔案。

## 資料夾結構

| 位置 | 內容 |
|---|---|
| `ocr/figcrop/` | 切圖工具：自動裁切考卷的圖和表 |
| `ocr/`（`pyexam`、`to_zizi_word`、`to_qbank_xlsx`） | OCR：考卷轉題庫 Excel、輸出 Word |
| `ocr/dehand/`、`ocr/clean_pdf.py` | 清稿：去除手寫筆跡 |
| `local-app/` | 主機服務與詳解系統網頁 |
| `exam-to-word/` | 詳解 Word 輸出工具 |
| `tools/` | 維護與自動化腳本 |
| `docs/` | 所有說明文件（切圖請看 `docs/切圖交接.md`） |

## 快速使用

```bash
cd code
npm install docx
node build_from_json.js sample_input.json output.docx
```

輸入 JSON schema 見 `code/build_from_json.js` 開頭註解。

## 規則異動流程

`docs/理化數學詳解與簡答產出規範.md` 是唯一權威版本。任何規則變動都要同步更新這份文件跟 `skill/SKILL.md`，兩者內容需保持一致。
