import os
import sys
import unittest
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import pipeline_eval


class PipelineEvalTest(unittest.TestCase):
    def test_page_metrics_counts_removed_hand_and_print_separately(self):
        q = {
            'x': np.full((4, 4), 255, np.uint8),
            'c': np.full((4, 4), 255, np.uint8),
            'hand': np.zeros((4, 4), bool),
        }
        # 印刷在左上、右上各一點；左上是圖框，另兩點是筆跡。
        q['hand'][1, 1] = q['hand'][3, 3] = True
        printed = np.zeros((4, 4), bool)
        printed[0, 0] = printed[0, 3] = True
        remaining = np.zeros((4, 4), bool)
        remaining[0, 3] = True  # 保留右上印刷，其他標註墨跡已被擦掉。
        with patch.object(pipeline_eval, 'ink', return_value=printed), \
             patch.object(pipeline_eval, '_remaining_ink', return_value=remaining):
            got = pipeline_eval.page_metrics(q, np.zeros((4, 4, 3), np.uint8), [(0, 0, 2, 2)])
        self.assertEqual(got['hand_removed'], 2)
        self.assertEqual(got['hand_total'], 2)
        self.assertEqual(got['print_erased'], 1)
        self.assertEqual(got['print_total'], 2)
        self.assertEqual(got['figure_erased'], 1)
        self.assertEqual(got['figure_total'], 1)

    def test_page_metrics_rejects_misaligned_annotations(self):
        q = {
            'x': np.full((4, 4), 255, np.uint8),
            'c': np.full((3, 4), 255, np.uint8),
            'hand': np.zeros((4, 4), bool),
        }
        with self.assertRaisesRegex(ValueError, '尺寸必須完全一致'):
            pipeline_eval.page_metrics(q, np.zeros((4, 4, 3), np.uint8), [])

    def test_mask_metrics_matches_score_style_hit_and_false_positive_counts(self):
        q = {
            'x': np.full((3, 3), 255, np.uint8),
            'c': np.full((3, 3), 255, np.uint8),
            'hand': np.zeros((3, 3), bool),
        }
        q['hand'][0, 0] = q['hand'][1, 1] = True
        printed = np.zeros((3, 3), bool)
        printed[0, 0] = printed[2, 2] = True
        hit = np.zeros((3, 3), bool)
        hit[0, 0] = hit[2, 2] = True
        with patch.object(pipeline_eval, 'ink', return_value=printed):
            got = pipeline_eval.mask_metrics(q, hit)
        self.assertEqual(got, {'hand_hit': 1, 'hand_total': 2,
                               'print_hit': 2, 'print_total': 2})

    def test_mask_metrics_rejects_misaligned_mask(self):
        q = {'x': np.full((3, 3), 255, np.uint8),
             'c': np.full((3, 3), 255, np.uint8),
             'hand': np.zeros((3, 3), bool)}
        with self.assertRaisesRegex(ValueError, '候選遮罩'):
            pipeline_eval.mask_metrics(q, np.zeros((2, 3), bool))

    def test_erase_mask_only_whitens_requested_pixels(self):
        source = np.full((2, 2, 3), 100, np.uint8)
        hit = np.array([[True, False], [False, True]])
        actual = pipeline_eval._erase_mask(source, hit)
        self.assertTrue(np.all(actual[hit] == 255))
        self.assertTrue(np.all(actual[~hit] == 100))


if __name__ == '__main__':
    unittest.main()
