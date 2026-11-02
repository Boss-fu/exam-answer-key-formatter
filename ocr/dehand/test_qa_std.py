import unittest

import cv2
import numpy as np

try:
    from .qa_std import assess, repair, verdict
except ImportError:
    from qa_std import assess, repair, verdict


def printed_page():
    image = np.full((180, 360), 255, np.uint8)
    for y in (40, 85, 130):
        cv2.putText(image, "123 ABC", (35, y), cv2.FONT_HERSHEY_SIMPLEX, .8, 0, 2, cv2.LINE_AA)
    return image


class QaStdTest(unittest.TestCase):
    def test_two_pixel_shift_is_repaired(self):
        clean = printed_page()
        original = cv2.warpAffine(clean, np.float32([[1, 0, 2], [0, 1, 0]]), (360, 180), borderValue=255)
        before = assess(original, clean)
        self.assertLess(abs(before["dx"]), 2.5)
        fixed = repair(original, before)
        after = assess(original, fixed)
        self.assertLess(after["shift"], .5)

    def test_missing_printed_character_is_filled(self):
        original = printed_page(); clean = original.copy()
        clean[20:48, 33:55] = 255  # 第一個字只從 gt 移除
        metrics = assess(original, clean)
        self.assertGreater(metrics["printed_mask"].sum(), 5)
        fixed = repair(original, metrics)
        self.assertLess(fixed[25:45, 38:52].mean(), 245)

    def test_clean_page_is_good(self):
        page = printed_page()
        self.assertEqual(verdict(assess(page, page)), "good")


if __name__ == "__main__":
    unittest.main()
