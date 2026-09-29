# OCR 資料格式 與 標準答案標註規範

OCR 輸出、與人工標註的「標準答案」，用**同一套 JSON schema**——這樣評測可以直接逐欄比對。

## 一份考卷 = 一個 JSON

```json
{
  "meta": {
    "school": "陽明國中",      // 看不清填 null
    "year": "113",            // 民國學年度
    "term": "上",             // 上 / 下
    "grade": "八",            // 七 / 八 / 九
    "subject": "自然",
    "examName": "第一次段考"
  },
  "questions": [
    {
      "no": 1,                 // 題號（整數）
      "type": "single",        // single/multiple/fill/essay/truefalse
      "section": "一、單選題",  // 所屬大題；沒有填 null
      "stem": "題幹文字（公式用 $...$）",
      "options": [             // 非選擇題用空陣列 []
        {"label": "A", "text": "…"},
        {"label": "B", "text": "…"},
        {"label": "C", "text": "…"},
        {"label": "D", "text": "…"}
      ],
      "answer": "B",           // 卷上有標準答案才填；沒有填 null
      "points": 2,             // 配分；沒印填 null
      "hasFigure": false,
      "figureNote": null,      // 有圖表時一句話描述
      "needsReview": false
    }
  ]
}
```

## 怎麼做「標準答案」測試資料（給團隊）

目標：挑 **10–20 份**有代表性的考卷（不同科目/年級/難度/掃描品質），每份做一個 `<名稱>.gt.json`。

1. 檔案放進 `ocr/testset/`：
   - `<名稱>.gt.json`（標準答案）
   - `<名稱>.png`（考卷圖；多頁用 `<名稱>.1.png`、`<名稱>.2.png`…；PDF 也可）
2. `.gt.json` 內容就照上面 schema，**照卷面逐題填正確值**：
   - `stem`、`options` 照原卷打字，公式用單一錢號 `$...$`。
   - `answer`：卷上有標準答案才填，否則 null（評測會自動跳過 null 的題不計答案分）。
   - 看不清無法確定的欄位，寧可標 `needsReview: true`，不要亂填（會拉低你自己的基準）。
3. 一份可以先用 `ocr.py` 跑出草稿，再人工修正成正確版，會比從零打快很多：
   ```bash
   python3 ocr/ocr.py testset/mock.png -o testset/mock.gt.json   # 產草稿
   # 然後人工核對修正 mock.gt.json
   ```

## 評測怎麼算分（看 eval.py）

- **題目偵測率**：對上的題數 ÷ 標準答案題數（漏抓、多抓都會反映）。
- **逐欄分數**：題幹/選項＝文字相似度；答案/類型/配分＝完全一致率。
- **綜合分數** = 偵測率 × 逐欄平均。改 prompt 後就看這個數字有沒有往上走。
