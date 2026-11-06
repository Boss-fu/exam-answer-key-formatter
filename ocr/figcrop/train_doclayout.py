#!/usr/bin/env python3
"""微調 PP-DocLayoutV3（現行版面模型，Apache-2.0）：手動截圖當圖／表標註，其他類別用現行模型自己的預測（自蒸餾），依考卷交叉驗證。

三個 venv（互不干擾）：
    評測 venv（zizi-exam）：  python ocr/figcrop/train_doclayout.py data [--long 1600]
    訓練 venv（paddle-layout，paddlepaddle 3.1.1 CPU＋paddlex 3.7.2＋PaddleDetection 外掛＋paddle2onnx 2.1.0）：
                              python ocr/figcrop/train_doclayout.py train --fold 0 --epochs 1 [--limit 16] [--freeze]
                              python ocr/figcrop/train_doclayout.py onnx  --run <訓練輸出資料夾>
    評測 venv：               python ocr/figcrop/train_doclayout.py eval  --onnx <model.onnx> --fold 0
資料集在 ocr/results/doclayout_train（不進 git，含考卷）；權重、訓練輸出在 ocr/results/paddle_layout。
- 圖／表：gt_eval 定位到的手動框（和表格模型框 IoU>.3 的算 table，其他算 image）；模型自己的 image／chart／table 框拿掉。
- 手動沒框、但模型或程式有切的圖區域塗白（不當正樣本也不當負樣本），和 train_data.py 一樣。
- 其他 22 類：現行模型（rapid_layout，門檻 .5）的輸出照抄；閱讀順序用模型輸出的順序，手動框沿用重疊最多的模型框的順序。
- 遮罩用矩形（手動只有框）。交叉驗證依考卷分組（_圖片型 和原卷同組），保留卷不在手動資料裡。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = ROOT / "ocr/results/doclayout_train"
WORK = ROOT / "ocr/results/paddle_layout"
PRETRAINED = ROOT / "ocr/results/figcrop_weights/PP-DocLayoutV3_pretrained.pdparams"
FIG = {"image", "chart", "table"}


def iou(a, b):
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]); i = max(0, x1 - x0) * max(0, y1 - y0)
    return i / max(1, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)


def paper_of(exam):
    return exam.removesuffix("_圖片型")


def folds_of(papers, k=5):
    papers = sorted(papers); random.Random(20261106).shuffle(papers)
    return {pp: i % k for i, pp in enumerate(papers)}


# ---------- data（評測 venv） ----------
def build(a):
    sys.path[:0] = [str(HERE), str(HERE.parent)]
    import cv2, pymupdf
    import crop, gt_eval
    from rapid_layout import RapidLayout
    import onnxruntime as ort
    import rapid_layout
    eng = RapidLayout(model_type="pp_doc_layoutv3")
    ref = Path(rapid_layout.__file__).parent / "models/pp_doc_layoutv3.onnx"   # 類別順序＝模型輸出的類別編號
    labels = ort.InferenceSession(str(ref)).get_modelmeta().custom_metadata_map["character"].splitlines()
    rows = json.loads(Path(a.eval).read_text(encoding="utf-8"))["rows"]
    pages = defaultdict(lambda: {"gt": [], "ignore": [], "bad": False})
    for r in rows:
        k = (r["exam"], r["page"])
        if r.get("error"): pages[k]["bad"] = True
        elif r.get("gt"): pages[k]["gt"].append(r["gt"])
        elif r.get("extra"): pages[k]["ignore"].append(r["auto"])
    if DATA.exists(): shutil.rmtree(DATA)
    (DATA / "images").mkdir(parents=True); (DATA / "annotations").mkdir()
    index = []; docs = {}
    for (exam, pn), p in sorted(pages.items()):
        if p["bad"] or not p["gt"]: continue
        doc = docs.setdefault(exam, pymupdf.open(Path(a.gt_dir) / exam / "exam.pdf"))
        img = gt_eval.render(doc[pn - 1]); H, W = img.shape[:2]
        r = eng(img); dets = [(c, [float(v) for v in b], float(s)) for b, c, s in zip(r.boxes, r.class_names, r.scores)]
        order = {i: i + 1 for i in range(len(dets))}               # rapid_layout 已依閱讀順序排好
        anns = []
        for g in p["gt"]:
            best = max(range(len(dets)), key=lambda i: iou(g, dets[i][1]), default=None)
            is_table = best is not None and dets[best][0] == "table" and iou(g, dets[best][1]) > .3
            ro = order[best] if best is not None and iou(g, dets[best][1]) > 0 else 0
            anns.append(("table" if is_table else "image", list(map(float, g)), ro))
        ignore = list(p["ignore"])
        for i, (c, b, s) in enumerate(dets):
            if c in FIG:
                if not any(iou(b, g) > .1 for g in p["gt"]):
                    ignore.append([int(v) for v in b])                 # 模型有、手動沒框：塗白忽略
                continue
            if any(iou(b, ig) > .5 for ig in ignore): continue
            anns.append((c, b, order[i]))
        for x0, y0, x1, y1 in ignore:
            x0, y0 = max(0, int(x0)), max(0, int(y0))
            if not any(iou([x0, y0, x1, y1], g) > .1 for g in p["gt"]):
                img[y0:int(y1), x0:int(x1)] = 255
        # 閱讀順序：沒有對應模型框的手動框插在上方最近的框之後；再重新編成 1..N
        anns.sort(key=lambda t: (t[2] if t[2] else 10 ** 6, t[1][1], t[1][0]))
        s = min(1.0, a.long / max(H, W))
        small = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img
        name = f"{exam}_p{pn}"
        crop.pyexam._imwrite(str(DATA / "images" / f"{name}.jpg"), small, [cv2.IMWRITE_JPEG_QUALITY, 92])
        index.append({"name": name, "exam": exam, "paper": paper_of(exam), "page": pn, "w": small.shape[1], "h": small.shape[0],
                      "anns": [[c, [v * s for v in b], k + 1] for k, (c, b, _ro) in enumerate(anns)], "manual": len(p["gt"])})
    fold = folds_of({i["paper"] for i in index})
    for i in index: i["fold"] = fold[i["paper"]]
    (DATA / "index.json").write_text(json.dumps({"labels": labels, "items": index}, ensure_ascii=False, indent=1), encoding="utf-8")
    def coco(items):
        imgs, anns = [], []
        for k, i in enumerate(items, 1):
            imgs.append({"id": k, "file_name": f"{i['name']}.jpg", "width": i["w"], "height": i["h"]})
            for c, (x0, y0, x1, y1), ro in i["anns"]:
                anns.append({"id": len(anns) + 1, "image_id": k, "category_id": labels.index(c) + 1, "bbox": [x0, y0, x1 - x0, y1 - y0],
                             "area": (x1 - x0) * (y1 - y0), "iscrowd": 0, "read_order": ro,
                             "segmentation": [[x0, y0, x1, y0, x1, y1, x0, y1]]})
        return {"images": imgs, "annotations": anns, "categories": [{"id": j + 1, "name": n} for j, n in enumerate(labels)]}
    for k in range(5):
        for split, items in (("train", [i for i in index if i["fold"] != k]), ("val", [i for i in index if i["fold"] == k])):
            (DATA / "annotations" / f"fold{k}_{split}.json").write_text(json.dumps(coco(items), ensure_ascii=False), encoding="utf-8")
    (DATA / "annotations" / "all_train.json").write_text(json.dumps(coco(index), ensure_ascii=False), encoding="utf-8")
    print(f"頁 {len(index)}、手動框 {sum(i['manual'] for i in index)}、全部標註 {sum(len(i['anns']) for i in index)}、卷 {len(fold)}")
    print("各組的卷：" + "；".join(f"{k}: {'、'.join(sorted(pp for pp in fold if fold[pp] == k))}" for k in range(5)))


# ---------- train / onnx（訓練 venv） ----------
def train(a):
    run = WORK / (a.tag or f"fold{a.fold}_e{a.epochs}{'_frz' if a.freeze else ''}{f'_n{a.limit}' if a.limit else ''}")
    run.mkdir(parents=True, exist_ok=True)
    ann = f"fold{a.fold}_train.json" if a.fold >= 0 else "all_train.json"
    if a.limit:                                   # 試水溫：只取前 N 張
        d = json.loads((DATA / "annotations" / ann).read_text(encoding="utf-8"))
        keep = {im["id"] for im in d["images"][:a.limit]}
        d["images"] = [im for im in d["images"] if im["id"] in keep]; d["annotations"] = [x for x in d["annotations"] if x["image_id"] in keep]
        ann = f"_limit{a.limit}_{ann}"; (DATA / "annotations" / ann).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    val = f"fold{a.fold}_val.json" if a.fold >= 0 else ann
    cfg = Path(sys.prefix) / "Lib/site-packages/paddlex/configs/modules/layout_analysis/PP-DocLayoutV3.yaml"
    main = WORK / "main.py"; main.write_text("from paddlex.engine import Engine\nEngine().run()\n", encoding="utf-8")
    ov = {"Global.mode": "train", "Global.device": "cpu", "Global.dataset_dir": str(DATA), "Global.output": str(run),
          "Train.epochs_iters": a.epochs, "Train.batch_size": a.batch, "Train.learning_rate": a.lr, "Train.num_classes": 25,
          "Train.pretrain_weight_path": str(PRETRAINED), "Train.warmup_steps": a.warmup, "Train.log_interval": 1, "Train.eval_interval": 10 ** 6}
    # PaddleX 的 COCOInstSegDataset 讀 annotations/instance_{train,val}.json：複製成固定檔名
    shutil.copy(DATA / "annotations" / ann, DATA / "annotations" / "instance_train.json")
    shutil.copy(DATA / "annotations" / val, DATA / "annotations" / "instance_val.json")
    cmd = [sys.executable, str(main), "-c", str(cfg)] + [x for k, v in ov.items() for x in ("-o", f"{k}={v}")]
    env = dict(os.environ, PYTHONUTF8="1")
    if a.freeze:      # 凍結骨幹：PaddleDetection 讀 PDX 產生的 yml，這裡用環境變數告訴 patch（見 _freeze_patch）
        env["FIGCROP_FREEZE_BACKBONE"] = "1"
    t0 = time.time(); print(" ".join(cmd), flush=True)
    r = subprocess.run(cmd, env=env, cwd=str(WORK))
    print(f"訓練結束 code={r.returncode}，{time.time() - t0:.0f} 秒；輸出 {run}")


def to_onnx(a):
    run = Path(a.run); w = run / "best_model" / "best_model.pdparams"
    if not w.exists():
        w = max(run.glob("**/*.pdparams"), key=lambda p: p.stat().st_mtime)
    out = run / "export"; cfg = Path(sys.prefix) / "Lib/site-packages/paddlex/configs/modules/layout_analysis/PP-DocLayoutV3.yaml"
    main = WORK / "main.py"
    subprocess.run([sys.executable, str(main), "-c", str(cfg), "-o", "Global.mode=export", "-o", "Global.device=cpu",
                    "-o", f"Global.output={out}", "-o", f"Export.weight_path={w}"], check=True, env=dict(os.environ, PYTHONUTF8="1"), cwd=str(WORK))
    p2o = Path(sys.prefix) / "Scripts" / "paddle2onnx.exe"
    subprocess.run([str(p2o), "--model_dir", str(out), "--model_filename", "inference.json", "--params_filename", "inference.pdiparams",
                    "--save_file", str(out / "model_raw.onnx"), "--opset_version", "16"], check=True)
    import onnx   # 補回 rapid_layout 要的類別 metadata（現行模型 ONNX 的 character）
    ref = onnx.load(a.ref, load_external_data=False); m = onnx.load(str(out / "model_raw.onnx"))
    del m.metadata_props[:]
    for p in ref.metadata_props: m.metadata_props.add(key=p.key, value=p.value)
    onnx.save(m, str(out / "model.onnx")); print("ONNX：", out / "model.onnx")


# ---------- eval（評測 venv） ----------
def evaluate(a):
    """驗證組的卷：用新模型取代版面模型跑 gt_eval 同一套評分；OCR 等照用快取，版面模型輸出不用快取。"""
    sys.path[:0] = [str(HERE), str(HERE.parent)]
    import crop, evalcache, gt_eval
    from rapid_layout import RapidLayout
    idx = json.loads((DATA / "index.json").read_text(encoding="utf-8"))["items"]
    exams = sorted({i["exam"] for i in idx if i["fold"] == a.fold}) if a.fold >= 0 else sorted({i["exam"] for i in idx})
    evalcache.enable(crop)
    eng = RapidLayout(model_type="pp_doc_layoutv3", model_dir_or_path=a.onnx)
    def layout_raw(img, model):                 # 不走快取：快取是現行模型的輸出
        r = eng(img); return r.boxes, r.class_names, r.scores
    crop.layout_raw = layout_raw
    base = json.loads(Path(a.eval).read_text(encoding="utf-8"))
    tmp = WORK / f"eval_fold{a.fold}"; tmp.mkdir(parents=True, exist_ok=True)
    gtd = tmp / "gt"; gtd.mkdir(exist_ok=True)
    for e in exams:
        if not (gtd / e).exists():
            shutil.copytree(Path(a.gt_dir) / e, gtd / e)
    sys.argv = ["gt_eval.py", "--gt-dir", str(gtd), "--out", str(tmp)]
    gt_eval.main()
    new = json.loads((tmp / "gt_eval.json").read_text(encoding="utf-8"))
    def tally(rows):
        rs = [r for r in rows if r.get("exam") in exams]
        return (sum(1 for r in rs if r.get("gt")), sum(1 for r in rs if r.get("gt") and r.get("hit")),
                sum(1 for r in rs if r.get("extra")), sum(1 for r in rs if r.get("gt") and r.get("qno_ok")))
    for name, rows in (("現行", base["rows"]), ("微調", new["rows"])):
        m, h, x, q = tally(rows); print(f"{name}：手動 {m}、中 {h}、漏 {m - h}、多框 {x}、題號對 {q}")
    for e in exams:
        b = [r for r in base["rows"] if r.get("exam") == e]; n = [r for r in new["rows"] if r.get("exam") == e]
        print(f"  {e:<22} 現行 中 {sum(1 for r in b if r.get('hit'))} 多 {sum(1 for r in b if r.get('extra'))}｜"
              f"微調 中 {sum(1 for r in n if r.get('hit'))} 多 {sum(1 for r in n if r.get('extra'))}（手動 {sum(1 for r in b if r.get('gt'))}）")


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("data"); d.add_argument("--eval", default=str(ROOT / "ocr/results/figcrop_gt/baseline.json"))
    d.add_argument("--gt-dir", default=str(ROOT / "ocr/results/figcrop_gt")); d.add_argument("--long", type=int, default=1600)
    t = sub.add_parser("train"); t.add_argument("--fold", type=int, default=0); t.add_argument("--epochs", type=int, default=1)
    t.add_argument("--batch", type=int, default=2); t.add_argument("--lr", type=float, default=2e-5); t.add_argument("--warmup", type=int, default=10)
    t.add_argument("--limit", type=int, default=0); t.add_argument("--freeze", action="store_true"); t.add_argument("--tag")
    o = sub.add_parser("onnx"); o.add_argument("--run", required=True)
    o.add_argument("--ref", default=str(Path.home() / ".venvs/zizi-exam/Lib/site-packages/rapid_layout/models/pp_doc_layoutv3.onnx"))
    e = sub.add_parser("eval"); e.add_argument("--onnx", required=True); e.add_argument("--fold", type=int, default=0)
    e.add_argument("--eval", default=str(ROOT / "ocr/results/figcrop_gt/baseline.json")); e.add_argument("--gt-dir", default=str(ROOT / "ocr/results/figcrop_gt"))
    a = ap.parse_args()
    {"data": build, "train": train, "onnx": to_onnx, "eval": evaluate}[a.cmd](a)


if __name__ == "__main__":
    main()
