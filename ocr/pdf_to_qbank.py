#!/usr/bin/env python3
"""一鍵：考卷 PDF → 題庫匯入 Excel（OCR＋去筆跡 → 切題草稿 → AI 看卷面圖校對）。

    python ocr/pdf_to_qbank.py ~/Desktop/待匯入/112_南市復興_國七上二段_生物_題目.pdf [--no-ai] [-o 輸出.xlsx]
輸出預設放在 PDF 旁邊：<卷名>_題庫.xlsx。檔名照「年度_學校_國七上二段_科目_題目」命名，考試名稱等欄位才會自動帶對。
"""
import argparse, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("pdf"); ap.add_argument("-o"); ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--work", help="pyexam 中間檔資料夾（預設暫存，跑完刪掉）"); a = ap.parse_args()
    pdf = os.path.abspath(os.path.expanduser(a.pdf)); name = os.path.splitext(os.path.basename(pdf))[0]
    out = a.o or os.path.join(os.path.dirname(pdf), name + "_題庫.xlsx")
    tmp = None if a.work else tempfile.TemporaryDirectory()
    work = os.path.join(a.work or tmp.name, name); os.makedirs(work, exist_ok=True)
    env = dict(os.environ); env.setdefault("DEHAND_I2I_OUT", "mask")      # OCR 用 v10 mask（27 份 Excel 驗收最好）
    print("1/3 OCR＋去筆跡…", flush=True)
    subprocess.run([sys.executable, os.path.join(HERE, "pyexam.py"), pdf, "-o", work], check=True, env=env,
                   stdout=subprocess.DEVNULL)
    import to_qbank_xlsx as X
    print("2/3 切題、產生草稿…", flush=True)
    rows = X.rows(work, name)
    if not a.no_ai:
        print("3/3 AI 對照卷面校對…", flush=True)
        import ai_inspect
        try:
            rows = ai_inspect.inspect(work, name, rows)
        except Exception as e:                       # AI 失敗就交草稿，並在每題標待確認
            print("AI 校對失敗，改交草稿：", e)
            for r in rows:
                r["待確認"] = "Y"; r["判斷依據"] = ("AI 校對失敗；" + r.get("判斷依據", "")).strip("；")
    X.write(rows, out)
    print(f"完成：{out}（{len(rows)} 題，待確認 {sum(r.get('待確認') == 'Y' for r in rows)} 題）")
    if tmp:
        tmp.cleanup()


if __name__ == "__main__":
    main()
