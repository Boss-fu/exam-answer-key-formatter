import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dehand import apply


class PrintedInkProtectionTest(unittest.TestCase):
    def test_only_components_fully_inside_eligible_text_box_are_protected(self):
        image = np.full((100, 100, 3), 255, np.uint8)
        # 同列兩個框，故第一個框是合格的印刷文字框。
        boxes = [(10, 10, 30, 14), (35, 10, 50, 14)]
        hit = np.zeros((100, 100), np.uint8)
        # 框內的小元件代表印刷筆畫；跨出框的元件代表壓字作答。
        hit[11:14, 14:17] = 1
        hit[11:14, 27:33] = 1
        image[hit > 0] = 80

        protected = apply._printed_ink_protect_mask(image, hit, detected_boxes=boxes)

        self.assertTrue(protected[11:14, 14:17].all())
        self.assertFalse(protected[11:14, 27:33].any())


if __name__ == '__main__':
    unittest.main()
