"""選項與附圖代號的切分回歸測試。"""
import unittest

from ocr.to_zizi_word import fix_opt_labels, is_figure_label_block, split_opts


class SplitOptionsTest(unittest.TestCase):
    def test_inline_options_require_a_boundary(self):
        head, opts = split_opts("題幹 (A)(1)→(甲)→b (B)(2)→(乙)→a (C)甲 (D)乙")
        self.assertEqual(head, "題幹")
        self.assertEqual(opts, ["(A)(1)→(甲)→b", "(B)(2)→(乙)→a", "(C)甲", "(D)乙"])

    def test_figure_labels_are_not_options(self):
        self.assertEqual(split_opts("(圖B)"), ("(圖B)", []))
        self.assertEqual(split_opts("(B)的構造會出現於器官內"), ("(B)的構造會出現於器官內", []))
        self.assertEqual(fix_opt_labels("(圖B)中何者正確？(A)甲 (B)乙 (C)丙 (D)丁"),
                         "(圖B)中何者正確？(A)甲 (B)乙 (C)丙 (D)丁")
        self.assertEqual(split_opts("題幹(圖B)的構造與(圖C)相連"),
                         ("題幹(圖B)的構造與(圖C)相連", []))

    def test_repairs_four_positionally_proven_damaged_markers(self):
        self.assertEqual(split_opts("(A)甲（及乙(E.)丙(D)丁")[1],
                         ["(A)甲", "(B)乙", "(C)丙", "(D)丁"])
        self.assertEqual(split_opts("(A)甲(13)乙(C)丙(D)丁")[1],
                         ["(A)甲", "(B)乙", "(C)丙", "(D)丁"])
        self.assertEqual(split_opts("(A)甲(B)乙(C)丙（T)）丁")[1],
                         ["(A)甲", "(B)乙", "(C)丙", "(D)丁"])

    def test_figure_label_after_completed_prompt_is_not_question_text(self):
        self.assertTrue(is_figure_label_block("廣告內容如下圖所示。何者正確？", [], "甲乙丙丁"))
        self.assertFalse(is_figure_label_block("右圖所示，請問", [], "何者正確？"))
        self.assertFalse(is_figure_label_block("廣告內容如下圖所示。何者正確？", ["(A)甲"], "圖內文字"))


if __name__ == "__main__":
    unittest.main()
