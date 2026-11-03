"""跨欄題目框不可被誤當成頁首。"""
import unittest
from unittest.mock import patch

import numpy as np

from ocr import pyexam


def box(x0, x1, y):
    return [[x0, y], [x1, y], [x1, y + 24], [x0, y + 24]]


class HeaderDetectionTest(unittest.TestCase):
    def test_cross_column_question_is_not_header(self):
        image = np.full((1200, 1000, 3), 255, np.uint8)
        boxes = [box(80, 940, 80)]  # 偵測器誤併同高的第 17、24 題
        for y in range(180, 1000, 70):
            boxes.extend((box(80, 430, y), box(570, 920, y)))
        with patch.object(pyexam.ppocr_engine, "det_boxes", return_value=boxes), \
             patch.object(pyexam.ppocr_engine, "rec_only", return_value=("17. 題幹 24. 題幹", .99)):
            self.assertEqual(pyexam.text_layout(image)["header"], 0)


if __name__ == "__main__":
    unittest.main()
