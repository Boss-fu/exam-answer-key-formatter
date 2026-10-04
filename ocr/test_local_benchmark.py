"""本機 OCR 資料分組與題號評測的最小回歸測試。"""

import tempfile
import unittest
from pathlib import Path

from ocr.dataset_catalog import build_catalog, split_for_group
from ocr.docx_question_candidates import extract_candidates
from ocr.acceptance_audit import audit


class LocalBenchmarkTests(unittest.TestCase):
    def test_variants_in_same_exam_folder_share_split(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exam = root / "113" / "某國中"
            exam.mkdir(parents=True)
            (exam / "段考原稿.pdf").touch()
            (exam / "段考乾淨.docx").touch()
            records = build_catalog(root, "fixed-seed")
            self.assertEqual(len(records), 2)
            self.assertEqual(len({item["split_candidate"] for item in records}), 1)
            self.assertFalse(any(item["label_verified"] for item in records))
            self.assertEqual(split_for_group("113/某國中", "fixed-seed"), records[0]["split_candidate"])

    def test_identical_file_in_different_folders_shares_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in (root / "國八" / "某國中", root / "國九" / "某國中"):
                folder.mkdir(parents=True)
                (folder / "同一份考卷.pdf").write_bytes(b"same document")
            records = build_catalog(root, "fixed-seed")
            self.assertEqual(len({item["group"] for item in records}), 1)
            self.assertEqual(len({item["split_candidate"] for item in records}), 1)

    def test_docx_embedded_question_number_and_decimal(self):
        result = extract_candidates([
            "1.第一題，配分 2.5 分",
            "【題組】請回答以下問題：2.第二題",
            "3.第三題",
            "文中日期為 1.最接近太陽",
        ])
        self.assertEqual([q["number"] for q in result["question_candidates"]], [1, 2, 3])
        self.assertEqual(result["missing_numbers"], [])

    def test_provisional_train_gold_cannot_support_test_claim(self):
        result = audit({
            "status": "provisional_visual_review",
            "split": "train",
            "page_questions": {"1": [1, 2]},
        })
        self.assertFalse(result["eligible_for_test_claim"])
        self.assertTrue(any("human_verified" in item for item in result["blockers"]))
        self.assertTrue(any("split" in item for item in result["blockers"]))

    def test_complete_human_review_is_eligible(self):
        result = audit({
            "status": "human_verified",
            "reviewer": "reviewer-id",
            "reviewed_at": "2026-10-02",
            "split": "test",
            "source_sha256": "abc",
            "page_questions": {"1": [1, 2]},
            "runtime_seconds": 12.3,
            "page_review": {
                "background_whiteness": True,
                "background_uniformity": True,
                "printed_thin_lines_preserved": True,
                "handwriting_removed": True,
                "formulas_and_symbols_preserved": True,
                "scientific_figures_preserved": True,
                "table_lines_preserved": True,
                "image_geometry_preserved": True,
                "meaning_unchanged": True,
                "ocr_text": True,
            },
            "word_review": {
                "question_numbering": True,
                "stem_and_options": True,
                "table_structure": True,
                "figure_placement": True,
                "handwriting_excluded": True,
                "tzutzu_layout": True,
                "overall_usable": True,
            },
        })
        self.assertTrue(result["eligible_for_test_claim"])
        self.assertEqual(result["blockers"], [])


if __name__ == "__main__":
    unittest.main()
