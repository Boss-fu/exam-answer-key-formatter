#!/usr/bin/env python3
"""文字層 PDF／Word → 題庫 23 欄 CSV。

先在本機抽題；若提供 --reference-csv，僅以題號合併答案、範圍與既有詳解，
不呼叫 AI。適合先驗證題庫匯入格式，圖片題與低信心題留在待確認欄。
"""
import argparse
import csv
import difflib
import re
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

FIELDS = ["年度", "來源學校", "考試名稱", "題號", "答案", "冊數", "冊名", "領域",
          "大單元", "子單元", "知識點", "題目摘要", "題型", "步驟分", "記憶分",
          "概念分", "圖表類型", "判斷依據", "待確認", "題目", "詳解", "題組文章", "題組ID"]
QUESTION = re.compile(r"^\s*(\d{1,3})\s*[\.、．)]\s*(.*)$")
OPTION = re.compile(r"(?:\(\s*[A-DＡ-Ｄ]\s*\)|（\s*[A-DＡ-Ｄ]\s*）)")
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def clean(text):
    text = text.replace("\u00a0", " ").replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def docx_paragraphs(path):
    with ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    out = []
    for p in root.iter(W_NS + "p"):
        value = clean("".join(t.text or "" for t in p.iter(W_NS + "t")))
        if value:
            out.append(value)
    return out


def source_text(path):
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return "\n".join(docx_paragraphs(path))
    if suffix == ".pdf":
        with tempfile.TemporaryDirectory() as td:
            txt = Path(td) / "source.txt"
            subprocess.run(["pdftotext", "-layout", str(path), str(txt)], check=True)
            return txt.read_text(encoding="utf-8", errors="replace")
    raise ValueError("只支援 PDF 或 DOCX")


def split_questions(text):
    """支援 PDF 可見題號，也支援 Word 編號已被 OOXML 吃掉的段落。"""
    lines = [clean(x) for x in text.splitlines()]
    found = []
    current = None
    last_number = 0
    for line in lines:
        m = QUESTION.match(line)
        # PDF 版面常把頁碼或表格中的數字放在行首（例如「8 20 公分」）。
        # 題號在同一份試卷中應嚴格遞增；不遞增的數字視為上一題的續行。
        number = int(m.group(1)) if m else None
        if m and number > last_number:
            if current:
                found.append(current)
            current = [number, m.group(2)]
            last_number = number
            continue
        if current is not None and line:
            current[1] += " " + line
    if current:
        found.append(current)
    if len(found) >= 2:
        return [(n, clean(t)) for n, t in found]

    # Word 題號若是 Word numbering，純文字沒有 1.；每個含有選項的段落視為一題。
    paras = [clean(x) for x in text.splitlines() if clean(x)]
    # Word numbering 可能不會出現在純文字；這時以題目段落的出現順序重新編號，
    # 不能使用原始段落索引，否則標題、跨頁文字會讓題號跳號。
    blocks = [(i + 1, p) for i, p in enumerate(
        (p for p in paras if len(OPTION.findall(p)) >= 2)
    )]
    return blocks


def read_reference(path):
    if not path:
        return {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {int(row["題號"]): row for row in csv.DictReader(f) if row.get("題號", "").isdigit()}


def question_stem(text):
    """只比較題幹，避免選項排版差異影響跨來源安全合併。"""
    text = re.split(r"\(A\)|（A）", text or "", maxsplit=1)[0]
    return re.sub(r"\s+", "", text)


def row_for(number, question, reference):
    row = {field: "" for field in FIELDS}
    old = reference.get(number, {})
    # 題號相同不代表是同一份考卷；只有題幹相似時才合併答案與分類，
    # 避免把另一份試卷的答案誤套進來。
    similarity = difflib.SequenceMatcher(
        None, question_stem(question), question_stem(old.get("題目", ""))
    ).ratio() if old else 0.0
    if similarity >= 0.80:
        for field in FIELDS:
            row[field] = old.get(field, "")
    row["題號"] = str(number)
    row["題目"] = question
    if not row["答案"]:
        row["待確認"] = "Y"
    if not row["圖表類型"]:
        row["圖表類型"] = "無"
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--reference-csv", type=Path)
    args = ap.parse_args()
    questions = split_questions(source_text(args.input))
    reference = read_reference(args.reference_csv)
    rows = [row_for(n, q, reference) for n, q in questions]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)
    print(f"完成：{len(rows)} 題、{len(FIELDS)} 欄 → {args.out}")
    if reference:
        matched = sum(bool(row["答案"]) for row in rows)
        print(f"參考 CSV 題幹確認後對應：{matched}/{len(rows)} 題；其餘已標記待確認")


if __name__ == "__main__":
    main()
