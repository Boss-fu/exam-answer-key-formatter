#!/usr/bin/env python3
"""去筆跡：同一驗證集（r8 的 validation_exams 前 40 頁）量模型在各門檻的 (筆跡抓到率, 印刷誤標率, 圖誤標率)。
    python tools/night/dehand_pr.py <模型 .pt 或 .onnx>"""
import json, os, sys
sys.path.insert(0, "ocr/dehand"); sys.path.insert(0, "ocr")
import onnx, torch
import finetune_jw_mask as F
dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
held = {os.path.basename(os.path.dirname(p)) for p in open("ocr/dehand/bench_jw_pages.txt", encoding="utf8").read().split()}
pages = F.load("ocr/dehand/data/std", held, "ocr/dehand/data/std_qa.csv")
ve = set(json.load(open("ocr/models/jw/jwmask-r8.json", encoding="utf8"))["validation_exams"])
va = [(p, q) for p, q in ((p, F.read(p)) for p in pages if p["exam"] in ve) if q is not None][:40]
for p, _ in va: F.figs(p)
m = sys.argv[1]
model = F.convert(onnx.load(m if m.endswith(".onnx") else F.JW)).to(dev)
if m.endswith(".pt"):
    st = torch.load(m, map_location="cpu", weights_only=True); model.load_state_dict(st.get("state_dict", st))
model.eval()
ths = [.02, .05, .08, .12, .18, .25, .35, .5]
with torch.no_grad():
    res = F.score(model, va, dev, th=ths)
for t, (c, pf, ff) in zip(ths, res):
    print(f"門檻 {t:.2f}：筆跡抓到 {c:.1%}  印刷誤標 {pf:.2%}  圖誤標 {ff:.2%}")
