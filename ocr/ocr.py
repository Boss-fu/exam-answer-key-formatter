#!/usr/bin/env python3
"""OCR 抽取：考卷圖片 → 結構化 JSON（引擎＝Claude 視覺）。

用法：
    python3 ocr.py 考卷p1.png 考卷p2.png -o out.json
    python3 ocr.py 考卷.pdf                 # PDF 也可（Claude 會讀）

原理：把 ocr/prompt.md 當指令、把圖片路徑一起交給本機 Claude Code
（claude -p ... --allowedTools Read），要它輸出 JSON。headless 模式一定要
開放 Read 工具，否則會停下來要權限、產不出東西。
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT_PATH = os.path.join(HERE, "prompt.md")


def load_prompt():
    with open(PROMPT_PATH, encoding="utf-8") as f:
        return f.read()


def extract_json(text):
    """從模型輸出裡把 JSON 抽出來（容忍 code fence 或前後雜訊）。"""
    if not text or not text.strip():
        raise ValueError("引擎沒有回傳內容")
    t = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t, re.I)
    if m:
        t = m.group(1).strip()
    s, e = t.find("{"), t.rfind("}")
    if s == -1 or e == -1:
        raise ValueError("輸出中找不到 JSON；前 300 字：" + text[:300])
    return json.loads(t[s:e + 1])


def run_ocr(image_paths, claude_bin=None, cwd=None, timeout=600):
    claude_bin = claude_bin or os.environ.get("CLAUDE_BIN", "claude")
    cwd = cwd or os.getcwd()
    abs_imgs = [os.path.abspath(p) for p in image_paths]
    for p in abs_imgs:
        if not os.path.exists(p):
            raise FileNotFoundError(f"找不到圖片：{p}")
    prompt = (
        load_prompt()
        + "\n\n========== 考卷圖片（請用讀取工具開啟這些檔案）==========\n"
        + "\n".join(abs_imgs)
    )
    proc = subprocess.run(
        [claude_bin, "-p", prompt, "--allowedTools", "Read"],
        cwd=cwd, capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0 and not proc.stdout:
        raise RuntimeError(f"Claude 結束碼 {proc.returncode}：{proc.stderr[:500]}")
    return extract_json(proc.stdout)


def main():
    ap = argparse.ArgumentParser(description="考卷圖片 → 結構化 JSON")
    ap.add_argument("images", nargs="+", help="考卷圖片/PDF（可多張＝多頁）")
    ap.add_argument("-o", "--output", help="輸出 JSON 路徑（不給則印到 stdout）")
    args = ap.parse_args()
    data = run_ocr(args.images)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        q = len(data.get("questions", []))
        print(f"✅ 已輸出 {args.output}（{q} 題）")
    else:
        print(text)


if __name__ == "__main__":
    sys.exit(main())
