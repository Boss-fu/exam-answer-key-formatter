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


def progress(out_dir, started, stage, msg, page=0, pages=0, img=None):
    """畫面回報是附加功能，任何寫檔問題都不可影響轉檔。"""
    if os.environ.get("OCR_PROGRESS") != "1":
        return
    try:
        event = {"t": round(time.time() - started, 1), "stage": stage,
                 "page": page, "pages": pages, "msg": msg, "img": img}
        with open(os.path.join(out_dir, "progress.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main():
    # Windows 預設 cp950：罕見字會讓 print 當掉，錯誤訊息到網頁也會變亂碼；local-app 一律用 UTF-8 解讀
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--dpi", type=int, default=350)
    ap.add_argument("--ai", default=os.environ.get("OCR_AI", "auto"), help="auto（Claude→Codex）、claude、codex、none")
    a = ap.parse_args()
    import pyexam
    import to_zizi_word

    t0 = time.time()
    try:
        r = pyexam.process(a.src, a.out, dpi=a.dpi)
        t_ocr = time.time() - t0
        if os.environ.get("OCR_SKIP_WORD") == "1":   # 只要題庫 Excel：Excel 之後由文字結果另外產生，不需要 Word 與 AI 判題
            print(chr(10).join(r["report"]))
            progress(a.out, t0, "done", "處理完成")
            print(json.dumps({"docx": None, "review": None, "questions": None, "figures": None, "tables": None,
                              "ai": None, "ai_error": "", "sec_ocr": round(t_ocr, 1), "sec_ai": 0,
                              "sec_total": round(time.time() - t0, 1)}, ensure_ascii=False))
            return
        ai_used, ai_note = None, ""
        if a.ai != "none":
            progress(a.out, t0, "ai", "正在判斷題目")
            import ai_judge
            try:
                res = ai_judge.judge(a.out, a.ai)
                ai_used = res.get("_meta", {}).get("backend", a.ai)
            except Exception as e:                   # 額度用完、沒登入、沒網路：照樣出 Word，只是改用規則切題
                ai_note = str(e)[:200]
            progress(a.out, t0, "ai", "題目判斷完成")
        t_ai = time.time() - t0 - t_ocr
        progress(a.out, t0, "word", "正在組成 Word")
        docx, review, st = to_zizi_word.build(a.out, os.path.join(a.out, "word"), dpi=a.dpi,
                                              src_name=os.path.basename(a.src), ai=a.ai if ai_used else None)
        progress(a.out, t0, "word", "Word 已組成")
    except Exception as e:
        progress(a.out, t0, "error", str(e)[:200])
        raise
    if ai_note:
        with open(review, "a", encoding="utf-8") as f:
            f.write(f"・這次沒有用到 AI（{ai_note}），題目邊界改用規則判斷，請特別檢查題號\n")
    print("\n".join(r["report"]))
    progress(a.out, t0, "done", "處理完成")
    print(json.dumps({"docx": docx, "review": review, "questions": st["questions"], "figures": st["figures"],
                      "tables": st["tables"], "ai": ai_used, "ai_error": ai_note,
                      "sec_ocr": round(t_ocr, 1), "sec_ai": round(t_ai, 1), "sec_total": round(time.time() - t0, 1)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
