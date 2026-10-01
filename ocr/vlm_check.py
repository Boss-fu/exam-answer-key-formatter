#!/usr/bin/env python3
"""本地視覺模型（Ollama，完全在本機、不呼叫外部 API）當「第二雙眼睛」：
看原卷每一頁，列出題號與題目開頭，再跟 to_zizi_word 切出來的題目對照，抓漏題、黏題。
不需要正解，所以正解只收部分內容的考卷也能檢查。

用法：
    python3 ocr/vlm_check.py 考卷.pdf 我們的.docx [--model qwen3-vl:8b]
輸出：每一題「原卷看到 / 我們有沒有」，以及漏題、黏題清單。
速度：每頁切 4 塊、每塊約 20~50 秒（看 CPU 忙不忙），所以只拿來抽查，不放進每次轉檔。
"""
import argparse
import base64
import json
import re
import sys
import urllib.request

import pymupdf

OLLAMA = "http://127.0.0.1:11434/api/chat"
PROMPT = ("這是一張考卷的局部。請列出這張圖裡「題目開頭有出現」的每一題：題號，以及題幹開頭的前 10 個字。"
          "被切掉、只看到後半段的題目不要列。忽略手寫筆跡（學生答案、勾勾、計算）。"
          "只輸出 JSON 陣列，例如 [{\"no\":1,\"start\":\"...\"}]；沒有題目就輸出 []。")


def ask(png_bytes, model):
    body = {"model": model, "stream": False, "think": False, "options": {"temperature": 0},
            "messages": [{"role": "user", "content": PROMPT, "images": [base64.b64encode(png_bytes).decode()]}]}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    text = json.load(urllib.request.urlopen(req, timeout=900))["message"]["content"]
    m = re.search(r"\[.*\]", text, re.S)
    try:
        return json.loads(m.group(0)) if m else []
    except json.JSONDecodeError:
        return []


def page_tiles(page):
    """直式頁切上下兩半 × 左右兩半；橫式頁（一張印兩頁）切成 4 欄 × 上下兩半"""
    r = page.rect
    cols = 4 if r.width > 1.2 * r.height else 2
    for j in range(cols):
        for i in range(2):
            # 上下兩半有一點重疊，避免剛好切在題號上
            y0, y1 = (0, 0.55) if i == 0 else (0.45, 1)
            clip = pymupdf.Rect(r.width * j / cols, r.height * y0, r.width * (j + 1) / cols, r.height * y1)
            yield page.get_pixmap(dpi=110, clip=clip).tobytes("png")


def norm(t):
    return re.sub(r"[^0-9A-Za-z一-鿿]", "", t or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("docx")
    ap.add_argument("--model", default="qwen3-vl:8b")
    ap.add_argument("--pages", default="", help="只看某幾頁，例如 1,2")
    a = ap.parse_args()
    only = {int(x) for x in a.pages.split(",") if x}

    seen = {}
    for n, page in enumerate(pymupdf.open(a.pdf), 1):
        if only and n not in only:
            continue
        for png in page_tiles(page):
            for q in ask(png, a.model):
                try:
                    no = int(q.get("no"))
                except (TypeError, ValueError):
                    continue
                if no not in seen and norm(q.get("start")):
                    seen[no] = (n, q.get("start", ""))
        print(f"第 {n} 頁看完，累計 {len(seen)} 題", file=sys.stderr, flush=True)

    import docx
    d = docx.Document(a.docx)
    ours = [norm(p.text) for p in d.paragraphs if '<w:numId w:val="18"/>' in p._p.xml]
    missing, merged = [], []
    for no in sorted(seen):
        pg, start = seen[no]
        key = norm(start)[:6]
        hit = [i for i, t in enumerate(ours) if key and key in t]
        if not hit:
            missing.append((no, pg, start))
        elif all(ours[i].index(key) > 12 for i in hit):
            merged.append((no, pg, start))
    print(f"原卷看到 {len(seen)} 題（題號 {min(seen, default=0)}～{max(seen, default=0)}），我們切出 {len(ours)} 題")
    for no, pg, st in missing:
        print(f"  漏？ 第{no}題（p{pg}）{st}")
    for no, pg, st in merged:
        print(f"  黏？ 第{no}題（p{pg}）{st}：出現在別題的題幹中間")
    gaps = [i for i in range(1, max(seen, default=0) + 1) if i not in seen]
    if gaps:
        print(f"  （模型也沒看到的題號：{gaps}，可能被手寫蓋住或在圖裡）")


if __name__ == "__main__":
    main()
