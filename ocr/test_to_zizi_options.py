"""選項與附圖代號的切分回歸測試。"""
import unittest

from ocr.to_zizi_word import fix_opt_labels, split_opts


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


if __name__ == "__main__":
    unittest.main()
