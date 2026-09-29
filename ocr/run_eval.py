#!/usr/bin/env python3
"""批次評測：對 testset/ 裡每份考卷跑 OCR、跟標準答案比對、彙總準確率。

testset/ 命名規則（每份考卷一組）：
    <名稱>.gt.json          ← 人工核對過的「標準答案」（schema 見 schema.md）
    <名稱>.png / .jpg / .pdf ← 該考卷的圖片；多頁用 <名稱>.1.png、<名稱>.2.png…

用法：
    python3 run_eval.py                 # 跑 ocr/testset/，結果寫 ocr/results/
    python3 run_eval.py --testset <dir> --out <dir>

會輸出：每份的 <名稱>.ocr.json、彙總報告 summary.json、以及終端摘要。
這支就是「評測 agent」的手腳；ocr-trainer 子代理讀它的報告來迭代 prompt。
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ocr import run_ocr          # noqa: E402
from eval import evaluate, print_report  # noqa: E402

IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".pdf")


def find_images(testset, name):
    """找 <name> 對應的圖片，支援多頁 <name>.1.png…，依檔名排序。"""
    hits = []
    for p in sorted(glob.glob(os.path.join(testset, name + "*"))):
        if p.endswith(".gt.json"):
            continue
        if os.path.splitext(p)[1].lower() in IMG_EXTS:
            hits.append(p)
    return hits


def main():
    ap = argparse.ArgumentParser(description="OCR 批次評測")
    ap.add_argument("--testset", default=os.path.join(HERE, "testset"))
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    gts = sorted(glob.glob(os.path.join(args.testset, "*.gt.json")))
    if not gts:
        print(f"⚠ {args.testset} 裡沒有 *.gt.json。請先放測試資料（見 schema.md / README.md）。")
        return 1

    reports = []
    for gt_path in gts:
        name = os.path.basename(gt_path)[:-len(".gt.json")]
        images = find_images(args.testset, name)
        if not images:
            print(f"⚠ {name}：找不到對應圖片，略過。")
            continue
        print(f"\n▶ {name}（{len(images)} 頁）…")
        try:
            ocr = run_ocr(images)
        except Exception as e:  # noqa: BLE001
            print(f"  ❌ OCR 失敗：{e}")
            reports.append({"name": name, "error": str(e)})
            continue
        with open(os.path.join(args.out, name + ".ocr.json"), "w", encoding="utf-8") as f:
            json.dump(ocr, f, ensure_ascii=False, indent=2)
        with open(gt_path, encoding="utf-8") as f:
            gt = json.load(f)
        r = evaluate(ocr, gt)
        r["name"] = name
        reports.append(r)
        print_report(r, title=name)

    ok = [r for r in reports if "overall" in r]
    summary = {
        "exams": len(reports),
        "evaluated": len(ok),
        "failed": [r["name"] for r in reports if "error" in r],
        "mean_overall": round(sum(r["overall"] for r in ok) / len(ok), 4) if ok else None,
        "mean_recall": round(sum(r["question_recall"] for r in ok) / len(ok), 4) if ok else None,
        "per_exam": [{"name": r["name"], "overall": r["overall"],
                      "recall": r["question_recall"]} for r in ok],
        "details": reports,
    }
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n========== 彙總 ==========")
    if ok:
        print(f"平均綜合分數：{summary['mean_overall']:.1%}   平均偵測率：{summary['mean_recall']:.1%}")
        for e in sorted(ok, key=lambda r: r["overall"])[:10]:
            print(f"  {e['name']:20s} 綜合 {e['overall']:.1%}")
    if summary["failed"]:
        print(f"失敗：{summary['failed']}")
    print(f"\n報告寫入：{os.path.join(args.out, 'summary.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
