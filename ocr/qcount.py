#!/usr/bin/env python3
"""快速量「題數」：不重跑 OCR，直接用 eval_gt 的 OCR 快取（ocr/results/gt_eval/<tag>/）重新切題、跟正解比題數。
改 to_zizi_word.py 的切題規則後先跑這支（幾十秒），總誤差變小、而且沒有任何一份變差才保留。

用法：
    python3 ocr/qcount.py v8                # 全部
    python3 ocr/qcount.py v8 --diag 百齡    # 另外列出含「百齡」的考卷漏了／黏了哪幾題
    python3 ocr/qcount.py v8 --ai claude    # 用 AI（ai_judge.py）判斷題目邊界；--ai ollama 用本機模型
總誤差＝各份 |我們題數 − 正解題數| 的加總；不計入正解只收部分內容的考卷（PARTIAL）。"""
import glob
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eval_gt as E          # noqa: E402
import to_zizi_word as Z     # noqa: E402
from testset_rules import MISMATCH, PARTIAL  # noqa: E402


def gt_stems(gt_path):
    import docx
    d = docx.Document(gt_path)
    ids = re.findall(r'<w:numId w:val="(\d+)"/>', d.element.body.xml)
    main = max(set(ids), key=ids.count) if len(ids) >= 10 else None
    out = []
    for p in d.paragraphs:
        if main and f'<w:numId w:val="{main}"/>' in p._p.xml:
            out.append(p.text)
        elif not main and re.match(r"\s*\d{1,2}\s*[.．、]", p.text):
            out.append(re.sub(r"^\s*\d{1,2}\s*[.．、]\s*(\(\s*[A-E]?\s*\))?", "", p.text))
    return out


def diag(name, docx_path):
    import docx
    on = [E.norm(p.text)[:200] for p in docx.Document(docx_path).paragraphs if '<w:numId w:val="18"/>' in p._p.xml]
    for i, g in enumerate(gt_stems(os.path.join(HERE, "testset", name + ".gt.docx")), 1):
        key = E.norm(g)[:8]
        hit = [t for t in on if key and key in t]
        if not hit:
            print(f"    漏？ 第{i}題：{g[:36]}")
        elif all(t.index(key) > 15 for t in hit):
            print(f"    黏  第{i}題（併進前一題）：{g[:30]}")


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "v8"
    kw = sys.argv[sys.argv.index("--diag") + 1] if "--diag" in sys.argv else None
    ai = sys.argv[sys.argv.index("--ai") + 1] if "--ai" in sys.argv else None   # claude／ollama：先問 AI（有快取）再切題
    tot = ok = n = 0
    tmp = tempfile.mkdtemp()
    for d in sorted(glob.glob(os.path.join(HERE, "results", "gt_eval", tag, "*", ""))):
        name = os.path.basename(d.rstrip("/"))
        gt = os.path.join(HERE, "testset", name + ".gt.docx")
        if name in MISMATCH or not os.path.exists(gt) or not glob.glob(d + "*.txt"):
            continue
        out = os.path.join(tmp, name)
        if ai:
            import ai_judge
            try:
                ai_judge.judge(d, ai)
            except Exception as ex:
                print(f"  ⚠ {name}: AI 失敗（{ex}），這份退回純規則")
        docx_path, _, st = Z.build(d, out, dpi=E.DPI, src_name=name + ".pdf", ai=ai)
        g = E.gt_questions(gt)
        mark = "✔" if st["questions"] == g else "✘"
        note = "（正解只收部分，不計）" if name in PARTIAL else ""
        print(f"{mark} {name:<26} {st['questions']:>3} / {g}{note}")
        if name not in PARTIAL:
            tot += abs(st["questions"] - g)
            ok += st["questions"] == g
            n += 1
        if kw and kw in name:
            diag(name, docx_path)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n題數全對 {ok}/{n}　總誤差 {tot}")


if __name__ == "__main__":
    main()
