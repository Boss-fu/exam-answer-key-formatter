# -*- coding: utf-8 -*-
"""把題庫 23 欄 CSV 直接轉成茲茲格式的「乾淨考卷 Word」。
—— 重用同一份 Claude 讀圖結果（CSV 的『題目』欄），不再讓 AI 讀第二次，省時間。
題目欄內的 $LaTeX$ 原樣保留（供 MathType Toggle TeX 轉換），〔含圖〕留為置中佔位。
用法：python3 csv_to_word.py <in.csv> <out.docx> [橫幅替換 JSON]
"""
import sys, os, re, csv, json
SKILL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exam-to-word")
os.environ.setdefault("EXAM_TEMPLATE", os.path.join(SKILL, "assets", "template.docx"))
sys.path.insert(0, os.path.join(SKILL, "scripts"))
import buildlib as B
from buildlib import FQ, FH, esc, p_section, tbl, finish

# ---- 文字「照原樣」輸出（不解析 _ * ^ ~ § 標記，才不會破壞 $LaTeX$）----
def runs_lit(text, font=FQ):
    out = []
    for tk in re.split(r'(\n|\t)', text):
        if tk == "\n": out.append(f'<w:r><w:rPr><w:rFonts {font}/></w:rPr><w:br/></w:r>')
        elif tk == "\t": out.append(f'<w:r><w:rPr><w:rFonts {font}/></w:rPr><w:tab/></w:r>')
        elif tk: out.append(f'<w:r><w:rPr><w:rFonts {font}/></w:rPr><w:t xml:space="preserve">{esc(tk)}</w:t></w:r>')
    return "".join(out)

def p_q(text):
    ppr = ('<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="18"/></w:numPr>'
           '<w:tabs><w:tab w:val="left" w:pos="3000"/><w:tab w:val="left" w:pos="5280"/>'
           '<w:tab w:val="left" w:pos="7560"/></w:tabs><w:adjustRightInd w:val="0"/>'
           '<w:snapToGrid w:val="0"/><w:spacing w:beforeLines="20" w:before="72" w:afterLines="20" w:after="72"/>'
           f'<w:rPr><w:rFonts {FQ}/></w:rPr></w:pPr>')
    return "<w:p>" + ppr + runs_lit(text) + "</w:p>"

def p_intro(text):
    ppr = ('<w:pPr><w:adjustRightInd w:val="0"/><w:snapToGrid w:val="0"/>'
           '<w:spacing w:beforeLines="30" w:before="108" w:afterLines="10" w:after="36"/>'
           '<w:ind w:left="480" w:hanging="240"/>'
           f'<w:rPr><w:rFonts {FQ}/></w:rPr></w:pPr>')
    return "<w:p>" + ppr + runs_lit("◎ " + text) + "</w:p>"

def note(text):
    ppr = ('<w:pPr><w:snapToGrid w:val="0"/><w:spacing w:beforeLines="10" w:before="36" '
           'w:afterLines="10" w:after="36"/><w:jc w:val="center"/>'
           f'<w:rPr><w:rFonts {FQ}/></w:rPr></w:pPr>')
    return "<w:p>" + ppr + runs_lit("〔" + text + "〕") + "</w:p>"

OPT_RE = re.compile(r'^\s*[\(（]\s*[A-DＡ-Ｄ]\s*[\)）]')

def split_stem_opts(q):
    lines = q.split("\n")
    start = next((i for i, l in enumerate(lines) if OPT_RE.match(l)), None)
    if start is None:
        return q.strip(), []
    stem = "\n".join(lines[:start]).strip()
    opts = [l.strip() for l in lines[start:] if l.strip()]
    return stem, opts

def build(in_csv, out_docx, repl):
    rows = list(csv.reader(open(in_csv, encoding="utf-8-sig")))
    rows = [r for r in rows if any(c.strip() for c in r)]
    head = [h.strip() for h in rows[0]]
    idx = {h: i for i, h in enumerate(head)}
    g = lambda r, n: (r[idx[n]] if n in idx and idx[n] < len(r) else "")
    data = [r for r in rows[1:] if g(r, "題號").strip().isdigit()]
    data.sort(key=lambda r: int(g(r, "題號")))

    B.start(out_docx + ".build", out_docx + ".figs")   # figs 用不到，給個路徑
    C = [p_section("單選題【每題 2.5 分，共 %d 題】" % len(data))]
    seen_group = set()
    for r in data:
        para = g(r, "題組文章") or (g(r, "題組") if "題組文章" not in idx else "")
        if para.strip():
            C.append(p_intro(para.strip()))
        stem, opts = split_stem_opts(g(r, "題目"))
        has_fig = "〔含圖〕" in stem or any("〔含圖〕" in o for o in opts)
        stem = stem.replace("〔含圖〕", "").strip()
        opts = [o.replace("〔含圖〕", "").strip() for o in opts]
        if has_fig:
            C.append(p_q(stem))
            C.append(note("含圖：請在此貼上截圖"))
            if opts:
                C.append(_opts_para("\n".join(opts)))
        else:
            # 選項短 → Tab 對齊同一行；長 → 各自換行
            def w(s): return sum(2 if ord(c) > 0x2000 else 1 for c in s)
            plain = [re.sub(r'\$[^$]*\$', 'x', o) for o in opts]
            tabbed = opts and len(opts) <= 4 and max((w(o) for o in plain), default=0) <= 13
            sep = "\t" if tabbed else "　"
            C.append(p_q(stem + ("\n" + sep.join(opts) if opts else "")))
    finish(C, out_docx, repl)
    import shutil
    for d in (out_docx + ".build", out_docx + ".figs"):
        shutil.rmtree(d, ignore_errors=True)

def _opts_para(t):
    return ('<w:p><w:pPr><w:adjustRightInd w:val="0"/><w:snapToGrid w:val="0"/>'
            '<w:spacing w:afterLines="20" w:after="72"/><w:ind w:left="960"/>'
            f'<w:rPr><w:rFonts {FQ}/></w:rPr></w:pPr>' + runs_lit(t) + '</w:p>')

if __name__ == "__main__":
    in_csv, out_docx = sys.argv[1], sys.argv[2]
    repl = json.loads(sys.argv[3]) if len(sys.argv) > 3 else []
    build(in_csv, out_docx, [tuple(x) for x in repl])
    print("ok", out_docx)
