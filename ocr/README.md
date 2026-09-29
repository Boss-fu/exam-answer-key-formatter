# OCR 系統（考卷圖 → 結構化 JSON）

引擎＝**Claude 視覺**。把考卷圖片交給 Claude，抽成一份結構化 JSON（題號/題幹/選項/答案/配分/圖），
一次涵蓋三種下游用途：**接 qbank 題庫、接詳解生成、接空白考卷/清稿**。

準確度靠一個**評測迴圈**顧（不是傳統模型訓練）：跑 OCR → 跟標準答案比對 → 算準確率 → 改 `prompt.md` → 再跑。

## 檔案

| 檔案 | 作用 |
|---|---|
| `prompt.md` | **大腦**：抽取提示詞。改這份＝改抽取邏輯 |
| `ocr.py` | 圖片 → JSON（呼叫 `claude -p ... --allowedTools Read`） |
| `eval.py` | 一份 OCR 輸出 vs 標準答案 → 逐欄準確率＋錯誤明細 |
| `run_eval.py` | 批次：跑整個 `testset/`、彙總平均準確率 → `results/summary.json` |
| `schema.md` | 資料格式＋「標準答案」標註規範 |
| `testset/` | 放測試資料（`<名稱>.gt.json` ＋ 考卷圖） |
| `results/` | 評測產出（已被 .gitignore 忽略） |

## 快速開始

```bash
# 1) 單份抽取
python3 ocr/ocr.py 某考卷.png -o out.json

# 2) 準備測試資料：挑 10-20 份考卷放 ocr/testset/，每份一個 <名稱>.gt.json（見 schema.md）
#    小技巧：先用 ocr.py 產草稿，再人工修正成標準答案

# 3) 批次評測，看準確率
python3 ocr/run_eval.py
```

## 需求

- 本機已安裝並登入 **Claude Code**（`claude`）。
- Python 3（只用標準函式庫，`eval` 不需裝套件）。
- PDF/圖片皆可；Claude 直接讀圖，不需額外 OCR 套件。

## 用「評測/訓練子代理」自動迭代

repo 內附一個子代理 `.claude/agents/ocr-trainer.md`。在這個 repo 開 Claude Code，叫它：

> 用 ocr-trainer 跑一輪：評測目前準確率，找出最常見的錯誤類型，提出並套用 `prompt.md` 的改進，再跑一次看有沒有進步。

它會跑 `run_eval.py`、讀 `results/summary.json` 的錯誤、改 `prompt.md`、重跑比較，**不改壞就保留、改壞就回退**。這就是「測試並訓練」這個 OCR agent 的實際做法。

## 現況與限制

- 乾淨、印刷體的考卷準確度很高；**手寫、糊掉、複雜表格/圖** 會拉低分數——這正是要靠測試集＋迭代改善的地方。
- `answer` 只有卷面有標準答案時才抽；要當「題庫答案」來源時，答案仍建議人工複核。
