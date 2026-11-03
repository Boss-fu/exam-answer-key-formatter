import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from to_qbank_xlsx import fix_ocr


class FixOcrTest(unittest.TestCase):
    def test_biology_phrase_fixes_require_context(self):
        self.assertEqual(fix_ocr("禁片有氯孔，形成冀便"), "葉片有氣孔，形成糞便")
        self.assertEqual(fix_ocr("酸齡度與器宮功能"), "酸鹼度與器官功能")
        self.assertEqual(fix_ocr("若甲為分解作用；乙為含成作用"), "若甲為分解作用；乙為合成作用")
        self.assertEqual(fix_ocr("消化業可分解澱粉"), "消化液可分解澱粉")

    def test_does_not_apply_ambiguous_character_globally(self):
        self.assertEqual(fix_ocr("已知有人進入禁區"), "已知有人進入禁區")
        self.assertEqual(fix_ocr("備註與採集資料"), "備註與採集資料")

    def test_terminal_question_mark(self):
        self.assertEqual(fix_ocr("何者正確?\n(A)甲"), "何者正確？\n(A)甲")


if __name__ == "__main__":
    unittest.main()
