#!/usr/bin/env python3
"""截圖任務驗收：比較兩份 gt_eval.json 的 summary（使用者手動截圖標準答案）。
通過＝召回、平均 IoU、題號一致率都不降，多框不增，且至少一項變好。
    python tools/night/gate_figcrop.py before.json after.json"""
import json, sys
b, a = (json.load(open(p))["summary"] for p in sys.argv[1:3])
keys = [("recall_iou80", 1), ("mean_iou", 1), ("qno_accuracy", 1), ("extras", -1)]
worse = [k for k, s in keys if (a[k] - b[k]) * s < -1e-9]
better = [k for k, s in keys if (a[k] - b[k]) * s > 1e-9]
ok = a.get("manual") == b.get("manual") and not worse and better
fmt = " ".join(f"{k} {b[k]}→{a[k]}" for k, _ in keys)
print(("PASS " if ok else "FAIL ") + fmt + (f"（變差：{'、'.join(worse)}）" if worse else ""))
sys.exit(0 if ok else 1)
