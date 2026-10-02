#!/usr/bin/env python3
"""一鍵：掃描考卷 → 茲茲格式 Word。給 local-app 呼叫（local-app/py-ocr.js）。
    ① pyexam：轉正、清紅藍筆、分欄、PP-OCRv5、表格、附圖（Python，0 token）
    ② ai_judge：AI 只判斷一次題目邊界／錯字／雜訊（預設 Claude → Codex 接力，約 6k token）；全部失敗就跳過
    ③ to_zizi_word：組茲茲範本 Word＋確認清單（Python）
最後一行印出 JSON 摘要（local-app 讀這行）。

用法：python3 ocr/exam_to_word.py 考卷.pdf -o 輸出資料夾 [--dpi 350] [--ai auto|claude|codex|none]
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--dpi", type=int, default=350)
    ap.add_argument("--ai", default=os.environ.get("OCR_AI", "auto"), help="auto（Claude→Codex）、claude、codex、none")
    a = ap.parse_args()
    import pyexam
    import to_zizi_word

    t0 = time.time()
    r = pyexam.process(a.src, a.out, dpi=a.dpi)
    t_ocr = time.time() - t0
    ai_used, ai_note = None, ""
    if a.ai != "none":
        import ai_judge
        try:
            res = ai_judge.judge(a.out, a.ai)
            ai_used = res.get("_meta", {}).get("backend", a.ai)
        except Exception as e:                       # 額度用完、沒登入、沒網路：照樣出 Word，只是改用規則切題
            ai_note = str(e)[:200]
    t_ai = time.time() - t0 - t_ocr
    docx, review, st = to_zizi_word.build(a.out, os.path.join(a.out, "word"), dpi=a.dpi,
                                          src_name=os.path.basename(a.src), ai=a.ai if ai_used else None)
    if ai_note:
        with open(review, "a", encoding="utf-8") as f:
            f.write(f"・這次沒有用到 AI（{ai_note}），題目邊界改用規則判斷，請特別檢查題號\n")
    print("\n".join(r["report"]))
    print(json.dumps({"docx": docx, "review": review, "questions": st["questions"], "figures": st["figures"],
                      "tables": st["tables"], "ai": ai_used, "ai_error": ai_note,
                      "sec_ocr": round(t_ocr, 1), "sec_ai": round(t_ai, 1), "sec_total": round(time.time() - t0, 1)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
