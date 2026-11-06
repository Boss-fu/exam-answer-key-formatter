#!/usr/bin/env python3
"""手動截圖 → 整頁偵測訓練資料集（YOLO 格式），給微調版面模型用。資料集在 ocr/results/figcrop_train（不進 git，含考卷）。

python ocr/figcrop/train_data.py [--eval ocr/results/figcrop_gt/baseline.json] [--imgsz 1280] [--folds 5]

- 正樣本：gt_eval 用 SIFT 定位到的手動框（單一類別 figure：圖和表都算，手動沒分）。
- 只收「有手動框的頁」。
- 手動沒框、但現行程式有切的框（gt_eval 的 extra）：可能是手動漏截的真圖，不能當負樣本，訓練圖上把那區塗白（忽略）。
  另外定位失敗的手動圖那頁整頁不收（不知道框在哪）。
- 交叉驗證依「考卷」分組：同一份卷（含 _圖片型 去文字層版本）一定在同一組，不會同時出現在訓練和驗證。
- 圖縮到長邊 imgsz（預設 1280），座標同比例；labels 是 YOLO 正規化 cx cy w h。
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
from collections import defaultdict
from pathlib import Path

import cv2
import pymupdf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
import sys
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import crop  # noqa: E402
import evalcache  # noqa: E402
import gt_eval  # noqa: E402

OUT = ROOT / "ocr/results/figcrop_train"


def paper_of(exam: str) -> str:
    """_圖片型 是同一份卷去文字層的版本，分組時算同一份。"""
    return exam.removesuffix("_圖片型")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", default=str(ROOT / "ocr/results/figcrop_gt/baseline.json"))
    ap.add_argument("--gt-dir", default=str(ROOT / "ocr/results/figcrop_gt"))
    ap.add_argument("--imgsz", type=int, default=1280); ap.add_argument("--folds", type=int, default=5)
    a = ap.parse_args()
    evalcache.enable(crop)                          # 類別判斷要跑版面模型，評測快取裡多半已有
    rows = json.loads(Path(a.eval).read_text(encoding="utf-8"))["rows"]
    pages = defaultdict(lambda: {"gt": [], "ignore": [], "bad": False})
    for r in rows:
        key = (r["exam"], r["page"])
        if r.get("error"):
            pages[key]["bad"] = True
        elif r.get("gt"):
            pages[key]["gt"].append(r["gt"])
        elif r.get("extra"):
            pages[key]["ignore"].append(r["auto"])
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "train2017").mkdir(parents=True); (OUT / "labels").mkdir()   # YOLOX 的 data loader 寫死讀 train2017/
    index = []; docs = {}
    for (exam, pn), p in sorted(pages.items()):
        if p["bad"] or not p["gt"]:
            continue
        doc = docs.setdefault(exam, pymupdf.open(Path(a.gt_dir) / exam / "exam.pdf"))
        img = gt_eval.render(doc[pn - 1])          # 和評測／正式切圖同解析度
        for x0, y0, x1, y1 in p["ignore"]:         # 手動沒框的區域塗白：不當正樣本也不當負樣本
            img[max(0, y0):y1, max(0, x0):x1] = 255
        H, W = img.shape[:2]; s = a.imgsz / max(H, W)
        small = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img
        name = f"{exam}_p{pn}"
        crop.pyexam._imwrite(str(OUT / "train2017" / f"{name}.jpg"), small, [cv2.IMWRITE_JPEG_QUALITY, 92])
        # 類別：手動沒分圖和表，取重疊最多的版面模型偵測框類別（table→1，其他→0）
        dets = crop.detect(gt_eval.render(doc[pn - 1]), "pp_doc_layoutv3")
        cls = [1 if max(((gt_eval.iou(g, b), c) for c, b, _s in dets), default=(0, ""))[1] == "table"
               and max((gt_eval.iou(g, b) for c, b, _s in dets if c == "table"), default=0) > .3 else 0 for g in p["gt"]]
        lines = [f"{c} {(x0 + x1) / 2 / W:.6f} {(y0 + y1) / 2 / H:.6f} {(x1 - x0) / W:.6f} {(y1 - y0) / H:.6f}" for c, (x0, y0, x1, y1) in zip(cls, p["gt"])]
        (OUT / "labels" / f"{name}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        index.append({"name": name, "exam": exam, "paper": paper_of(exam), "page": pn, "boxes": len(p["gt"]), "ignored": len(p["ignore"]),
                      "w": small.shape[1], "h": small.shape[0], "scale": s if s < 1 else 1.0,
                      "anns": [[c, [round(v * (s if s < 1 else 1), 1) for v in g]] for c, g in zip(cls, p["gt"])]})
    papers = sorted({i["paper"] for i in index}); random.Random(20261106).shuffle(papers)
    fold_of = {pp: k % a.folds for k, pp in enumerate(papers)}
    for i in index:
        i["fold"] = fold_of[i["paper"]]
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    # YOLOX 吃 COCO 格式：每組一份 train／val（val＝該組的卷，train＝其他組），另給 all（全部，最後上線用）
    def coco(items):
        imgs, anns = [], []
        for k, i in enumerate(items, 1):
            imgs.append({"id": k, "file_name": f"{i['name']}.jpg", "width": i["w"], "height": i["h"]})
            for c, (x0, y0, x1, y1) in i["anns"]:
                anns.append({"id": len(anns) + 1, "image_id": k, "category_id": c + 1, "bbox": [x0, y0, x1 - x0, y1 - y0],
                             "area": (x1 - x0) * (y1 - y0), "iscrowd": 0})
        return {"images": imgs, "annotations": anns, "categories": [{"id": 1, "name": "figure"}, {"id": 2, "name": "table"}]}
    (OUT / "annotations").mkdir()
    for k in range(a.folds):
        for split, items in (("train", [i for i in index if i["fold"] != k]), ("val", [i for i in index if i["fold"] == k])):
            (OUT / "annotations" / f"fold{k}_{split}.json").write_text(json.dumps(coco(items), ensure_ascii=False), encoding="utf-8")
    (OUT / "annotations" / "all_train.json").write_text(json.dumps(coco(index), ensure_ascii=False), encoding="utf-8")
    print(f"類別：圖 {sum(1 for i in index for c, _ in i['anns'] if c == 0)}、表 {sum(1 for i in index for c, _ in i['anns'] if c == 1)}")
    per = defaultdict(lambda: [0, 0])
    for i in index:
        per[i["fold"]][0] += 1; per[i["fold"]][1] += i["boxes"]
    print(f"頁 {len(index)}、框 {sum(i['boxes'] for i in index)}、塗白忽略區 {sum(i['ignored'] for i in index)}、卷 {len(papers)}（含圖片型 {len({i['exam'] for i in index})} 份）")
    print("各組（頁／框）：" + "、".join(f"{k}: {v[0]}/{v[1]}" for k, v in sorted(per.items())))
    print("各組的卷：" + "；".join(f"{k}: {'、'.join(sorted(pp for pp in papers if fold_of[pp] == k))}" for k in range(a.folds)))


if __name__ == "__main__":
    main()
