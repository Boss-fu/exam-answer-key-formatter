import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import agree_label
from finetune_jw_mask import load_pseudo, read_pseudo


class AgreeLabelTest(unittest.TestCase):
    def test_only_agreement_is_supervised_and_loader_stays_compatible(self):
        gray = np.array([[10, 255, 10, 10]], np.uint8)
        v10 = np.array([[.81, .01, .20, .04]], np.float32)
        r8 = np.array([[.90, .02, .90, .04]], np.float32)
        with patch.object(agree_label, 'ink', return_value=np.array([[1, 1, 1, 0]], bool)):
            mask, weight = agree_label.agreed_labels(gray, v10, r8)
        np.testing.assert_array_equal(mask, [[255, 0, 0, 0]])
        # 第二點是確定印刷；第三點不一致、第四點不是墨跡，皆忽略。
        np.testing.assert_array_equal(weight, [[255, 255, 0, 0]])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); rows = []
            agree_label.write_page(root, 'sample', 1, '/tmp/sample.pdf', gray, mask, weight, rows)
            (root / 'manifest.json').write_text(json.dumps(rows), encoding='utf8')
            loaded = load_pseudo(str(root))
            self.assertEqual(len(loaded), 1)
            image, hand = read_pseudo(loaded[0])
            np.testing.assert_array_equal(image, gray)
            np.testing.assert_array_equal(hand, [[1, 0, 0, 0]])
            saved_weight = cv2.imread(str(root / 'sample' / 'p1_w.png'), 0)
            np.testing.assert_array_equal(saved_weight, weight)


if __name__ == '__main__':
    unittest.main()
