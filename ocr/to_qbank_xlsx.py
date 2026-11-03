#!/usr/bin/env python3
"""pyexam 結果 → 題庫匯入 Excel 草稿（欄位同「待匯入」的 <卷>_題目.xlsx）。

填得出來的欄位：年度、來源學校、考試名稱、題號、題目（題幹＋每個選項一行）、題組文章、題組ID、
圖表類型（無／表格；其他圖留白讓 AI 或人判斷）、待確認（辨識信心低、沒選項、有圖時標 Y）。
分類欄位（冊數、大單元、知識點、題型、配分…）與答案、詳解留白，交給題庫系統的 AI 步驟。
    python ocr/to_qbank_xlsx.py <pyexam 輸出資料夾> [-o 輸出.xlsx] [--name 112_南市復興_國七上二段_生物_題目]
"""
import argparse, os, re, sys, tempfile

import openpyxl
from openpyxl.styles import Alignment, Font

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import to_zizi_word as Z  # noqa: E402

COLS = ["年度", "來源學校", "考試名稱", "題號", "答案", "冊數", "冊名", "領域", "大單元", "子單元", "知識點", "題目摘要",
        "題型", "步驟分", "記憶分", "概念分", "圖表類型", "判斷依據", "待確認", "題目", "詳解", "題組文章", "題組ID"]
CN = "一二三四五六七八九"
SUBJ = {"生物": "生物", "理化": "理化", "地科": "地科", "自然": "自然"}


def exam_info(name):
    """檔名「112_南市復興_國七上二段_生物_題目」→ 年度、學校、考試名稱、題組 ID 前綴。認不出的欄位留空。"""
    m = re.match(r"(\d{3})_([^_]+)_國([七八九])([上下])([一二三四1-4])段(?:_([^_]+))?", name)
    if not m:
        return {"年度": "", "來源學校": "", "考試名稱": "", "gid": ""}
    year, school, grade, term, nth, subj = m.groups()
    school = school if re.search(r"(國中|高中|中學)$", school) else school + "國中"
    nth = CN[int(nth) - 1] if nth.isdigit() else nth
    subj = SUBJ.get(subj or "", subj or "")
    exam = f"{school}-{grade}年級-{year}-{term}學期-第{nth}次段考" + (f"-{subj}" if subj else "")
    return {"年度": year + term, "來源學校": school, "考試名稱": exam, "gid": f"{school}-{grade}年級-{year}{term}-第{nth}次段考"}


def clean(t):
    t = re.sub(r"\s*⚠需確認", "", t or "")
    t = re.sub(r"〔(?:含圖|表格)\s*\d*〕", "", t)
    return re.sub(r"[ 　]{2,}", " ", t).strip()


def question_text(q):
    stem = clean(q["stem"])
    if "圖片" in q["kinds"] or "表格" in q["kinds"]:
        stem += "〔含圖〕"
    opts = [clean(o) for o in q["opts"] if clean(o)]
    if q.get("tf") and not opts:
        opts = ["(A)正確", "(B)錯誤"]       # 是非題：題庫格式把正確／錯誤當成 (A)(B) 選項
    return "\n".join([stem] + opts)


def rows(pyexam_dir, name):
    info = exam_info(name)
    col = {}
    with tempfile.TemporaryDirectory() as tmp:
        Z.build(pyexam_dir, tmp, dpi=300, src_name=name + ".pdf", collect=col)
    out = []
    prev = 0
    for q in col.get("questions", []):
        # 題號：卷面認得到的印刷題號優先（比前一題大才採用），認不到才接續前一題 +1——
        # 這樣某一題黏題／多切只影響那一題，不會讓後面整串題號都位移
        num = q["pn"] if q.get("pn") and q["pn"] > prev else prev + 1
        prev = num
        kinds = q["kinds"]
        why = []
        if q["review"]:
            why.append("有辨識信心低的字")
        if not q["opts"]:
            why.append("沒抓到選項（非選題或選項在圖上）")
        if "圖片" in kinds:
            why.append("含圖，請確認圖表類型")
        out.append({
            "年度": info["年度"], "來源學校": info["來源學校"], "考試名稱": info["考試名稱"], "題號": str(num),
            "圖表類型": "表格" if kinds == ["表格"] else ("" if kinds else "無"),
            "判斷依據": "；".join(why), "待確認": "Y" if why else "",
            "題目": question_text(q), "題組文章": clean(q["group"]),
            "題組ID": "",
            "_gid": q["gid"],
        })
    # 題組 ID 用該題組第一題的「Excel 題號」（不是 Word 流水號）
    first = {}
    for r in out:
        if r["_gid"] is not None:
            first.setdefault(r["_gid"], r["題號"])
            r["題組ID"] = f"{info['gid']}-{first[r['_gid']]}" if info["gid"] else ""
    return out


def write(rs, path):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "題目"
    ws.append(COLS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rs:
        ws.append([r.get(k, "") for k in COLS])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["T"].width = 70; ws.column_dimensions["V"].width = 40
    wb.save(path)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("dir"); ap.add_argument("-o"); ap.add_argument("--name"); a = ap.parse_args()
    name = a.name or os.path.basename(a.dir.rstrip("/"))
    out = a.o or os.path.join(a.dir, name + "_草稿.xlsx")
    rs = rows(a.dir, name); write(rs, out)
    print(f"{out}：{len(rs)} 題，待確認 {sum(r['待確認'] == 'Y' for r in rs)} 題")


if __name__ == "__main__":
    main()
