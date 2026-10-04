#!/usr/bin/env python3
"""列出 manifest 中誤收的印刷答案頁；只有 --apply 才刪除頁面資料與紀錄。"""
import argparse
import json
import os
import re


ANSWER_ORIG = re.compile(r"解答|答案|詳解")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="ocr/dehand/data")
    ap.add_argument("--apply", action="store_true", help="實際刪除列出的頁面資料與 manifest 紀錄")
    a = ap.parse_args()
    path = os.path.join(a.data, "manifest.json")
    if not os.path.exists(path):
        print(f"找不到 {path}")
        return 1
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    bad = [m for m in manifest if ANSWER_ORIG.search(m.get("orig", ""))]
    folders = sorted({m["pair"] for m in bad})
    for m in bad:
        print(f"{'刪除' if a.apply else '會刪除'}：{m['pair']}/p{m['page']}（原稿 {m['orig']}）")
    for pair in folders:
        print(f"{'清理' if a.apply else '會清理'}資料夾：{os.path.join(a.data, pair)}")
    print(f"{'刪除' if a.apply else '會刪除'} {len(bad)} 頁，涉及 {len(folders)} 個資料夾")
    if not a.apply:
        return 0
    for m in bad:
        base = os.path.join(a.data, m["pair"], f"p{m['page']}")
        for suffix in ("_x.png", "_m.png"):
            file = base + suffix
            if os.path.exists(file):
                os.remove(file)
    for pair in folders:
        directory = os.path.join(a.data, pair)
        if os.path.isdir(directory) and not os.listdir(directory):
            os.rmdir(directory)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([m for m in manifest if m not in bad], f, ensure_ascii=False, indent=1)
        f.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
