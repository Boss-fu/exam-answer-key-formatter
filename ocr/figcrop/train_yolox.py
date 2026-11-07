#!/usr/bin/env python3
"""用手動截圖微調 YOLOX（Apache-2.0）成「圖／表」偵測器，CPU 訓練，依考卷交叉驗證。訓練環境另開 venv（torch CPU＋yolox），不和 OCR 環境混。

先跑 ocr/figcrop/train_data.py 產生 ocr/results/figcrop_train，再：
    <訓練 venv>/python ocr/figcrop/train_yolox.py cv   [--epochs 60] [--size 768] [--model yolox_s]   # 5 組交叉驗證
    <訓練 venv>/python ocr/figcrop/train_yolox.py full [--epochs 60] ...                                 # 全部資料訓練（上線候選）
預訓練權重（COCO）放 ocr/results/figcrop_weights/<model>.pth，沒有會從 YOLOX 官方 release 下載。
結果在 ocr/results/figcrop_train_runs/<tag>/：每組的 best.pth、val 預測、report.json（逐卷：模型 recall@IoU.8 vs 現行模型＋規則）。
YOLOX 0.3.0 的 Trainer 寫死 CUDA，這裡自己寫簡單的 CPU 訓練迴圈（沿用 Exp 的模型、資料增強、optimizer、lr 排程）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import torch

_imread = cv2.imread
def _imread_any(path, flags=cv2.IMREAD_COLOR):
    """YOLOX 用 cv2.imread 讀圖；Windows 碰到中文路徑會回 None（檔名都是中文卷名），改成先讀檔再解碼。"""
    try:
        data = np.fromfile(str(path), np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, flags) if data.size else None
cv2.imread = _imread_any
from yolox.exp import Exp as BaseExp
from yolox.utils import postprocess

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "ocr/results/figcrop_train"
WEIGHTS = {"yolox_s": (0.33, 0.50, "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.pth"),
           "yolox_tiny": (0.33, 0.375, "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.pth"),
           "yolox_nano": (0.33, 0.25, "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.pth")}


class Exp(BaseExp):
    def __init__(self, model, size, epochs, train_ann, val_ann):
        super().__init__()
        self.depth, self.width, _ = WEIGHTS[model]
        self.num_classes = 2
        self.input_size = self.test_size = (size, size)
        self.max_epoch = epochs; self.no_aug_epochs = max(2, epochs // 6); self.warmup_epochs = 2
        self.data_dir = str(DATA); self.train_ann = train_ann; self.val_ann = val_ann
        self.data_num_workers = 0; self.multiscale_range = 2
        self.mosaic_prob = .5; self.mixup_prob = 0; self.hsv_prob = .5; self.flip_prob = 0   # 考卷不左右翻
        self.degrees = 2.0; self.translate = .05; self.mosaic_scale = (.6, 1.4); self.shear = 1.0
        self.basic_lr_per_img = .01 / 64.0; self.eval_interval = 10 ** 9
        if model == "yolox_nano":
            self.depthwise = True

    def get_model(self):
        if model_name == "yolox_nano":
            from yolox.models import YOLOX, YOLOPAFPN, YOLOXHead
            in_ch = [256, 512, 1024]
            backbone = YOLOPAFPN(self.depth, self.width, in_channels=in_ch, act=self.act, depthwise=True)
            head = YOLOXHead(self.num_classes, self.width, in_channels=in_ch, act=self.act, depthwise=True)
            self.model = YOLOX(backbone, head)
            return self.model
        return super().get_model()

    def get_dataset(self, cache=False, cache_type="ram"):
        from yolox.data import COCODataset, TrainTransform
        return COCODataset(data_dir=self.data_dir, json_file=self.train_ann, name="train2017", img_size=self.input_size,
                           preproc=TrainTransform(max_labels=50, flip_prob=self.flip_prob, hsv_prob=self.hsv_prob))


def pretrained(model):
    p = ROOT / "ocr/results/figcrop_weights" / f"{model}.pth"   # 不放 DATA：train_data.py 重建資料集會清掉 DATA
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(WEIGHTS[model][2], p)
    return p


def load_pretrained(net, path):
    ckpt = torch.load(path, map_location="cpu", weights_only=False)["model"]
    own = net.state_dict()
    ok = {k: v for k, v in ckpt.items() if k in own and v.shape == own[k].shape}     # 分類頭類別數不同，略過
    net.load_state_dict(ok, strict=False)
    return len(ok), len(own)


def train(exp, out: Path, batch=4):
    torch.manual_seed(0); np.random.seed(0)
    net = exp.get_model(); n_ok, n_all = load_pretrained(net, pretrained(model_name))
    net.train()
    loader = exp.get_data_loader(batch_size=batch, is_distributed=False, no_aug=False)
    iters = max(1, len(loader.dataset) // batch)
    opt = exp.get_optimizer(batch); sched = exp.get_lr_scheduler(exp.basic_lr_per_img * batch, iters)
    it = iter(loader); step = 0; log = []; t0 = time.time()
    for ep in range(exp.max_epoch):
        if ep == exp.max_epoch - exp.no_aug_epochs:   # 最後幾輪關掉 mosaic，和 YOLOX 原版一樣
            loader.close_mosaic(); net.head.use_l1 = True; it = iter(loader)
        tot = 0.0
        for _ in range(iters):
            inps, targets, _, _ = next(it)
            inps = inps.float(); targets = targets.float(); targets.requires_grad = False
            loss = net(inps, targets)["total_loss"]
            opt.zero_grad(); loss.backward(); opt.step()
            lr = sched.update_lr(step + 1); step += 1
            for g in opt.param_groups:
                g["lr"] = lr
            tot += float(loss)
        log.append({"epoch": ep + 1, "loss": round(tot / iters, 4), "sec": round(time.time() - t0)})
        print(f"  epoch {ep + 1}/{exp.max_epoch} loss {tot / iters:.3f}（{time.time() - t0:.0f} 秒）", flush=True)
    torch.save({"model": net.state_dict()}, out / "last.pth")
    return net, {"pretrained_tensors": f"{n_ok}/{n_all}", "log": log}


def iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1]); x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    return inter / max(1, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


@torch.no_grad()
def predict(net, exp, items, conf=.3, nms=.45):
    from yolox.data import ValTransform
    net.eval(); pre = ValTransform(legacy=False); out = {}
    for i in items:
        img = cv2.imdecode(np.fromfile(str(DATA / "train2017" / f"{i['name']}.jpg"), np.uint8), cv2.IMREAD_COLOR)
        x, _ = pre(img, None, exp.test_size); r = min(exp.test_size[0] / img.shape[0], exp.test_size[1] / img.shape[1])
        o = postprocess(net(torch.from_numpy(x).unsqueeze(0).float()), exp.num_classes, conf, nms, class_agnostic=True)[0]
        boxes = [] if o is None else [[float(v) / r for v in row[:4]] + [float(row[4] * row[5]), int(row[6])] for row in o.numpy()]
        out[i["name"]] = boxes
    return out


def score(items, preds, baseline_rows):
    """逐卷：模型（訓練圖座標）recall@.8 vs 現行模型＋規則（baseline.json 同頁 hit）。"""
    rep = {}
    base = {}
    for r in baseline_rows:
        if "hit" in r:
            base.setdefault((r["exam"], r["page"]), []).append(r["hit"])
    for i in items:
        gts = [g for _c, g in i["anns"]]; boxes = [b[:4] for b in preds[i["name"]]]
        hits = sum(1 for g in gts if max((iou(g, b) for b in boxes), default=0) >= .8)
        extra = sum(1 for b in boxes if max((iou(g, b) for g in gts), default=0) < .1)
        e = rep.setdefault(i["exam"], {"manual": 0, "model_hit": 0, "model_extra": 0, "rules_hit": 0})
        e["manual"] += len(gts); e["model_hit"] += hits; e["model_extra"] += extra
        e["rules_hit"] += sum(base.get((i["exam"], i["page"]), []))
    return rep


def main():
    global model_name
    ap = argparse.ArgumentParser(); ap.add_argument("mode", choices=["cv", "full"]); ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--size", type=int, default=768); ap.add_argument("--model", default="yolox_s", choices=list(WEIGHTS))
    ap.add_argument("--batch", type=int, default=4); ap.add_argument("--folds", default="0,1,2,3,4"); ap.add_argument("--tag")
    ap.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    a = ap.parse_args(); model_name = a.model; torch.set_num_threads(a.threads)
    index = json.loads((DATA / "index.json").read_text(encoding="utf-8"))
    baseline = json.loads((ROOT / "ocr/results/figcrop_gt/baseline.json").read_text(encoding="utf-8"))["rows"]
    tag = a.tag or f"{a.model}_{a.size}_e{a.epochs}"; run = ROOT / "ocr/results/figcrop_train_runs" / tag; run.mkdir(parents=True, exist_ok=True)
    report = {"args": vars(a), "folds": {}}
    folds = [int(f) for f in a.folds.split(",")] if a.mode == "cv" else ["all"]
    for k in folds:
        out = run / f"fold{k}"; out.mkdir(exist_ok=True)
        train_ann = f"fold{k}_train.json" if a.mode == "cv" else "all_train.json"
        exp = Exp(a.model, a.size, a.epochs, train_ann, train_ann)
        print(f"[{tag}] fold {k}：訓練 {train_ann}", flush=True)
        net, info = train(exp, out, a.batch)
        if a.mode == "cv":
            val = [i for i in index if i["fold"] == k]
            preds = predict(net, exp, val)
            (out / "val_pred.json").write_text(json.dumps(preds), encoding="utf-8")
            info["per_exam"] = score(val, preds, baseline)
            for ex, e in sorted(info["per_exam"].items()):
                print(f"  {ex:<22} 手動 {e['manual']:>3}  模型 {e['model_hit']:>3}（多 {e['model_extra']}）  現行 {e['rules_hit']:>3}", flush=True)
        report["folds"][str(k)] = info
        (run / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.mode == "cv":
        tot = {"manual": 0, "model_hit": 0, "model_extra": 0, "rules_hit": 0}
        for f in report["folds"].values():
            for e in f["per_exam"].values():
                for key in tot:
                    tot[key] += e[key]
        report["total"] = tot
        (run / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[{tag}] 合計：手動 {tot['manual']}、模型 recall@.8 {tot['model_hit']}（{tot['model_hit'] / max(1, tot['manual']):.1%}，多框 {tot['model_extra']}）"
              f"、現行模型＋規則 {tot['rules_hit']}（{tot['rules_hit'] / max(1, tot['manual']):.1%}）")


model_name = "yolox_s"
if __name__ == "__main__":
    main()
