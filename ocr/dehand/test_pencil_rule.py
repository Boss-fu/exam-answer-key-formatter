import os
import sys
import unittest

import cv2
import numpy as np
import pymupdf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dehand.pencil_rule import detect

PDF = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                   "local-app/output/py-ocr/ebe4bfefb6b8eb3c882b1362/陽明國中-8年級112下第一次段考-地國理公-7-9.pdf")


@unittest.skipUnless(os.path.exists(PDF), "陽明實測卷不在本機")
class PencilRuleTest(unittest.TestCase):
    def test_answers_erased_and_question_numbers_preserved(self):
        p = pymupdf.open(PDF)[0]
        pix = p.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY)
        gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w).copy()
        mask = detect(gray) > 0
        # 左欄題號 1~6 的行框起點均在 x<300；不應被遮罩傷到超過 1%。
        from ppocr_engine import det_boxes
        checked = 0
        for box in det_boxes(gray):
            a = np.asarray(box); x0, y0, x1, y1 = int(a[:,0].min()), int(a[:,1].min()), int(a[:,0].max())+1, int(a[:,1].max())+1
            if not (x0 < 300 and 550 < y0 < 1850):
                continue
            # 用深色印刷筆畫檢查題號；前方淡的手寫作答括號不納入題號像素。
            left, right = x0, min(x1, x0 + 75)
            roi = gray[y0:y1, left:right]
            ink = roi < 140
            if ink.sum() > 12 and checked < 6:
                self.assertGreater((~mask[y0:y1, left:right][ink]).mean(), .99)
                checked += 1
        self.assertGreaterEqual(checked, 6)
        # 作答括號／答案所在的左欄起始帶須有擦除，避免退化成全空遮罩。
        self.assertTrue(mask[550:1900, 80:230].any())
        self.assertGreater(mask.mean(), .0001)
