#!/usr/bin/env python3
"""夜間把關：比較改動前後 27 份 --xlsx 結果。通過條件：總分不降、沒有任何一份降超過 0.5 個百分點、題組文章≥90% 不降。
    python tools/night/gate.py before.txt after.txt   → 印 PASS/FAIL 與摘要，結束碼 0＝通過"""
import re, sys
def load(p):
    rows, total, grp = {}, None, None
    for ln in open(p, encoding="utf-8"):
        m = re.match(r"\|\s*(\S+)\s*\|.*?\|\s*([\d.]+)%\s*\|", ln)
        if m and "考卷" not in ln: rows[m.group(1)] = float(m.group(2))
        m = re.search(r"平均字元正確率 ([\d.]+)%", ln)
        if m: total = float(m.group(1))
        m = re.search(r"'題組文章≥90%': (\d+)", ln)
        if m: grp = int(m.group(1))
    return rows, total, grp
b, a = load(sys.argv[1]), load(sys.argv[2])
drops = [f"{k} {b[0][k]}→{a[0].get(k, 0)}" for k in b[0] if a[0].get(k, 0) < b[0][k] - 0.5]
ok = a[1] is not None and b[1] is not None and a[1] >= b[1] and not drops and (a[2] or 0) >= (b[2] or 0)
print(("PASS" if ok else "FAIL") + f" 總分 {b[1]}→{a[1]} 題組≥90% {b[2]}→{a[2]}" + (" 下降：" + "、".join(drops) if drops else ""))
sys.exit(0 if ok else 1)
