import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ocr_symbols import fix_symbols


class OcrSymbols(unittest.TestCase):
    def test_reported_cases(self):
        # 福大回報：左邊的怪符號 → 右邊一般字
        self.assertEqual(fix_symbols("㈲ ㈬ ㆗ ㆙ ㆚ ㆛"), "有 水 中 甲 乙 丙")

    def test_sentence(self):
        self.assertEqual(fix_symbols("㆙、㆚、㆛三者㆗㈲㈬分"), "甲、乙、丙三者中有水分")

    def test_other_ranges(self):
        self.assertEqual(fix_symbols("㊀㊁ ⼈⼦ 說"), "一二 人子 說")   # 圈圈、康熙部首、相容漢字

    def test_numbering_keeps_parens(self):
        self.assertEqual(fix_symbols("㈠ ㈡"), "(一) (二)")

    def test_normal_text_untouched(self):
        s = "（C）1. 下列何者正確？ＡＢＣ，ｔ=25°C；𝑥²"
        self.assertEqual(fix_symbols(s), s)
        self.assertEqual(fix_symbols(""), "")
        self.assertIsNone(fix_symbols(None))

    def test_idempotent(self):
        once = fix_symbols("㈲㆗㆙")
        self.assertEqual(fix_symbols(once), once)


if __name__ == "__main__":
    unittest.main()
