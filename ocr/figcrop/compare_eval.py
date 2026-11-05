#!/usr/bin/env python3
"""逐張比較兩次 gt_eval：閘門只看總分，這裡列出哪幾張變好、變差、題號改變，並依卷別統計。

python ocr/figcrop/compare_eval.py before.json after.json
"""
import json
import sys


def load(path):
    data = json.load(open(path, encoding="utf-8"))
    rows = {(r["exam"], r["page"], r["file"]): r for r in data["rows"] if r.get("gt")}
    return data, rows


def main():
    (_, before), (after_data, after) = load(sys.argv[1]), load(sys.argv[2])
    print(after_data["summary"])
    print("合格", sum(r["hit"] for r in before.values()), "→", sum(r["hit"] for r in after.values()),
          "／題號正確", sum(bool(r.get("qno_ok")) for r in before.values()), "→", sum(bool(r.get("qno_ok")) for r in after.values()),
          "／框有變", sum(1 for k, r in after.items() if k in before and before[k]["auto"] != r["auto"]))
    for k, r in after.items():
        o = before.get(k)
        if o is None:
            print("新增", k[0], "p", k[1], "Q", r["qno"], round(r["iou"], 2))
        elif o["hit"] and not r["hit"]:
            print("退", k[0], "p", k[1], "Q", r["qno"], "手動", r["gt"], "舊", o["auto"], "新", r["auto"], round(r["iou"], 2))
        elif r["hit"] and not o["hit"]:
            print("進", k[0], "p", k[1], "Q", r["qno"], round(o["iou"], 2), "→", round(r["iou"], 2))
        elif o.get("qno_ok") and not r.get("qno_ok") and r["hit"]:
            print("題號對→錯", k[0], "Q", r["qno"], "猜", o["auto_qno"], "→", r["auto_qno"])
    per = {}
    for r in after.values():
        per.setdefault(r["exam"], []).append(r["hit"])
    for exam, hits in sorted(per.items()):
        print(f"{exam:<24} {sum(hits):>3}/{len(hits):<3} {sum(hits) / len(hits):.1%}")


if __name__ == "__main__":
    main()
