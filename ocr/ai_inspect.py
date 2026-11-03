#!/usr/bin/env python3
"""題庫 Excel 最後一道：AI 對照乾淨卷頁面圖，逐題校正草稿（題目、題組文章、題號），目標 99%。

輸入是 to_qbank_xlsx 的草稿列與 pyexam 的 previews/p*_clean.jpg；AI（預設 Gemini Flash，經 agy、走使用者的
Google AI Pro 訂閱）看圖回傳「整份考卷的完整題目清單」JSON，程式用它覆寫草稿的題目／題組文章，題號缺的補、多的刪。
AI 只看圖與草稿、只回 JSON，不執行任何工具。
    python ocr/ai_inspect.py <pyexam 輸出資料夾> [-o 輸出.xlsx]
"""
import argparse, glob, json, os, re, shutil, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import to_qbank_xlsx as X  # noqa: E402

MODEL = os.environ.get("AI_INSPECT_MODEL", "gemini-3.8-flash-high")

RULES = """你是題庫建檔的最後校對。附圖是一份國中考卷（已清除學生筆跡）的每一頁，依頁序排列。
下面 JSON 是 OCR 自動產生的題庫草稿，可能有：錯字、題號錯位、漏題或多切一題、題幹混進圖內文字／刻度／表格內容、
頁首頁尾（校名、頁碼、「下頁還有試題」）、學生手寫答案、選項漏掉或併在一起。
請逐題對照圖片，輸出「整份考卷正確的完整題目清單」，規則：
1. 題號：用卷面印的題號；大題換段從 1 重編時接續前面編號（例如選擇題 35 題後，題組題 1 → 36）。題組小題 (1)(2)(3) 各算一題。
2. 題目：題幹＋換行＋每個選項一行，例如 "下列何者正確？\\n(A)甲\\n(B)乙\\n(C)丙\\n(D)丁"。不含題號、作答括號、學生答案。
   題目有附圖或表格時，在題幹最後加「〔含圖〕」；圖內的文字、刻度、表格內容不要抄進題目。
   是非題的選項寫成 "(A)正確\\n(B)錯誤"。化學式、單位照卷面字元（例如 CO₂、mL、°C）。
3. 題組文章：題組的共同引言（例如「附圖為…，回答第 29~32 題」後面的說明文字）放這裡，同題組每一題都填同一段；不是題組就留空。
4. 逐字照卷面，不要改寫、不要補充卷面沒有的字。看不清楚的字保留草稿的字。
只輸出 JSON 陣列，每個元素 {"題號": "1", "題目": "...", "題組文章": "..."}，不要其他文字。
"""


def pages(pyexam_dir):
    ims = glob.glob(os.path.join(pyexam_dir, "previews", "p*_clean.jpg"))
    return sorted(ims, key=lambda p: int(re.search(r"p(\d+)_clean", p).group(1)))


def ask(images, draft, timeout=600):
    """把頁面圖複製到暫存資料夾、以 @檔名 交給 agy；回傳解析好的 list。"""
    with tempfile.TemporaryDirectory() as tmp:
        refs = []
        for i, p in enumerate(images, 1):
            dst = os.path.join(tmp, f"page{i}.jpg"); shutil.copy(p, dst); refs.append(f"@page{i}.jpg")
        prompt = RULES + "\n頁面圖：" + " ".join(refs) + "\n\n草稿：\n" + json.dumps(draft, ensure_ascii=False) + \
            "\n\n只根據上面的圖片與草稿回答，不要使用任何工具。"
        r = subprocess.run(["agy", "--model", MODEL, "--print-timeout", f"{timeout}s", "-p", prompt],
                           cwd=tmp, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout + 60)
    out = r.stdout
    a, b = out.find("["), out.rfind("]")
    if a < 0 or b <= a:
        raise RuntimeError("AI 沒有回 JSON：" + (out or r.stderr)[-400:])
    return json.loads(out[a:b + 1], strict=False)      # AI 常在字串裡直接換行；寬鬆解析


def inspect(pyexam_dir, name, rows):
    draft = [{"題號": r["題號"], "題目": r["題目"], "題組文章": r["題組文章"]} for r in rows]
    fixed = ask(pages(pyexam_dir), draft)
    base = {k: rows[0][k] for k in ("年度", "來源學校", "考試名稱")} if rows else X.exam_info(name)
    old = {r["題號"]: r for r in rows}
    gid_prefix = X.exam_info(name)["gid"]
    out, first = [], {}
    for it in fixed:
        q = str(it.get("題號", "")).strip()
        if not q.isdigit():
            continue
        r = dict(old.get(q) or {**{k: base.get(k, "") for k in ("年度", "來源學校", "考試名稱")}, "題號": q, "圖表類型": "", "判斷依據": "AI 補的題", "待確認": "Y"})
        r["題目"] = it.get("題目") or r.get("題目", "")
        r["題組文章"] = it.get("題組文章") or ""
        if r["題組文章"]:
            first.setdefault(r["題組文章"], q); r["題組ID"] = f"{gid_prefix}-{first[r['題組文章']]}" if gid_prefix else ""
        else:
            r["題組ID"] = ""
        if "〔含圖〕" in r["題目"] and r.get("圖表類型") == "無":
            r["圖表類型"] = ""; r["待確認"] = "Y"
        out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("dir"); ap.add_argument("-o"); ap.add_argument("--name"); a = ap.parse_args()
    name = a.name or os.path.basename(a.dir.rstrip("/"))
    rows = inspect(a.dir, name, X.rows(a.dir, name))
    out = a.o or os.path.join(a.dir, name + "_AI校對.xlsx"); X.write(rows, out)
    print(f"{out}：{len(rows)} 題")


if __name__ == "__main__":
    main()
