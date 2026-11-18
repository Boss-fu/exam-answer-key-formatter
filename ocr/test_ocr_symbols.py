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

    def test_known_confusions(self):
        # 華興國中 113 上第二次段考（地科）人工校對抓到的真實誤認
        self.assertEqual(fix_symbols("位在非洲板瑰的乙"), "位在非洲板塊的乙")
        self.assertEqual(fix_symbols("測站距離甲>乙>丙>于"), "測站距離甲>乙>丙>丁")
        self.assertEqual(fix_symbols("(A)大陸地殼一玄武岩質"), "(A)大陸地殼—玄武岩質")

    def test_known_confusions_do_not_touch_normal_text(self):
        for s in ["位在非洲板塊的乙", "于是我們出發", "甲>乙>丙>丁", "地殼一般由玄武岩構成", "地函一定比地殼厚"]:
            self.assertEqual(fix_symbols(s), s)

    def test_idempotent(self):
        once = fix_symbols("㈲㆗㆙")
        self.assertEqual(fix_symbols(once), once)


if __name__ == "__main__":
    unittest.main()
