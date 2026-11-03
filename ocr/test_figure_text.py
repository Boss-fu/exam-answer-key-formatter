"""附圖短標籤的回歸測試；不需模型或考卷。"""
import unittest

from ocr.pyexam import split_figure_text


def item(text, x, y, w=70, h=24):
    return {"text": text, "conf": 0.95,
            "box": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]}


class FigureShortLabelTest(unittest.TestCase):
    def test_classification_labels_move_into_figure(self):
        figure = (100, 100, 400, 300)  # 連通的分類線／框
        items = [item("無機化合物", 125, 125), item("小分子", 250, 125),
                 item("天然聚合物", 125, 230), item("合成聚合物", 250, 230),
                 item("下列何者正確？", 30, 340, 180), item("(A)甲", 30, 380)]
        figs, body, _, labels = split_figure_text(items, [figure], pad=70)
        self.assertEqual(figs, [figure])
        self.assertEqual([x["text"] for x in body], ["下列何者正確？", "(A)甲"])
        self.assertEqual([x["text"] for x in labels[0]],
                         ["無機化合物", "小分子", "天然聚合物", "合成聚合物"])

    def test_option_and_sentence_are_never_swallowed(self):
        figure = (100, 100, 300, 260)
        items = [item("搖動", 120, 130), item("油水", 190, 160),
                 item("(A)均勻互溶", 130, 210), item("請判斷下列敘述是否正確。", 110, 280, 220)]
        figs, body, _, labels = split_figure_text(items, [figure], pad=70)
        self.assertEqual(figs, [figure])
        self.assertEqual([x["text"] for x in labels[0]], ["搖動", "油水"])
        self.assertEqual([x["text"] for x in body], ["(A)均勻互溶", "請判斷下列敘述是否正確。"])


if __name__ == "__main__":
    unittest.main()
