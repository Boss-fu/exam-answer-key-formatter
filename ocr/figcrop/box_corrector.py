#!/usr/bin/env python3
"""框後校正器（試水溫）：不動版面模型權重，用手動資料學「現行流程切出的框留不留、四邊往哪修」，依考卷交叉驗證。

兩步（兩個 venv：特徵要跑切圖流程，訓練要 scikit-learn）：
    <評測 venv>/python ocr/figcrop/box_corrector.py features            # → ocr/results/figcrop_corrector/features.json
    <訓練 venv>/python ocr/figcrop/box_corrector.py cv [--thr 0.5]        # 依卷 5 組交叉驗證，對照現行
標籤：框和任一手動框 IoU ≥ .1 → 留（否則是多框）；和手動框 IoU ≥ .3 → 四邊差（手動−自動，除以頁寬）當修邊目標。
評分用同一套簡化指標（每張手動取最好的框 IoU ≥ .8 算中、和手動都 < .1 的框算多框），現行與校正後都用它，公平比較。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "ocr/results/figcrop_corrector"


def iou(a, b):
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    i = max(0, x1 - x0) * max(0, y1 - y0)
    return i / max(1, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)


def paper_of(exam):
    return exam.removesuffix("_圖片型")


# ---------- 特徵（評測 venv） ----------
def box_features(img, b, ocr_lines, dets, crop, gray):
    import numpy as np
    H, W = img.shape[:2]; x0, y0, x1, y1 = b; w, h = max(1, x1 - x0), max(1, y1 - y0)
    tl = [l for l in ocr_lines if crop.text_like(l[4])]
    inside = [l for l in ocr_lines if l[0] >= x0 - 5 and l[2] <= x1 + 5 and l[1] >= y0 - 5 and l[3] <= y1 + 5]
    def nearest(side):
        best = 1.0
        for l0, l2, l1, l3, _t in tl:
            hov = min(l1, x1) - max(l0, x0); vov = min(l3, y1) - max(l2, y0)
            if side == "up" and hov > 0 and l3 <= y0 + 5: best = min(best, (y0 - l3) / H)
            if side == "down" and hov > 0 and l2 >= y1 - 5: best = min(best, (l2 - y1) / H)
            if side == "left" and vov > 0 and l1 <= x0 + 5: best = min(best, (x0 - l1) / W)
            if side == "right" and vov > 0 and l0 >= x1 - 5: best = min(best, (l0 - x1) / W)
        return max(0.0, best)
    def ink(xa, ya, xb, yb):
        xa, ya, xb, yb = max(0, xa), max(0, ya), min(W, xb), min(H, yb)
        if xb <= xa or yb <= ya: return 0.0
        return float((gray[ya:yb, xa:xb] < 160).mean())
    s = int(.01 * W)
    best = max(((iou(b, d), sc, c) for c, d, sc in dets), default=(0, 0, ""))
    return [x0 / W, y0 / H, x1 / W, y1 / H, w / W, h / H, float(np.log(w / h)),
            best[0], best[1], float(best[2] == "table"), float(best[2] in ("image", "figure")), float(best[2] == "chart"),
            sum(1 for _c, d, _s in dets if iou(d, b) > 0 and d[0] >= x0 - 5 and d[2] <= x1 + 5 and d[1] >= y0 - 5 and d[3] <= y1 + 5),
            crop.sentence_cover(b, ocr_lines), len(inside), sum(1 for l in inside if crop.text_like(l[4])),
            sum(1 for l in inside if crop.RE_QNUM.match(str(l[4]))),
            nearest("up"), nearest("down"), nearest("left"), nearest("right"),
            ink(x0, y0, x1, y1), ink(x0, y0 - s, x1, y0), ink(x0, y1, x1, y1 + s), ink(x0 - s, y0, x0, y1), ink(x1, y0, x1 + s, y1),
            float(crop.has_border(img, b))]


def features(a):
    sys.path[:0] = [str(HERE), str(HERE.parent)]
    import cv2, pymupdf
    import crop, evalcache, gt_eval
    evalcache.enable(crop)
    rows = json.loads(Path(a.eval).read_text(encoding="utf-8"))["rows"]
    gts = defaultdict(list); bad = set()
    for r in rows:
        if r.get("error"): bad.add((r["exam"], r["page"]))
        elif r.get("gt"): gts[(r["exam"], r["page"])].append(r["gt"])
    exams = sorted({r["exam"] for r in rows}); out = []
    for exam in exams:
        doc = pymupdf.open(Path(a.gt_dir) / exam / "exam.pdf")
        for pn, page in enumerate(doc, 1):
            if (exam, pn) in bad: continue
            img = gt_eval.render(page); first = []; crop.text_lines(img, first)
            if not crop.looks_upright(first): continue        # 手動卷都是直立頁；轉向頁座標換算複雜，試水溫先略過
            raw = []; boxes, dets = crop.crop_page(img, raw=raw, ocr=first)
            ocr_lines = [(l[0], l[1], l[2], l[3], t) for l, t in raw]
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY); H, W = img.shape[:2]
            items = []
            for b in boxes:
                b = [int(v) for v in b]; g = max(gts[(exam, pn)], key=lambda x: iou(b, x), default=None)
                gi = iou(b, g) if g else 0
                items.append({"box": b, "f": box_features(img, b, ocr_lines, dets, crop, gray), "keep": gi >= .1,
                              "delta": [(g[k] - b[k]) / W for k in range(4)] if g and gi >= .3 else None})
            out.append({"exam": exam, "paper": paper_of(exam), "page": pn, "W": W, "H": H, "gt": gts[(exam, pn)], "items": items})
        print(exam, flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "features.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"頁 {len(out)}、框 {sum(len(p['items']) for p in out)}（多框 {sum(1 for p in out for i in p['items'] if not i['keep'])}）、手動 {sum(len(p['gt']) for p in out)}")


# ---------- 交叉驗證（訓練 venv） ----------
def score(pages, boxes_of):
    hit = extra = man = 0
    for p in pages:
        bx = boxes_of(p); man += len(p["gt"])
        hit += sum(1 for g in p["gt"] if max((iou(g, b) for b in bx), default=0) >= .8)
        extra += sum(1 for b in bx if max((iou(g, b) for g in p["gt"]), default=0) < .1)
    return man, hit, extra


def cv(a):
    import numpy as np
    from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
    pages = json.loads((OUT / "features.json").read_text(encoding="utf-8"))
    papers = sorted({p["paper"] for p in pages}); random.Random(20261106).shuffle(papers)
    fold = {pp: k % a.folds for k, pp in enumerate(papers)}
    tot = defaultdict(int); per = []
    for k in range(a.folds):
        tr = [p for p in pages if fold[p["paper"]] != k]; va = [p for p in pages if fold[p["paper"]] == k]
        X = np.array([i["f"] for p in tr for i in p["items"]]); y = np.array([i["keep"] for p in tr for i in p["items"]])
        clf = GradientBoostingClassifier(n_estimators=a.trees, max_depth=2, learning_rate=.1, random_state=0).fit(X, y) if len(set(y)) > 1 else None
        regs = []
        D = [(i["f"], i["delta"]) for p in tr for i in p["items"] if i["delta"]]
        for e in range(4):
            regs.append(GradientBoostingRegressor(n_estimators=a.trees, max_depth=2, learning_rate=.05, loss="huber", random_state=0)
                        .fit(np.array([f for f, _ in D]), np.array([d[e] for _, d in D])))
        def corrected(p, drop=True, edges=True):
            out = []
            for i in p["items"]:
                f = np.array([i["f"]])
                if drop and clf is not None and clf.predict_proba(f)[0][list(clf.classes_).index(True)] < a.thr:
                    continue
                b = list(i["box"])
                if edges:
                    for e in range(4):
                        d = float(regs[e].predict(f)[0])
                        if abs(d) >= a.min_shift:
                            b[e] = b[e] + d * p["W"]
                out.append(b)
            return out
        res = {"現行": score(va, lambda p: [i["box"] for i in p["items"]]),
               "只刪框": score(va, lambda p: corrected(p, True, False)),
               "只修邊": score(va, lambda p: corrected(p, False, True)),
               "刪框＋修邊": score(va, lambda p: corrected(p, True, True))}
        for name, (m, h, x) in res.items():
            tot[(name, "m")] += m; tot[(name, "h")] += h; tot[(name, "x")] += x
        per.append((k, sorted({p["paper"] for p in va}), res))
        print(f"fold {k}（{'、'.join(sorted({p['paper'] for p in va}))}）：" + "；".join(f"{n} {h}/{m} 多框 {x}" for n, (m, h, x) in res.items()), flush=True)
    print("合計：" + "；".join(f"{n} {tot[(n, 'h')]}/{tot[(n, 'm')]} 多框 {tot[(n, 'x')]}" for n in ("現行", "只刪框", "只修邊", "刪框＋修邊")))


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("features"); f.add_argument("--eval", default=str(ROOT / "ocr/results/figcrop_gt/baseline.json"))
    f.add_argument("--gt-dir", default=str(ROOT / "ocr/results/figcrop_gt"))
    c = sub.add_parser("cv"); c.add_argument("--folds", type=int, default=5); c.add_argument("--thr", type=float, default=.5)
    c.add_argument("--trees", type=int, default=150); c.add_argument("--min-shift", type=float, default=.002)
    a = ap.parse_args()
    features(a) if a.cmd == "features" else cv(a)


if __name__ == "__main__":
    main()
