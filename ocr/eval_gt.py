#!/usr/bin/env python3
"""有標準答案的 OCR 評測：我們的 pyexam vs 試卷寶，用同一份人工完成的 Word 當正解打分數。

testset 檔名規則（全部被 .gitignore 排除，考卷不進 git）：
    ocr/testset/<名稱>.pdf        原始掃描卷
    ocr/testset/<名稱>.gt.docx    人工完成的乾淨版（正解）
    ocr/testset/<名稱>.ss.docx    試卷寶轉出的 Word（對照組，可省略）

指標（簡體先轉繁體、只比中英數字；標點、空白、作答括號、題號格式不算）：
    順序  ＝正解與輸出的最長共同子序列／正解字數：正解的字有沒有「依正確順序」出現。
            多出來的內容（正解沒收的地科、圖內刻度）不扣分，所以最能代表閱讀順序對不對
    CER   ＝編輯距離／正解字數，會受閱讀順序影響，多出來的內容也會扣分
    漏字  ＝正解裡的字在輸出中找不到的比例（不看順序，純「字有沒有認出來」）
    多字  ＝輸出多出來、正解沒有的字（手寫、圖內刻度、雜訊）
    圖    ＝裁出的附圖數 / 正解 Word 的圖片數

用法：
    python3 ocr/eval_gt.py                     # 全部（pyexam 結果有快取，改了程式加 --fresh）
    python3 ocr/eval_gt.py 百齡 --diff          # 只跑檔名含「百齡」的，並列出最常認錯的字
    OCR_MODELS_DIR=ocr/models/server python3 ocr/eval_gt.py --tag server   # 換模型比較
輸出：ocr/results/gt_eval_<tag>.md
"""
import argparse
import collections
import difflib
import glob
import json
import os
import re
import shutil
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
TESTSET = os.path.join(HERE, "testset")
RESULTS = os.path.join(HERE, "results")
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DPI = 350                                         # 跟 local-app 呼叫 pyexam 的解析度一致
KEEP = re.compile(r"[0-9A-Za-z㐀-鿿]")


_cc = None


def norm(t):
    """簡體一律先轉繁體再比（試卷寶有時整份輸出簡體）：這裡量的是「字認對沒」，不是字形"""
    global _cc
    if _cc is None:
        import opencc
        _cc = opencc.OpenCC("s2t")
    t = _cc.convert(unicodedata.normalize("NFKC", t))
    t = re.sub(r"[\(（]\s*[A-Ea-e]?\s*[\)）]", "", t)          # 作答括號 ( B )、(　)
    return "".join(KEEP.findall(t))


def docx_text(path):
    """段落＋表格（合併儲存格只算一次）＋文字方塊；依文件順序走 body，避免表格被排到最後"""
    import docx
    d = docx.Document(path)
    parts = []
    for el in d.element.body.iter(W_NS + "p"):
        # 文字方塊裡的段落也會被 iter 到：每個字只算給「最近的那一層」段落，避免重複
        parts.append("".join(t.text or "" for t in el.iter(W_NS + "t") if _owner_p(t) is el))
    return "\n".join(parts)


def _owner_p(node):
    node = node.getparent()
    while node is not None and node.tag != W_NS + "p":
        node = node.getparent()
    return node


def gt_questions(path):
    """正解的題數：用 Word 自動編號的就數主要編號清單的段落；把「1.」打成字的就找從 1 開始連續遞增的最長題號鏈。"""
    import docx
    d = docx.Document(path)
    ids = re.findall(r'<w:numId w:val="(\d+)"/>', d.element.body.xml)
    if len(ids) >= 10:
        return collections.Counter(ids).most_common(1)[0][1]
    nums = [int(m.group(1)) for m in (re.match(r"\s*(\d{1,2})\s*[.．、]", p.text) for p in d.paragraphs) if m]
    want = 1
    for n in nums:
        if n == want:
            want += 1
    return want - 1


def docx_images(path):
    import zipfile
    with zipfile.ZipFile(path) as z:
        x = z.read("word/document.xml").decode("utf-8", "ignore")
    return x.count("<w:drawing") + x.count("<w:pict")


def pyexam_text(txt, tables=None):
    """pyexam 的 .txt → 純文字：〔表格 k〕換回儲存格內容（同頁第 k 個表），頁碼標頭與佔位都拿掉"""
    if tables:
        out, page = [], 0
        for ln in txt.splitlines():
            mp = re.match(r"【第 (\d+) 頁", ln)
            if mp:
                page = int(mp.group(1))
            mt = re.match(r"〔表格 (\d+)〕$", ln.strip())
            t = tables.get(f"p{page}_t{mt.group(1)}") if mt else None
            out.append("\n".join("".join(c[0] for c in r) for r in t["rows"]) if t else ln)
        txt = "\n".join(out)
    t = re.sub(r"【第 \d+ 頁[^】]*】", "", txt)
    t = re.sub(r"〔表格[^〕]*〕", "", t)
    t = t.replace("⚠需確認", "")
    t = re.sub(r"〔含圖[^〕]*〕", "", t)
    return t


def run_pyexam(pdf, out_dir, fresh):
    txt = os.path.join(out_dir, os.path.splitext(os.path.basename(pdf))[0] + ".txt")
    meta = os.path.join(out_dir, "meta.json")
    if os.path.exists(txt) and os.path.exists(meta) and not fresh:
        return json.load(open(meta, encoding="utf-8"))
    sys.path.insert(0, HERE)
    import pyexam
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)
    t0 = time.time()
    pyexam.process(pdf, out_dir, dpi=DPI)
    m = {"sec": time.time() - t0, "figs": len(glob.glob(os.path.join(out_dir, "*_fig*.png"))), "txt": txt}
    json.dump(m, open(meta, "w", encoding="utf-8"), ensure_ascii=False)
    return m


def score(ref, hyp, conf=None):
    from rapidfuzz.distance import LCSseq, Levenshtein
    n = max(1, len(ref))
    rc, hc = collections.Counter(ref), collections.Counter(hyp)
    if conf is not None:
        sm = difflib.SequenceMatcher(None, ref, hyp, autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "replace" and i2 - i1 == j2 - j1 and i2 - i1 <= 3:
                for x, y in zip(ref[i1:i2], hyp[j1:j2]):
                    conf[(x, y)] += 1
    return {"cer": Levenshtein.distance(ref, hyp) / n, "order": LCSseq.similarity(ref, hyp) / n,
            "miss": sum((rc - hc).values()) / n, "extra": sum((hc - rc).values()) / n}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kw", nargs="?", default="")
    ap.add_argument("--fresh", action="store_true", help="重跑 pyexam，不用快取")
    ap.add_argument("--diff", action="store_true", help="列出最常認錯的字")
    ap.add_argument("--tag", default="mobile", help="結果標籤（換模型或參數時用來區分快取）")
    ap.add_argument("--set", default="testset", help="testset（開發用）或 holdout（最後驗收用，平常不要拿來調程式）")
    a = ap.parse_args()

    global TESTSET
    TESTSET = os.path.join(HERE, a.set)
    gts = sorted(glob.glob(os.path.join(TESTSET, "*.gt.docx")))
    gts = [g for g in gts if a.kw in os.path.basename(g)]
    if not gts:
        print("ocr/testset/ 沒有 <名稱>.gt.docx；規則見本檔開頭說明。")
        return 1
    rows, tot = [], collections.defaultdict(float)
    conf_us, conf_ss = collections.Counter(), collections.Counter()
    for g in gts:
        name = os.path.basename(g)[:-len(".gt.docx")]
        pdf = os.path.join(TESTSET, name + ".pdf")
        if not os.path.exists(pdf):
            continue
        ref = norm(docx_text(g))
        m = run_pyexam(pdf, os.path.join(RESULTS, "gt_eval", a.tag, name), a.fresh)
        tj = m["txt"][:-4] + "_tables.json"
        tables = json.load(open(tj, encoding="utf-8")) if os.path.exists(tj) else None
        us = score(ref, norm(pyexam_text(open(m["txt"], encoding="utf-8").read(), tables)), conf_us if a.diff else None)
        ss_path = os.path.join(TESTSET, name + ".ss.docx")
        ss = score(ref, norm(docx_text(ss_path)), conf_ss if a.diff else None) if os.path.exists(ss_path) else None
        import tempfile
        import to_zizi_word
        wdir = os.path.join(RESULTS, "gt_eval", a.tag, name, "word")
        shutil.rmtree(wdir, ignore_errors=True)
        try:
            docx_out, _, st = to_zizi_word.build(os.path.dirname(m["txt"]), wdir, dpi=DPI, src_name=name + ".pdf")
            import docx as _d
            _d.Document(docx_out)                     # 打得開才算
            qn, word_ok = st["questions"], True
        except Exception as e:
            qn, word_ok = 0, False
            print(f"  ✘ {name}: Word 產生失敗 {e}", flush=True)
        r = {"name": name, "chars": len(ref), "us": us, "ss": ss, "sec": m["sec"],
             "q": qn, "gt_q": gt_questions(g), "word_ok": word_ok,
             "figs": m["figs"], "gt_figs": docx_images(g), "ss_figs": docx_images(ss_path) if ss else None}
        rows.append(r)
        w = len(ref)
        tot["chars"] += w
        for k in ("cer", "miss", "extra", "order"):
            tot["us_" + k] += us[k] * w
            if ss:
                tot["ss_" + k] += ss[k] * w
                tot["ssw"] += w if k == "cer" else 0
        print(f"  {name}: 題數 {qn}/{r['gt_q']}　我們 漏字 {us['miss']:.1%}／順序 {us['order']:.1%}"
              + (f"　試卷寶 漏字 {ss['miss']:.1%}／順序 {ss['order']:.1%}" if ss else "") + f"　{m['sec']:.0f}s", flush=True)

    pct = lambda v: f"{v:.1%}"
    lines = [f"# OCR 評測（有正解）— {a.tag}", "",
             "| 考卷 | 字數 | 我們 漏字 | 試卷寶 漏字 | 我們 順序 | 試卷寶 順序 | 我們 多字 | 試卷寶 多字 | 我們 CER | 試卷寶 CER | 圖 我們/試卷寶/正解 | 秒 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|"]
    for r in rows:
        u, s = r["us"], r["ss"] or {}
        lines.append(f"| {r['name']} | {r['chars']} | {pct(u['miss'])} | {pct(s['miss']) if s else '-'} | "
                     f"{pct(u['order'])} | {pct(s['order']) if s else '-'} | "
                     f"{pct(u['extra'])} | {pct(s['extra']) if s else '-'} | {pct(u['cer'])} | {pct(s['cer']) if s else '-'} | "
                     f"{r['figs']}/{r['ss_figs'] if s else '-'}/{r['gt_figs']} | {r['sec']:.0f} |")
    c, sw = tot["chars"], tot["ssw"] or 1
    lines.append(f"| **加權平均** | {int(c)} | **{pct(tot['us_miss'] / c)}** | **{pct(tot['ss_miss'] / sw)}** | "
                 f"**{pct(tot['us_order'] / c)}** | **{pct(tot['ss_order'] / sw)}** | "
                 f"{pct(tot['us_extra'] / c)} | {pct(tot['ss_extra'] / sw)} | {pct(tot['us_cer'] / c)} | {pct(tot['ss_cer'] / sw)} | | |")
    if a.diff:
        for label, cf in (("我們", conf_us), ("試卷寶", conf_ss)):
            lines += ["", f"**{label}最常認錯（正解→輸出）**：" + "　".join(f"{x}→{y}×{n}" for (x, y), n in cf.most_common(40))]
    md = "\n".join(lines)
    os.makedirs(RESULTS, exist_ok=True)
    out = os.path.join(RESULTS, f"gt_eval_{a.tag}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(md + "\n")
    with open(out[:-3] + ".json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("\n" + md + f"\n\n→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
