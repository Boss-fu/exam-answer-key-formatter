#!/usr/bin/env python3
"""把兩次 eval_gt 的結果（改前／改後）畫成一頁 HTML 報告，順便附一段三方對照原文。

用法：
    python3 ocr/report_gt.py --before mobile --after v3 --sample 112-2-3_G8_石牌國中 -o ocr/results/report.html
--exclude 可排除正解對不上掃描卷的考卷（例如正解是別的版本）。
"""
import argparse
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
TESTSET = os.path.join(HERE, "testset")
sys.path.insert(0, HERE)


def load(tag):
    with open(os.path.join(RESULTS, f"gt_eval_{tag}.json"), encoding="utf-8") as f:
        return {r["name"]: r for r in json.load(f)}


def wavg(rows, side, key):
    rows = [r for r in rows if r.get(side)]
    n = sum(r["chars"] for r in rows) or 1
    return sum(r[side][key] * r["chars"] for r in rows) / n


def excerpt_lines(text, n):
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("【第") or ln.startswith("───"):
            continue
        out.append(ln.replace("⚠需確認", "").strip())
        if len(out) >= n:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", default="mobile")
    ap.add_argument("--after", default="v3")
    ap.add_argument("--sample", default="")
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("-o", "--out", default=os.path.join(RESULTS, "report.html"))
    a = ap.parse_args()
    import eval_gt

    B, A = load(a.before), load(a.after)
    names = [n for n in A if n in B and n not in a.exclude]
    rb, ra = [B[n] for n in names], [A[n] for n in names]
    m = {
        "miss": (wavg(rb, "us", "miss"), wavg(ra, "us", "miss"), wavg(ra, "ss", "miss")),
        "order": (wavg(rb, "us", "order"), wavg(ra, "us", "order"), wavg(ra, "ss", "order")),
        "extra": (wavg(rb, "us", "extra"), wavg(ra, "us", "extra"), wavg(ra, "ss", "extra")),
    }
    secs = sum(r["sec"] for r in ra) / max(1, len(ra))
    pages = {}
    for n in names:
        try:
            import pymupdf
            pages[n] = pymupdf.open(os.path.join(TESTSET, n + ".pdf")).page_count
        except Exception:
            pages[n] = 0
    per_page = sum(r["sec"] for r in ra) / max(1, sum(pages.values()))

    rows = []
    for n in names:
        b, x = B[n], A[n]
        rows.append({"name": n, "chars": x["chars"],
                     "ob": b["us"]["order"], "oa": x["us"]["order"], "os": x["ss"]["order"] if x["ss"] else None,
                     "mb": b["us"]["miss"], "ma": x["us"]["miss"], "ms": x["ss"]["miss"] if x["ss"] else None,
                     "figs": x["figs"], "ssf": x["ss_figs"], "gtf": x["gt_figs"]})
    rows.sort(key=lambda r: r["oa"] - r["ob"], reverse=True)

    sample = None
    if a.sample:
        n = a.sample
        gt = eval_gt.docx_text(os.path.join(TESTSET, n + ".gt.docx"))
        ss = eval_gt.docx_text(os.path.join(TESTSET, n + ".ss.docx"))
        us_txt = open(os.path.join(RESULTS, "gt_eval", a.after, n, n + ".txt"), encoding="utf-8").read()
        sample = {"name": n, "gt": excerpt_lines(gt, 14), "us": excerpt_lines(us_txt, 14),
                  "ss": excerpt_lines(ss, 14)}

    data = {"m": m, "rows": rows, "n": len(names), "chars": sum(r["chars"] for r in ra),
            "per_page": per_page, "excluded": a.exclude, "sample": sample}
    tpl = open(os.path.join(HERE, "report_gt.html"), encoding="utf-8").read()
    out = tpl.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(out)
    print(a.out)


if __name__ == "__main__":
    main()
