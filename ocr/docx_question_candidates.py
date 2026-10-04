#!/usr/bin/env python3
"""從同場考試的 DOCX 產生待核對題目文字；不宣稱它是正確標註。"""

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
QUESTION = re.compile(r"(?<![\d.、．])(\d{1,2})[.、．](?!\d)\s*")


def read_paragraphs(source):
    with ZipFile(source) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    paragraphs = []
    for paragraph in root.iter(W + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(W + "t"))
        paragraphs.append(text.strip())
    return paragraphs


def extract_candidates(paragraphs):
    questions = []
    rejected = []
    current = None
    for paragraph_no, text in enumerate(paragraphs, start=1):
        matches = list(QUESTION.finditer(text))
        # 題組說明可能與新題號在同一段；同時避開文章日期與倒退的數字清單。
        match = next((m for m in matches if (
            (current is None and int(m.group(1)) == 1)
            or (current is not None and current["number"] < int(m.group(1)) <= current["number"] + 3)
        )), None)
        if match:
            number = int(match.group(1))
            before = text[:match.start()].strip()
            after = text[match.end():].strip()
            current = {"number": number, "paragraph": paragraph_no,
                       "text_lines": [part for part in (before, after) if part], "verified": False}
            questions.append(current)
        elif current is not None:
            if matches and matches[0].start() == 0:
                rejected.append({"paragraph": paragraph_no, "number": int(matches[0].group(1)), "text": text[:120]})
            if text:
                current["text_lines"].append(text)
    for question in questions:
        question["text"] = "\n".join(line for line in question.pop("text_lines") if line)
    numbers = {question["number"] for question in questions}
    final = max(numbers) if numbers else 0
    return {
        "status": "unverified_candidate",
        "question_candidates": questions,
        "missing_numbers": sorted(set(range(1, final + 1)) - numbers),
        "rejected_numbered_paragraphs": rejected,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.source.is_file() or args.source.suffix.lower() != ".docx":
        parser.error("來源必須是存在的 .docx 檔案")
    result = extract_candidates(read_paragraphs(args.source))
    result["source"] = str(args.source.resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "candidates": len(result["question_candidates"]),
        "missing_numbers": result["missing_numbers"],
        "rejected_numbered_paragraphs": len(result["rejected_numbered_paragraphs"]),
        "out": str(args.out),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
