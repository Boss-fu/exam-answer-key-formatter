"""Word 前的卷頭、頁尾過濾規則。"""
import unittest

from ocr.to_zizi_word import is_boilerplate_line, recover_truncated_qnum


class BoilerplateLineTest(unittest.TestCase):
    def test_header_variants_are_removed(self):
        for text in ("九年級：理化", "科目名稱：理化", "科目代碼：23", "命題教師：王老師",
                     "12班 座號：3 姓名：小明", "112學年度第二學期段考試卷"):
            self.assertTrue(is_boilerplate_line(text, in_header=True), text)

    def test_page_and_continuation_variants_are_removed(self):
        for text in ("第1頁，共4頁", "第 I 頁，共 O 頁", "請接第2頁繼續作答", "背面尚有試題"):
            self.assertTrue(is_boilerplate_line(text), text)

    def test_normal_question_is_kept(self):
        self.assertFalse(is_boilerplate_line("1. 下列何者正確？", in_header=True))
        self.assertFalse(is_boilerplate_line("班級制度的演變", in_header=False))

    def test_recover_only_unambiguous_truncated_question_numbers(self):
        self.assertEqual(recover_truncated_qnum("1).承上題，圖中何者正確？", 9), 10)
        self.assertEqual(recover_truncated_qnum("3.承上題，圖中何者正確？", 38), 39)
        self.assertIsNone(recover_truncated_qnum("3.下列何者正確？", 22))
        self.assertIsNone(recover_truncated_qnum("1).一般敘述", 9))


if __name__ == "__main__":
    unittest.main()
