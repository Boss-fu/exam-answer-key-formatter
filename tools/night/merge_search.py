#!/usr/bin/env python3
"""從根本融合俊瑋 v10 與 r8：兩者同一個 U-Net（r8 是 v10 微調來的），直接在權重層級合成「單一模型」。
θ = v10 + α_段 ×（r8 − v10），分 4 段（淺層編碼 enc0-1、深層編碼 enc2-4、解碼 dec、輸出頭）各自找 α。
目標：抓到率 ≥97% 的門檻下，印刷誤標＋圖誤標最低（同一驗證集）。贏 r8 才輸出 ocr/dehand/runs/jw-merge/。"""
import copy, json, os, sys
sys.path.insert(0, "ocr/dehand"); sys.path.insert(0, "ocr")
import onnx, torch
import finetune_jw_mask as F
dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
held = {os.path.basename(os.path.dirname(p)) for p in open("ocr/dehand/bench_jw_pages.txt", encoding="utf8").read().split()}
pages = F.load("ocr/dehand/data/std", held, "ocr/dehand/data/std_qa.csv")
ve = set(json.load(open("ocr/models/jw/jwmask-r8.json", encoding="utf8"))["validation_exams"])
va = [(p, q) for p, q in ((p, F.read(p)) for p in pages if p["exam"] in ve) if q is not None][:40]
for p, _ in va: F.figs(p)
model = F.convert(onnx.load(F.JW)).to(dev).eval()
v10 = {k: v.clone() for k, v in model.state_dict().items()}
r8 = torch.load("ocr/dehand/runs/jw-mask-r8/dehand_jwmask.pt", map_location="cpu", weights_only=True); r8 = r8.get("state_dict", r8)
def seg(k):
    if k.startswith(("enc/0", "enc/1")): return "enc_low"
    if k.startswith("enc/"): return "enc_high"
    if k.startswith("dec/"): return "dec"
    return "head"
SEGS = ["enc_low", "enc_high", "dec", "head"]
THS = [.04, .06, .08, .10, .12, .15, .18, .22]
def evaluate(alpha):
    sd = {k: (v10[k] + alpha[seg(k)] * (r8[k].to(v10[k].device) - v10[k])) if v10[k].dtype.is_floating_point else v10[k] for k in v10}
    model.load_state_dict(sd)
    with torch.no_grad(): res = F.score(model, va, dev, th=THS)
    ok = [(pf + ff, t, c, pf, ff) for t, (c, pf, ff) in zip(THS, res) if c >= .97]
    best = min(ok) if ok else (9 + (1 - max(c for c, _, _ in res)), None, max(c for c, _, _ in res), None, None)
    print(f"α={alpha} → 誤標 {best[0]:.4f} 門檻 {best[1]} 抓到 {best[2]:.1%}", flush=True)
    return best, sd
alpha = {s: 1.0 for s in SEGS}
base, _ = evaluate(alpha); best, best_sd, best_a = base, None, dict(alpha)
for g in (.5, .75, 1.25):                      # 先整體插值
    a = {s: g for s in SEGS}; r, sd = evaluate(a)
    if r[0] < best[0]: best, best_sd, best_a = r, sd, a
for _ in range(2):                             # 再逐段座標搜尋
    for s in SEGS:
        for v in (0.0, .5, 1.0, 1.5):
            if v == best_a[s]: continue
            a = dict(best_a); a[s] = v; r, sd = evaluate(a)
            if r[0] < best[0] - 1e-4: best, best_sd, best_a = r, sd, a
print("r8：", base, "\n最佳：", best, best_a)
out = {"r8": base[:5], "best": best[:5], "alpha": best_a}
if best_sd is not None and best[0] < base[0]:
    os.makedirs("ocr/dehand/runs/jw-merge", exist_ok=True)
    torch.save(best_sd, "ocr/dehand/runs/jw-merge/dehand_jwmask.pt")
    model.load_state_dict(best_sd); m = model.cpu().eval()
    torch.onnx.export(m, torch.ones(1, 3, 512, 512), "ocr/dehand/runs/jw-merge/dehand_jwmask.onnx", input_names=["image"],
                      output_names=["out"], opset_version=17, dynamo=False,
                      dynamic_axes={"image": {0: "b", 2: "h", 3: "w"}, "out": {0: "b", 2: "h", 3: "w"}})
    out["saved"] = "ocr/dehand/runs/jw-merge/dehand_jwmask.onnx"
json.dump(out, open("ocr/results/night/merge_best.json", "w"), ensure_ascii=False, indent=1, default=str)
