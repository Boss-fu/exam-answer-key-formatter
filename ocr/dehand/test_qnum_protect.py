import os
import sys
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import pymupdf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dehand import apply


PDF = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                   "ocr", "testset", "113-1-1_G8_士林國中.pdf")


@unittest.skipUnless(os.path.exists(PDF), "士林國中測試卷不在本機")
class QuestionNumberProtectionTest(unittest.TestCase):
    def test_page_one_question_number_ink_is_not_erased(self):
        page = pymupdf.open(PDF)[0]
        pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csRGB)
        rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, 3).copy()
        original = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

        # 以同一個偵測結果界定題號框，量真正的深色印刷像素而非框內白底。
        protected = apply._qnum_protect_mask(original) > 0
        self.assertGreater(protected.sum(), 0, "第 1 頁沒有偵測到可保護的題號框")
        # 以全幅候選遮罩測試共用擦除出口：題號禁擦必須仍守住。這避免載入
        # r5／跑鉛筆規則使單頁測試超過 30 秒；完整 both 模式由驗收流程跑。
        with patch.dict(os.environ, {"DEHAND_PROTECT_QNUM": "1", "DEHAND_MODE": "rule"}), \
                patch("dehand.pencil_rule.detect", return_value=np.ones(original.shape[:2], np.uint8)):
            cleaned, _ = apply.erase(original, dpi=300)
        ink = cv2.cvtColor(original, cv2.COLOR_BGR2GRAY) < 140
        qnum_ink = protected & ink
        self.assertGreater(qnum_ink.sum(), 0)
        erased = np.any(cleaned != original, axis=2)
        self.assertLess(erased[qnum_ink].mean(), .01)
