#!/usr/bin/env python3
"""截圖任務驗收：比較兩份 gt_eval.json 的 summary（使用者手動截圖標準答案）。
通過＝召回、平均 IoU、題號正確數都不降，多框不增，且至少一項變好。
題號看「正確張數」不看比例：比例的分母是合格張數，多命中一張題號錯的圖比例就會降，等於懲罰召回變好
（2026-11-06 監督方同意更改）；比例照樣印出來參考。
    python tools/night/gate_figcrop.py before.json after.json"""
import json, sys


def load(path):
    data = json.load(open(path, encoding="utf-8"))
    s = dict(data["summary"])
    s["qno_correct"] = sum(1 for r in data["rows"] if r.get("hit") and r.get("qno_ok"))
    return s


b, a = (load(p) for p in sys.argv[1:3])
keys = [("recall_iou80", 1), ("mean_iou", 1), ("qno_correct", 1), ("extras", -1)]
worse = [k for k, s in keys if (a[k] - b[k]) * s < -1e-9]
better = [k for k, s in keys if (a[k] - b[k]) * s > 1e-9]
ok = a.get("manual") == b.get("manual") and not worse and better
fmt = " ".join(f"{k} {b[k]}→{a[k]}" for k, _ in keys) + f"（qno_accuracy {b['qno_accuracy']}→{a['qno_accuracy']}）"
print(("PASS " if ok else "FAIL ") + fmt + (f"（變差：{'、'.join(worse)}）" if worse else ""))
sys.exit(0 if ok else 1)
