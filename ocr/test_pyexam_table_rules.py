"""表格／題目框判定的快取回歸測試；不需重跑 OCR。"""
import json
import re
import unittest
from pathlib import Path

from ocr.pyexam import is_text_table, table_has_question_or_paragraph


ROOT = Path(__file__).resolve().parents[1]
LANYA = ROOT / "local-app/output/py-ocr/ed6acb108d554550937334ba/result/114上-國九理化-一段-蘭雅國中_tables.json"
BAILING = ROOT / "ocr/results/gt_eval/nodehand/112-2-2_G8_百齡國中/112-2-2_G8_百齡國中_tables.json"


def _item(text, x, y):
    return {"text": text, "conf": 1.0,
            "box": [[x, y], [x + 300, y], [x + 300, y + 30], [x, y + 30]]}


class TableQuestionFrameCacheTest(unittest.TestCase):
    def test_lanya_page2_left_question_frame_is_text(self):
        """蘭雅第 2 頁左欄：快取中的 16–23 題須保留成 OCR 文字。"""
        rows = json.loads(LANYA.read_text())["p2_t1"]["rows"]
        text = " ".join(cell[0] for row in rows for cell in row)
        qnos = [int(n) for n in re.findall(r"(?<!\d)(1[6-9]|2[0-3])\.", text)]
        items = [_item(f"{n}.題目內容", 20, i * 50) for i, n in enumerate(qnos)]
        table = {"box": (0, 0, 1000, 500), "xs": [0, 333, 666, 1000], "ys": [0, 250, 500]}
        self.assertTrue(table_has_question_or_paragraph(table, items))

    def test_bailing_inner_column_numbered_table_stays_table(self):
        """百齡真表格：1–4 在內欄，不得因連號而放行成文字。"""
        rows = json.loads(BAILING.read_text())["p3_t2"]["rows"]
        text = " ".join(cell[0] for row in rows for cell in row)
        qnos = [int(n) for n in re.findall(r"(\d)\.", text)]
        self.assertEqual(qnos, [1, 2, 3, 4])
        items = [_item(f"{n}.欄內說明文字", 500, i * 50) for i, n in enumerate(qnos)]
        table = {"box": (0, 0, 1000, 500), "xs": [0, 400, 1000], "ys": [0, 250, 500]}
        self.assertFalse(table_has_question_or_paragraph(table, items))

    def test_sparse_single_visual_column_is_still_a_table(self):
        """淡直線／合併 OCR 框不能讓多列真表格漏回題幹。"""
        table = {"box": (0, 0, 500, 600), "xs": [0, 500], "ys": [0, 150, 300, 450, 600]}
        items = [_item("動脈", 100, 30), _item("靜脈", 100, 180), _item("血流方向", 100, 330)]
        self.assertTrue(is_text_table(table, items))


if __name__ == "__main__":
    unittest.main()
