#!/usr/bin/env python3
"""AI 校對後的 Excel 跟正解 Excel 同題號比對。  python ocr/ai_eval.py <AI xlsx 資料夾> [-v]"""
import glob, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qbank_eval as E

d = sys.argv[1]; v = "-v" in sys.argv; gold = os.path.expanduser("~/Desktop/待匯入")
accs = []
for f in sorted(glob.glob(os.path.join(d, "*_題目.xlsx"))):
    n = os.path.basename(f)[:-5]; g = E.gold_rows(os.path.join(gold, n + ".xlsx")); o = E.gold_rows(f)
    acc = {q: 1 - E.cer(E.norm(x["題目"]), E.norm(o.get(q, {}).get("題目", ""))) for q, x in g.items()}
    accs += acc.values()
    print(f"| {n[:12]} | {len(g)} | {len(o)} | {sum(acc.values()) / len(acc):.1%} | {sum(a >= .95 for a in acc.values())} |")
    if v:
        for q, a in acc.items():
            if a < .95: print(f"    {q} {a:.0%} 正:{E.norm(g[q]['題目'])[:50]}\n           AI:{E.norm(o.get(q, {}).get('題目', ''))[:50]}")
print(f"**AI 校對合計**：{len(accs)} 題；平均 {sum(accs) / max(1, len(accs)):.1%}；≥95% {sum(a >= .95 for a in accs)}／{len(accs)}；≥99% {sum(a >= .99 for a in accs)}")
