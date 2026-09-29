#!/usr/bin/env python3
"""評測：把 OCR 輸出跟「標準答案」逐欄比對，算準確率、列錯誤。

用法：
    python3 eval.py ocr_out.json ground_truth.json
兩個檔都是同一套 schema（見 schema.md）；ground_truth 是人工核對過的正確版。
純標準函式庫（difflib），不需安裝套件。
"""
import argparse
import difflib
import json
import sys


def norm(s):
    if s is None:
        return ""
    return " ".join(str(s).split())


def sim(a, b):
    """文字相似度 0~1（字元層級）。兩邊都空算 1。"""
    a, b = norm(a), norm(b)
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def exact(a, b):
    return 1.0 if norm(a) == norm(b) else 0.0


def options_similarity(gt_opts, ocr_opts):
    """依 label 對齊比對選項文字，回傳平均相似度（GT 沒選項則回 None）。"""
    gt = {norm(o.get("label")): o.get("text", "") for o in (gt_opts or [])}
    ocr = {norm(o.get("label")): o.get("text", "") for o in (ocr_opts or [])}
    if not gt:
        return None
    scores = [sim(text, ocr.get(label, "")) for label, text in gt.items()]
    return sum(scores) / len(scores)


def evaluate(ocr, gt):
    gt_qs = {q.get("no"): q for q in gt.get("questions", [])}
    ocr_qs = {q.get("no"): q for q in ocr.get("questions", [])}
    matched = [n for n in gt_qs if n in ocr_qs]
    missing = [n for n in gt_qs if n not in ocr_qs]          # 該有卻沒抓到
    extra = [n for n in ocr_qs if n not in gt_qs]            # 多抓的（幻覺/錯號）

    acc = {"stem": [], "option": [], "answer": [], "type": [], "points": []}
    errors = []
    for n in matched:
        g, o = gt_qs[n], ocr_qs[n]
        s_stem = sim(g.get("stem"), o.get("stem"))
        acc["stem"].append(s_stem)
        if s_stem < 0.9:
            errors.append({"no": n, "field": "stem", "score": round(s_stem, 3),
                           "expected": norm(g.get("stem"))[:80], "got": norm(o.get("stem"))[:80]})

        s_opt = options_similarity(g.get("options"), o.get("options"))
        if s_opt is not None:
            acc["option"].append(s_opt)
            if s_opt < 0.9:
                errors.append({"no": n, "field": "options", "score": round(s_opt, 3),
                               "expected": [norm(x.get("text"))[:40] for x in g.get("options", [])],
                               "got": [norm(x.get("text"))[:40] for x in o.get("options", [])]})

        if g.get("answer") is not None:
            a = exact(g.get("answer"), o.get("answer"))
            acc["answer"].append(a)
            if a < 1:
                errors.append({"no": n, "field": "answer", "score": 0,
                               "expected": g.get("answer"), "got": o.get("answer")})

        t = exact(g.get("type"), o.get("type"))
        acc["type"].append(t)
        if t < 1:
            errors.append({"no": n, "field": "type", "score": 0,
                           "expected": g.get("type"), "got": o.get("type")})

        if g.get("points") is not None:
            acc["points"].append(exact(g.get("points"), o.get("points")))

    def avg(xs):
        return round(sum(xs) / len(xs), 4) if xs else None

    field_scores = {k + "_" + ("similarity" if k in ("stem", "option") else "accuracy"): avg(v)
                    for k, v in acc.items()}
    # 綜合分數：偵測率 × 逐欄平均（只計有值的欄位）
    detect = len(matched) / len(gt_qs) if gt_qs else 0.0
    field_vals = [v for v in field_scores.values() if v is not None]
    field_mean = sum(field_vals) / len(field_vals) if field_vals else 0.0
    overall = round(detect * field_mean, 4)

    return {
        "gt_questions": len(gt_qs),
        "ocr_questions": len(ocr_qs),
        "matched": len(matched),
        "missing_no": missing,
        "extra_no": extra,
        "question_recall": round(detect, 4),
        "field_scores": field_scores,
        "overall": overall,
        "errors": sorted(errors, key=lambda e: e["score"]),
    }


def print_report(r, title=""):
    if title:
        print(f"\n===== {title} =====")
    print(f"題數：標準 {r['gt_questions']} / OCR {r['ocr_questions']} / 對上 {r['matched']}")
    if r["missing_no"]:
        print(f"  漏抓題號：{r['missing_no']}")
    if r["extra_no"]:
        print(f"  多抓題號：{r['extra_no']}")
    print(f"題目偵測率：{r['question_recall']:.1%}")
    print("逐欄分數：")
    for k, v in r["field_scores"].items():
        print(f"  {k:22s} {'—' if v is None else f'{v:.1%}'}")
    print(f"綜合分數（偵測率×逐欄）：{r['overall']:.1%}")
    if r["errors"]:
        print(f"錯誤明細（{len(r['errors'])} 筆，分數低到高）：")
        for e in r["errors"][:30]:
            print(f"  題{e['no']} [{e['field']}] score={e['score']}  期望={e['expected']}  得到={e['got']}")


def main():
    ap = argparse.ArgumentParser(description="OCR 輸出 vs 標準答案 評測")
    ap.add_argument("ocr_json")
    ap.add_argument("gt_json")
    ap.add_argument("--json", action="store_true", help="輸出機器可讀 JSON 報告")
    args = ap.parse_args()
    with open(args.ocr_json, encoding="utf-8") as f:
        ocr = json.load(f)
    with open(args.gt_json, encoding="utf-8") as f:
        gt = json.load(f)
    r = evaluate(ocr, gt)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print_report(r)


if __name__ == "__main__":
    sys.exit(main())
