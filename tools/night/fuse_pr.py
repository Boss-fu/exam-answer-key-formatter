#!/usr/bin/env python3
"""融合 r8＋俊瑋 v10 的筆跡機率：掃融合方式×門檻，在同一驗證集量 (抓到, 印刷誤標, 圖誤標)。
挑「抓到 ≥97% 時印刷誤標最低」的組合寫進 ocr/results/night/fuse_best.json。"""
import json, os, sys
sys.path.insert(0, "ocr/dehand"); sys.path.insert(0, "ocr")
import numpy as np, onnx, torch
import finetune_jw_mask as F
dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
held = {os.path.basename(os.path.dirname(p)) for p in open("ocr/dehand/bench_jw_pages.txt", encoding="utf8").read().split()}
pages = F.load("ocr/dehand/data/std", held, "ocr/dehand/data/std_qa.csv")
ve = set(json.load(open("ocr/models/jw/jwmask-r8.json", encoding="utf8"))["validation_exams"])
va = [(p, q) for p, q in ((p, F.read(p)) for p in pages if p["exam"] in ve) if q is not None][:40]
for p, _ in va: F.figs(p)
def mk(path):
    m = F.convert(onnx.load(path if path.endswith(".onnx") else F.JW)).to(dev)
    if path.endswith(".pt"):
        st = torch.load(path, map_location="cpu", weights_only=True); m.load_state_dict(st.get("state_dict", st))
    return m.eval()
r8, v10 = mk("ocr/dehand/runs/jw-mask-r8/dehand_jwmask.pt"), mk(F.JW)
class Fuse(torch.nn.Module):
    """輸出跟單一模型同格式（第 1 通道是 logit），score() 不用改。"""
    def __init__(self, how, w): super().__init__(); self.how, self.w = how, w
    def forward(self, x):
        a, b = r8(x), v10(x); pa, pb = torch.sigmoid(a[:, 1:2]), torch.sigmoid(b[:, 1:2])
        p = {"avg": self.w * pa + (1 - self.w) * pb, "max": torch.maximum(pa, pb),
             "geo": pa ** self.w * pb ** (1 - self.w)}[self.how].clamp(1e-6, 1 - 1e-6)
        return torch.cat([a[:, :1], torch.log(p / (1 - p))], 1)
ths = [.05, .08, .10, .12, .15, .2, .25, .3]
rows = []
with torch.no_grad():
    for how, w in [("single_r8", 1), ("avg", .7), ("avg", .5), ("geo", .7), ("geo", .5), ("max", 0)]:
        m = r8 if how == "single_r8" else Fuse(how, w)
        for t, (c, pf, ff) in zip(ths, F.score(m, va, dev, th=ths)):
            rows.append(dict(how=how, w=w, th=t, catch=c, print_fp=pf, fig_fp=ff))
            print(f"{how:9s} w={w:.1f} 門檻 {t:.2f}：抓到 {c:.1%} 印刷誤標 {pf:.2%} 圖誤標 {ff:.2%}", flush=True)
ok = [r for r in rows if r["catch"] >= .97]
best = min(ok, key=lambda r: r["print_fp"] + r["fig_fp"]) if ok else max(rows, key=lambda r: r["catch"] - 3 * (r["print_fp"] + r["fig_fp"]))
base = min((r for r in rows if r["how"] == "single_r8" and r["catch"] >= .97), key=lambda r: r["print_fp"], default=None)
json.dump({"best": best, "r8_at_97": base, "all": rows}, open("ocr/results/night/fuse_best.json", "w"), ensure_ascii=False, indent=1)
print("最佳", best, "\nr8 同抓到率", base)
