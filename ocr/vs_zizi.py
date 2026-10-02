# -*- coding: utf-8 -*-
"""對打：我們（ocr/to_zizi_word）vs 俊瑋的卷土重來（zizi-exam），同一批考卷、同一套評分（都用最終 Word 的文字）。
python vs_zizi.py A   我們的主場：ocr/testset 的 13 份三件組
python vs_zizi.py B   他的主場：zizi-exam 的 samples/ + samples/truth/*.docx
結果寫到 scratchpad/vs/<A|B>.json，可重跑（已有的 Word 會沿用）。"""
import glob
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SP = os.environ.get("VS_WORK", os.path.join(REPO, "ocr", "results"))   # 對打的工作資料夾（gitignore）
ZZ = os.environ.get("ZIZI_DIR", "")   # 卷土重來（zizi-exam）的程式資料夾，見 docs/OCR交接_Codex.md
PY = os.environ.get("OCR_PY", sys.executable)   # 跑 to_zizi_word／卷土重來用的 Python（要裝好 ocr/requirements.txt）
sys.path.insert(0, os.path.join(REPO, "ocr"))
import eval_gt as E  # noqa: E402

EXCLUDE = {"113-1-1_G8_陽明國中"}           # 正解跟掃描卷對不上


def zizi_word(pdf, tag):
    """跑卷土重來（子程序，免得它 segfault 拖垮整批），回傳它的 docx"""
    out = os.path.join(SP, "vs", tag, "zizi_out")
    work = os.path.join(SP, "vs", tag, "zizi_work", os.path.splitext(os.path.basename(pdf))[0])
    os.makedirs(out, exist_ok=True)
    code = ("import sys,json; sys.path.insert(0, %r); from pathlib import Path; from app import pipeline; "
            "r = pipeline.run(Path(%r), Path(%r), Path(%r)); print('DOCX=' + r['docx'])") % (ZZ, pdf, work, out)
    p = subprocess.run([PY, "-c", code], cwd=ZZ, capture_output=True, text=True, timeout=1800)
    m = re.search(r"DOCX=(.+)", p.stdout)
    return m.group(1).strip() if m else None


def ours_word(pdf, tag):
    out = os.path.join(SP, "vs", tag, "ours", os.path.splitext(os.path.basename(pdf))[0])
    hit = glob.glob(os.path.join(out, "*.docx"))
    if hit:
        return hit[0]
    p = subprocess.run([PY, os.path.join(REPO, "ocr", "to_zizi_word.py"), pdf, "-o", out],
                       capture_output=True, text=True, timeout=1800)
    hit = glob.glob(os.path.join(out, "*.docx"))
    return hit[0] if hit else None


def qcount(docx_path):
    import docx
    x = docx.Document(docx_path).element.body.xml
    return len(re.findall(r'<w:numId w:val="18"/>', x))


def score_docx(docx_path, gt_path):
    if not docx_path or not os.path.exists(docx_path):
        return None
    ref = E.norm(E.docx_text(gt_path))
    hyp = E.norm(E.docx_text(docx_path))
    s = E.score(ref, hyp)
    s["q"] = qcount(docx_path)
    s["chars"] = len(ref)
    return s


def run(mode):
    if mode == "A":
        items = [(p, p[:-4] + ".gt.docx") for p in sorted(glob.glob(os.path.join(REPO, "ocr/testset/*.pdf")))
                 if os.path.exists(p[:-4] + ".gt.docx") and os.path.basename(p)[:-4] not in EXCLUDE]
    else:
        items = [(os.path.join(ZZ, "samples", os.path.basename(t)[:-5] + ".pdf"), t)
                 for t in sorted(glob.glob(os.path.join(ZZ, "samples/truth/*.docx")))]
    res_path = os.path.join(SP, "vs", mode + ".json")
    os.makedirs(os.path.dirname(res_path), exist_ok=True)
    res = json.load(open(res_path)) if os.path.exists(res_path) else {}
    for pdf, gt in items:
        name = os.path.basename(pdf)[:-4]
        if name in res and res[name].get("zizi") and res[name].get("ours"):
            continue
        r = res.get(name, {"gt_q": E.gt_questions(gt)})
        if not r.get("ours"):
            r["ours"] = score_docx(ours_word(pdf, mode), gt)
        if not r.get("zizi"):
            r["zizi"] = score_docx(zizi_word(pdf, mode), gt)
        res[name] = r
        json.dump(res, open(res_path, "w"), ensure_ascii=False, indent=1)
        f = lambda s, k: f"{s[k]:.1%}" if s else "失敗"
        print(f"{name}: 題數 我們 {r['ours']['q'] if r['ours'] else '-'} / 卷土 {r['zizi']['q'] if r['zizi'] else '-'} / 正解 {r['gt_q']}"
              f"　順序 我們 {f(r['ours'], 'order')} / 卷土 {f(r['zizi'], 'order')}"
              f"　漏字 我們 {f(r['ours'], 'miss')} / 卷土 {f(r['zizi'], 'miss')}", flush=True)
    # 總表（只算兩邊都成功的）
    both = [v for v in res.values() if v.get("ours") and v.get("zizi")]
    n = sum(v["ours"]["chars"] for v in both) or 1
    for who in ("ours", "zizi"):
        o = sum(v[who]["order"] * v[who]["chars"] for v in both) / n
        m = sum(v[who]["miss"] * v[who]["chars"] for v in both) / n
        qok = sum(1 for v in both if v[who]["q"] == v["gt_q"])
        print(f"== {who}: 順序 {o:.1%}　漏字 {m:.1%}　題數全對 {qok}/{len(both)}　（失敗 {sum(1 for v in res.values() if not v.get(who))} 份）")


if __name__ == "__main__":
    run(sys.argv[1])
