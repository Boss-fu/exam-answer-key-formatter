#!/usr/bin/env python3
"""題庫評測：OCR 文字逐題對照「題庫 Excel」（題目欄）——量最終目標 PDF→題庫 Excel 的準確度。

正解：待匯入資料夾裡人工整理好的 <卷>_題目.xlsx（欄位：題號、題目…）。
輸出：ocr/results/qbank_bench/<卷>/<卷>.txt（pyexam 產物）。
    python ocr/qbank_eval.py [--bench ocr/results/qbank_bench] [--gold ~/Desktop/待匯入] [-v]
指標：
  題數：輸出 Word 的題目段落（茲茲題號清單）數 vs 正解題數——切題是否正確；
  字元正確率：每題正解在整份輸出文字中找最相近的一段（rapidfuzz 對齊），算 1-CER——跟切題無關的純辨識品質。
"""
import argparse, glob, os, re, unicodedata
import docx, openpyxl
from rapidfuzz import fuzz


def word_questions(d, n):
    """用 to_zizi_word 從 pyexam 結果建茲茲 Word，數題目段落（numId 18，跟 qcount 同一套）。"""
    import sys, tempfile
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import to_zizi_word as Z
    with tempfile.TemporaryDirectory() as out:
        try:
            p, _, _ = Z.build(d, out, dpi=300, src_name=n + ".pdf")
        except Exception:
            return 0
        return sum('<w:numId w:val="18"/>' in q._p.xml for q in docx.Document(p).paragraphs)

RE_Q = re.compile(r"^\s*(?:[（(]\s*[A-Ea-e]?\s*[）)]\s*)?(\d{1,2})\s*[\.．、]")


def norm(s):
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = re.sub(r"〔含圖\s*\d*〕|⚠需確認|【[^】]*】", "", s)
    # 正解 Excel 的公式是 LaTeX（$100\,\text{g}$）；比的是文字，所以去掉 LaTeX 外殼
    s = re.sub(r"\\(?:text|mathrm|mathbf|operatorname)\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\circ", "°", s); s = re.sub(r"\\(?:times)", "×", s); s = re.sub(r"\\[,;:! ]|\\quad|\$", "", s)
    s = re.sub(r"[_^]\{([^}]*)\}", r"\1", s); s = re.sub(r"[_^](\w)", r"\1", s)
    return re.sub(r"[\s　]+", "", s)


def cer(ref, hyp):
    if not ref: return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return min(1.0, prev[-1] / len(ref))


def split_questions(txt):
    qs, cur, want = {}, None, 1
    for line in txt.splitlines():
        if line.startswith("【第"): continue
        m = RE_Q.match(line)
        n = int(m.group(1)) if m else 0
        # 題號要大致遞增，避免選項或內文裡的數字被當成新題；前面漏切時，後面「題號＋中文題幹」的行仍要接得上
        if m and n not in qs and (want - 1 <= n <= want + 8 or (n > (cur or 0) and re.match(r"[（(）)\s]*[\u4e00-\u9fff]", line[m.end():]))):
            cur = n; want = cur + 1; qs[cur] = line[m.end():]
        elif cur is not None:
            qs[cur] += line
    return qs


def gold(xlsx):
    ws = openpyxl.load_workbook(xlsx, read_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True)); h = list(rows[0])
    iq, it = h.index("題號"), h.index("題目")
    out = {}
    for r in rows[1:]:
        try: out[int(str(r[iq]).strip())] = str(r[it] or "")
        except (TypeError, ValueError): pass
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--bench", default="ocr/results/qbank_bench")
    ap.add_argument("--gold", default=os.path.expanduser("~/Desktop/待匯入")); ap.add_argument("-v", action="store_true"); a = ap.parse_args()
    tot_q = tot_hit = 0; accs = []
    print("| 考卷 | 正解題數 | Word 題數 | 平均字元正確率 | 低於 90% 的題 |"); print("|---|---:|---:|---:|---|")
    for d in sorted(glob.glob(os.path.join(a.bench, "*", ""))):
        n = os.path.basename(d.rstrip("/")); x = os.path.join(a.gold, n + ".xlsx"); t = os.path.join(d, n + ".txt")
        if not (os.path.exists(x) and os.path.exists(t)): continue
        g = gold(x); full = norm(open(t, encoding="utf-8").read())
        hit = list(range(1, word_questions(d, n) + 1)); acc = {}
        for q, txt in g.items():
            # 是非題的「(A)正確(B)錯誤」是建庫時補的，原卷沒有，不算辨識錯
            ref = re.sub(r"\(A\)正確\(B\)錯誤$", "", norm(txt)); al = fuzz.partial_ratio_alignment(ref, full) if ref else None
            acc[q] = 1 - cer(ref, full[al.dest_start:al.dest_end]) if al else 0.0
        bad = [q for q in g if acc.get(q, 0) < .9]
        tot_q += len(g); tot_hit += (len(hit) == len(g)); accs += [acc.get(q, 0) for q in g]
        print(f"| {n.replace('_國七上二段_生物_題目', '')} | {len(g)} | {len(hit)} | {sum(acc.get(q, 0) for q in g) / max(1, len(g)):.1%} | {','.join(map(str, bad[:12]))}{'…' if len(bad) > 12 else ''} |")
        if a.v:
            for q in bad[:3]:
                al = fuzz.partial_ratio_alignment(norm(g[q]), full)
                print(f"    第{q}題 正解：{norm(g[q])[:70]}\n          輸出：{full[al.dest_start:al.dest_end][:70]}")
    print(f"\n**合計**：{tot_q} 題；題數完全正確的考卷 {tot_hit} 份；逐題平均字元正確率 {sum(accs) / max(1, len(accs)):.1%}，"
          f"≥95% 的題 {sum(x >= .95 for x in accs)}／{len(accs)}")


if __name__ == "__main__":
    main()
