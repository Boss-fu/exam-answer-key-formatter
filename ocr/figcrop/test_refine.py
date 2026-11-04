import unittest
import cv2, numpy as np
try:
 from .refine import refine_box, edge_cut_ink, has_body_line
except ImportError:
 from refine import refine_box, edge_cut_ink, has_body_line

class RefineTest(unittest.TestCase):
 def test_expands_to_include_axes_and_keeps_body_out(self):
  im=np.full((160,220,3),255,np.uint8)
  cv2.rectangle(im,(50,50),(120,110),(0,0,0),2); cv2.line(im,(45,115),(125,115),(0,0,0),2)
  lines=[(15,18,205,30,'這是一整行題目正文'),(42,120,65,132,'圖(一)')]
  b=refine_box(im,(52,52,118,108),ocr_lines=lines,column=(0,0,220,160))
  self.assertLessEqual(b[0],45); self.assertGreaterEqual(b[3],132); self.assertGreater(b[1],30)
 def test_table_has_padding(self):
  im=np.full((100,100,3),255,np.uint8); cv2.rectangle(im,(30,30),(70,70),(0,0,0),2)
  b=refine_box(im,(31,31,69,69),kind='table')
  self.assertLessEqual(b[0],24); self.assertGreaterEqual(b[2],76)
 def test_metrics(self):
  im=np.full((80,80,3),255,np.uint8); cv2.line(im,(10,40),(70,40),(0,0,0),2)
  self.assertGreater(edge_cut_ink(im,(20,20,60,60)),0)
  self.assertTrue(has_body_line((0,0,80,80),[(5,5,70,12,'')],100))
if __name__=='__main__': unittest.main()
