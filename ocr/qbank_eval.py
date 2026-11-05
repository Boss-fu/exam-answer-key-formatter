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
    for k, v in {"rightarrow": "→", "to": "→", "leftarrow": "←", "uparrow": "↑", "downarrow": "↓", "sim": "~", "cdot": "·",
                 "pm": "±", "div": "÷", "le": "≦", "ge": "≧", "leq": "≦", "geq": "≧", "neq": "≠", "approx": "≒", "degree": "°"}.items():
        s = re.sub(r"\\" + k + r"(?![A-Za-z])", v, s)   # 正解 Excel 的 LaTeX 符號換成卷面上的字元，比的是辨識不是記法
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


def gold_rows(xlsx):
    ws = openpyxl.load_workbook(xlsx, read_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True)); h = list(rows[0])
    return {str(r[h.index("題號")]).strip(): {k: str(r[h.index(k)] or "") for k in ("題目", "題組ID", "題組文章")} for r in rows[1:] if r[h.index("題號")]}


def xlsx_eval(a):
    """最終目標的分數：草稿 Excel 跟正解 Excel 同一個題號的題目欄比字元正確率（切錯題號就直接算錯）。"""
    import to_qbank_xlsx as X
    accs, gid_ok, gid_n, exact = [], 0, 0, 0
    print("| 考卷 | 正解題數 | 草稿題數 | 同題號字元正確率 | ≥95% 題數 | 題組ID 一致 |"); print("|---|---:|---:|---:|---:|---:|")
    for d in sorted(glob.glob(os.path.join(a.bench, "*", ""))):
        n = os.path.basename(d.rstrip("/")); x = os.path.join(a.gold, n + ".xlsx")
        if not (os.path.exists(x) and os.path.exists(os.path.join(d, n + ".txt"))): continue
        if a.only and not any(k in n for k in a.only.split(",")): continue
        g = gold_rows(x); ours = {r["題號"]: r for r in X.rows(d, n)}
        acc = [1 - cer(norm(v["題目"]), norm(ours[q]["題目"])) if q in ours else 0.0 for q, v in g.items()]
        gi = [(v["題組ID"] == ours.get(q, {}).get("題組ID", "")) for q, v in g.items() if v["題組ID"] or ours.get(q, {}).get("題組ID")]
        accs += acc; gid_ok += sum(gi); gid_n += len(gi); exact += len(ours) == len(g)
        print(f"| {n.replace('_國七上二段_生物_題目', '')} | {len(g)} | {len(ours)} | {sum(acc) / max(1, len(acc)):.1%} | {sum(v >= .95 for v in acc)} | {sum(gi)}/{len(gi)} |")
    print(f"\n**Excel 合計**：{len(accs)} 題；題數全對 {exact} 份；同題號題目欄平均字元正確率 {sum(accs) / max(1, len(accs)):.1%}，"
          f"≥95% 的題 {sum(v >= .95 for v in accs)}／{len(accs)}；題組ID 一致 {gid_ok}／{gid_n}")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--bench", default="ocr/results/qbank_bench")
    ap.add_argument("--gold", default=os.environ.get("QBANK_GOLD") or next((p for p in ("ocr/results/qbank_gold", os.path.expanduser("~/Desktop/待匯入")) if os.path.isdir(p)), "ocr/results/qbank_gold")); ap.add_argument("-v", action="store_true")
    ap.add_argument("--only", default="", help="只評這幾份（逗號分隔、比對檔名片段，例如 113_陽明,114_大安）；Codex 沙箱 30 秒限制用")
    ap.add_argument("--xlsx", action="store_true", help="用 to_qbank_xlsx 產生的題庫 Excel 草稿逐題號比對（同題號的題目欄字元正確率、題組ID 一致率）")
    a = ap.parse_args()
    if a.xlsx:
        return xlsx_eval(a)
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
