#!/usr/bin/env python3
"""訓練我們自己的去筆跡模型（小型 U-Net，Mac 用 MPS 顯示卡、沒有就 CPU）。

資料：pairs_build.py 產生的 <data>/<配對>/p<頁>_x.png（原稿灰階）＋ p<頁>_m.png（筆跡遮罩）；
     人工修正的頁（之後的修正工具輸出到 <data>/_manual/）一樣格式，權重加倍。
切分：依「考試」（manifest 的 group）切訓練／驗證，同一場考試不會同時出現在兩邊。
損失：加權 BCE＋Dice。「印刷字上被判成筆跡」的懲罰 ×PRINT_W：寧可少擦，不可擦到印刷。
門檻：驗證集上掃 0.30~0.95，取「印刷誤擦 < 0.5%」之下召回最高的門檻。
輸出：ocr/models/dehand.onnx、ocr/models/dehand.json（門檻、召回、印刷誤擦、驗證組）。

評分（跟卷土重來舊版同一套）：
    召回 recall      ＝筆跡像素被判成筆跡的比例（越高越好；卷土重來 v1：36.2%）
    印刷誤擦 erase   ＝印刷墨跡像素被判成筆跡的比例（越低越好；卷土重來 v1：0.49%）

用法：python3 ocr/dehand/train.py --data ocr/dehand/data --epochs 30
"""
import argparse
import glob
import json
import os
import random
import re
import sys
import time

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
MODELS = os.path.join(os.path.dirname(HERE), "models")
PATCH = 256
PRINT_W = 3.0
MAX_ERASE = 0.005


# ---------------------------------------------------------------- 模型
def block(i, o):
    return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True),
                         nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True))


class UNet(nn.Module):
    def __init__(self, ch=(16, 32, 64, 128)):
        super().__init__()
        self.downs = nn.ModuleList()
        c = 1
        for o in ch:
            self.downs.append(block(c, o))
            c = o
        self.mid = block(ch[-1], ch[-1] * 2)
        self.ups, self.decs = nn.ModuleList(), nn.ModuleList()
        c = ch[-1] * 2
        for o in reversed(ch):
            self.ups.append(nn.ConvTranspose2d(c, o, 2, stride=2))
            self.decs.append(block(o * 2, o))
            c = o
        self.head = nn.Conv2d(ch[0], 1, 1)

    def forward(self, x):
        skips = []
        for d in self.downs:
            x = d(x)
            skips.append(x)
            x = F.max_pool2d(x, 2)
        x = self.mid(x)
        for up, dec, s in zip(self.ups, self.decs, reversed(skips)):
            x = up(x)
            x = dec(torch.cat([x, s], 1))
        return self.head(x)            # logits


# ---------------------------------------------------------------- 資料
from pairs_build import ink  # noqa: E402  墨跡判定跟產生資料時同一套


def load_pages(data):
    man = json.load(open(os.path.join(data, "manifest.json"), encoding="utf-8")) if os.path.exists(
        os.path.join(data, "manifest.json")) else []
    group = {(m["pair"], str(m["page"])): m["group"] for m in man}
    mp = os.path.join(data, "_manual", "manifest.json")
    manual = json.load(open(mp, encoding="utf-8")) if os.path.exists(mp) else {}
    pages = []
    sources = [("real", os.path.join(data, "*", "*_x.png")),
               ("manual", os.path.join(data, "_manual", "*_x.png")),
               ("synth", os.path.join(data, "_synth", "*", "*_x.png"))]
    synth_path = os.path.join(data, "_synth", "manifest.json")
    synth = json.load(open(synth_path, encoding="utf-8")) if os.path.exists(synth_path) else []
    synth_group = {(m["pair"], str(m["page"])): m["group"] for m in synth}
    for source, pat in sources:
      for x in sorted(glob.glob(pat)):
        if source == "real" and os.path.basename(os.path.dirname(x)) in ("_manual", "_synth", "_strokes"):
            continue                             # 人工/合成頁由各自的來源讀，不重複
        m = x[:-6] + "_m.png"
        if not os.path.exists(m):
            continue
        pair = os.path.basename(os.path.dirname(x))
        stem = os.path.basename(x)[:-6]
        if source == "manual":                    # 人工修正頁：<檔名>_p<頁>，同一份考卷同一組
            grp = manual.get(stem, "manual:" + re.sub(r"_p\d+$", "", stem))   # 跟自動配對同一份考卷＝同一組
        elif source == "synth":
            grp = synth_group.get((pair, stem), "synth:" + pair)
        else:
            grp = group.get((pair, stem[1:]), pair)
        img, msk = cv2.imread(x, cv2.IMREAD_GRAYSCALE), cv2.imread(m, cv2.IMREAD_GRAYSCALE) > 127
        near = cv2.dilate(msk.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        prt = ink(img) & ~near                   # 印刷墨跡；筆跡周圍 2px 是模糊帶（筆跡壓到印刷、遮罩邊緣），不算印刷也不算筆跡
        lab = msk.astype(np.uint8) | (prt.astype(np.uint8) << 1)     # bit0＝筆跡、bit1＝印刷（省記憶體：一頁一張）
        qp = x[:-6] + "_q.png"
        if os.path.exists(qp):                   # 題號區（合成時記錄）打包進 lab 的 bit2，不另佔記憶體
            lab |= ((cv2.imread(qp, cv2.IMREAD_GRAYSCALE) > 127) & ((lab & 1) == 0)).astype(np.uint8) << 2
        pages.append({"x": img, "lab": lab, "nhand": int(msk.sum()), "group": grp, "hasq": os.path.exists(qp),
                      "w": 2.0 if source == "manual" else (.7 if source == "synth" else 1.0),
                      "source": source, "name": f"{pair}/{stem}"})
    return pages


def sample(page, rng):
    """七成挑有筆跡的位置；加縮放、明暗、雜訊、模糊（模擬手機照片、淡掃描）"""
    x, lab = page["x"], page["lab"]
    m = lab & 1
    H, W = x.shape
    s = rng.uniform(0.8, 1.25)
    ps = int(PATCH / s)
    ps = min(ps, H, W)
    ys, xs = np.nonzero(m)
    if len(ys) and rng.random() < 0.7:
        k = rng.randrange(len(ys))
        cy, cx = ys[k], xs[k]
        y0 = int(np.clip(cy - ps // 2 + rng.randint(-ps // 4, ps // 4), 0, H - ps))
        x0 = int(np.clip(cx - ps // 2 + rng.randint(-ps // 4, ps // 4), 0, W - ps))
    else:
        y0, x0 = rng.randint(0, H - ps), rng.randint(0, W - ps)
    xi = cv2.resize(x[y0:y0 + ps, x0:x0 + ps], (PATCH, PATCH), interpolation=cv2.INTER_AREA).astype(np.float32)
    mi = cv2.resize(m[y0:y0 + ps, x0:x0 + ps], (PATCH, PATCH), interpolation=cv2.INTER_NEAREST)
    pi = cv2.resize(lab[y0:y0 + ps, x0:x0 + ps] >> 1, (PATCH, PATCH),
                    interpolation=cv2.INTER_NEAREST)
    xi = xi * rng.uniform(0.85, 1.1) + rng.uniform(-20, 15)
    if rng.random() < 0.3:
        xi = cv2.GaussianBlur(xi, (3, 3), 0)
    if rng.random() < 0.3:
        xi = xi + np.random.normal(0, rng.uniform(2, 8), xi.shape)
    return np.clip(xi, 0, 255) / 255.0, mi.astype(np.float32), pi.astype(np.float32)


# ---------------------------------------------------------------- 評分
@torch.no_grad()
def predict_page(model, img, dev, tile=768, pad=32):
    H, W = img.shape
    out = np.zeros((H, W), np.float32)
    x = torch.from_numpy(img.astype(np.float32) / 255.0)
    for y0 in range(0, H, tile):
        for x0 in range(0, W, tile):
            ya, xa = max(0, y0 - pad), max(0, x0 - pad)
            yb, xb = min(H, y0 + tile + pad), min(W, x0 + tile + pad)
            t = x[ya:yb, xa:xb]
            h, w = t.shape
            ph, pw = (16 - h % 16) % 16, (16 - w % 16) % 16
            t = F.pad(t[None, None], (0, pw, 0, ph), value=1.0).to(dev)
            p = torch.sigmoid(model(t))[0, 0, :h, :w].cpu().numpy()
            out[y0:min(H, y0 + tile), x0:min(W, x0 + tile)] = p[y0 - ya:y0 - ya + min(tile, H - y0),
                                                               x0 - xa:x0 - xa + min(tile, W - x0)]
    return out


def evaluate(model, pages, dev):
    probs = [(predict_page(model, p["x"], dev), p) for p in pages]
    best = None
    table = []
    for th in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.93, 0.95, 0.97, 0.98, 0.99]:
        tp = hand = er = prt = qer = qpix = 0
        for pr, p in probs:
            pred = pr >= th
            hm, pm = (p["lab"] & 1) > 0, (p["lab"] & 2) > 0
            hand += hm.sum()
            tp += (pred & hm).sum()
            prt += pm.sum()
            er += (pred & pm).sum()
            if p.get("hasq"):
                qm = (p["lab"] & 4) > 0
                qpix += qm.sum(); qer += (pred & qm).sum()
        rec, ers = tp / max(1, hand), er / max(1, prt)
        table.append((round(float(th), 3), float(rec), float(ers), float(qer / max(1, qpix))))
        if ers < MAX_ERASE and (best is None or rec > best[1]):
            best = (round(float(th), 3), float(rec), float(ers), float(qer / max(1, qpix)))
    return best, table


def group_metrics(model, pages, dev):
    """A=真實配對、B=合成、C=人工；各自獨立挑安全門檻並留下可審核表。"""
    out = {}
    for key, label in (("real", "A_real"), ("synth", "B_synth"), ("manual", "C_manual")):
        subset = [p for p in pages if p["source"] == key]
        if subset:
            best, table = evaluate(model, subset, dev)
            out[label] = {"pages": len(subset), "best": best, "table": table}
    return out


# ---------------------------------------------------------------- 主程式
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="ocr/dehand/data")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--steps", type=int, default=150, help="每個 epoch 幾批")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--synth-ratio", type=float, default=.7, help="合成頁相對於真實頁的抽樣權重")
    ap.add_argument("--out", default=None, help="run 輸出資料夾；預設 ocr/dehand/runs/<時間>")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    torch.manual_seed(a.seed)
    dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")

    pages = load_pages(a.data)
    groups = sorted({p["group"] for p in pages})
    if len(groups) < 2:
        sys.exit(f"訓練資料只有 {len(groups)} 場考試，至少要 2 場（一場訓練、一場驗證）。先跑 pairs_build.py。")
    rng.shuffle(groups)
    nval = min(len(groups) - 1, max(1, int(round(len(groups) * a.val_frac))))
    val_g = set(groups[:nval])
    tr = [p for p in pages if p["group"] not in val_g]
    va = [p for p in pages if p["group"] in val_g]
    print(f"裝置 {dev}；訓練 {len(tr)} 頁（{len(groups) - nval} 場考試）、驗證 {len(va)} 頁（{nval} 場）", flush=True)
    weights = [p["w"] * (a.synth_ratio / .7 if p["source"] == "synth" else 1) * (1 + 50 * p["nhand"] / p["lab"].size) for p in tr]

    model = UNet().to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=2e-3, total_steps=a.epochs * a.steps)
    best_rec, best_state = -1, None
    for ep in range(1, a.epochs + 1):
        model.train()
        t0, tot = time.time(), 0.0
        for _ in range(a.steps):
            xs, ms, ps = zip(*[sample(rng.choices(tr, weights)[0], rng) for _ in range(a.batch)])
            x = torch.tensor(np.stack(xs))[:, None].float().to(dev)
            m = torch.tensor(np.stack(ms))[:, None].to(dev)
            pw = torch.tensor(np.stack(ps))[:, None].to(dev)
            logit = model(x)
            w = 1.0 + (PRINT_W - 1.0) * pw + 2.0 * m        # 印刷字誤判重罰；筆跡少，正樣本加權
            bce = F.binary_cross_entropy_with_logits(logit, m, weight=w)
            p = torch.sigmoid(logit)
            dice = 1 - (2 * (p * m).sum() + 1) / (p.sum() + m.sum() + 1)
            loss = bce + 0.5 * dice
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            tot += loss.item()
        msg = f"epoch {ep:>2}/{a.epochs}  loss {tot / a.steps:.4f}  {time.time() - t0:.0f}s"
        if ep % 5 == 0 or ep == a.epochs:
            model.eval()
            best, _ = evaluate(model, va, dev)
            if best:
                msg += f"  驗證：門檻 {best[0]} 召回 {best[1]:.1%} 印刷誤擦 {best[2]:.2%}"
                if best[1] > best_rec:
                    best_rec, best_state = best[1], {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            else:
                msg += "  驗證：任何門檻的印刷誤擦都 ≥ 0.5%"
        print(msg, flush=True)

    if best_state is None:
        best_state = {k: v.detach().cpu() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    model.eval()
    best, table = evaluate(model, va, dev); groups_report = group_metrics(model, va, dev)
    out = a.out or os.path.join(HERE, "runs", time.strftime("%Y%m%d-%H%M%S")); os.makedirs(out, exist_ok=True)
    cpu_model = model.to("cpu")
    torch.onnx.export(cpu_model, torch.ones(1, 1, 256, 256), os.path.join(out, "dehand.onnx"),
                      input_names=["x"], output_names=["logits"], opset_version=17,
                      dynamic_axes={"x": {2: "h", 3: "w"}, "logits": {2: "h", 3: "w"}})
    info = {"threshold": best[0] if best else 0.9, "dpi": 150, "recall": best[1] if best else None,
            "print_erase": best[2] if best else None, "val_groups": sorted(val_g),
            "val_pages": [p["name"] for p in va], "train_pages": len(tr), "table": table, "validation": groups_report,
            "note": "我們自己訓練（ocr/dehand/train.py），資料來自茲茲乾淨版考卷對齊相減"}
    json.dump(info, open(os.path.join(out, "dehand.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n完成：門檻 {info['threshold']}  召回 {info['recall']}  印刷誤擦 {info['print_erase']}"
          f"\n（卷土重來 v1：召回 36.2%、印刷誤擦 0.49%）→ {os.path.join(out, 'dehand.onnx')}")


if __name__ == "__main__":
    sys.exit(main())
