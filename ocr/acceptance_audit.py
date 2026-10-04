#!/usr/bin/env python3
"""Audit whether OCR evidence is sufficient for a held-out model acceptance claim.

This does not infer ground truth. It blocks promotion when labels are provisional,
the case belongs to train, source identity is missing, or Word/layout checks have
not been human-reviewed.
"""

import argparse
import hashlib
import json
from pathlib import Path


REQUIRED_PAGE_FIELDS = (
    "background_whiteness",
    "background_uniformity",
    "printed_thin_lines_preserved",
    "handwriting_removed",
    "formulas_and_symbols_preserved",
    "scientific_figures_preserved",
    "table_lines_preserved",
    "image_geometry_preserved",
    "meaning_unchanged",
    "ocr_text",
)

REQUIRED_WORD_FIELDS = (
    "question_numbering",
    "stem_and_options",
    "table_structure",
    "figure_placement",
    "handwriting_excluded",
    "tzutzu_layout",
    "overall_usable",
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit(gold, *, source=None, expected_split="test"):
    blockers = []
    warnings = []
    if gold.get("status") != "human_verified":
        blockers.append("gold.status 不是 human_verified")
    if not gold.get("reviewer"):
        blockers.append("缺少 reviewer")
    if not gold.get("reviewed_at"):
        blockers.append("缺少 reviewed_at")
    if gold.get("split") != expected_split:
        blockers.append(f"標註 split 不是 {expected_split}")
    if not gold.get("source_sha256"):
        blockers.append("缺少 source_sha256，無法證明驗收的原卷版本")
    elif source and sha256(source) != gold["source_sha256"]:
        blockers.append("source_sha256 與原卷不符")

    pages = gold.get("page_questions")
    if not isinstance(pages, dict) or not pages:
        blockers.append("缺少 page_questions")
    elif any(len(numbers) != len(set(numbers)) for numbers in pages.values()):
        blockers.append("page_questions 內含同頁重複題號")

    page_review = gold.get("page_review", {})
    missing_page = [field for field in REQUIRED_PAGE_FIELDS if field not in page_review]
    failed_page = [field for field in REQUIRED_PAGE_FIELDS if page_review.get(field) is not True]
    if missing_page:
        blockers.append("頁面人工驗收尚未完整：" + ", ".join(missing_page))
    elif failed_page:
        blockers.append("頁面人工驗收未通過：" + ", ".join(failed_page))

    word_review = gold.get("word_review", {})
    missing_word = [field for field in REQUIRED_WORD_FIELDS if field not in word_review]
    failed_word = [field for field in REQUIRED_WORD_FIELDS if word_review.get(field) is not True]
    if missing_word:
        blockers.append("Word 人工驗收尚未完整：" + ", ".join(missing_word))
    elif failed_word:
        blockers.append("Word 人工驗收未通過：" + ", ".join(failed_word))
    if not gold.get("runtime_seconds"):
        warnings.append("未記錄 runtime_seconds，無法比較效率")

    return {
        "eligible_for_test_claim": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "scope": {
            "anchor_gold": bool(pages),
            "page_review_fields": sorted(page_review),
            "word_review_fields": sorted(word_review),
            "expected_split": expected_split,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--expected-split", default="test", choices=("train", "validation", "test"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    result = audit(gold, source=args.source, expected_split=args.expected_split)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["eligible_for_test_claim"] else 2)


if __name__ == "__main__":
    main()
