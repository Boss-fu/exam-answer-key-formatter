import unittest
import cv2, numpy as np
try:
 from .refine import refine_box, edge_cut_ink, has_body_line
 from .crop import guess_qno, unrotate_box
except ImportError:
 from refine import refine_box, edge_cut_ink, has_body_line
 from crop import guess_qno, unrotate_box

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
 def test_qno_same_column_group_and_scanned_prefix(self):
  raw=[((132,20,190,40),'回答第 6～8 題'), ((820,55,875,76),')12.'), ((120,80,175,100),')5.')]
  self.assertEqual(guess_qno((800,180,1100,300),raw,1200),12)
  self.assertEqual(guess_qno((100,180,500,300),raw,1200),5)
 def test_qno_passage_belongs_to_next_question(self):
  raw=[((100,100,500,130),'28.下列何者正確？'),((100,140,500,170),'(A)甲(B)乙(C)丙(D)以上皆可。'),
       ((100,220,500,250),'大雄進行國中生物的實驗，葉片設置遮光區，'),((100,260,500,290),'等待數日，如圖所示，試回答下列問題'),
       ((100,500,500,530),'29.下列何者是本實驗的操縱變因？'),((1000,20,1100,40),'40.甲'),((1000,700,1100,720),'41.乙')]
  self.assertEqual(guess_qno((600,230,900,400),raw,1200),29)
 def test_qno_figure_after_options_stays(self):
  raw=[((100,100,500,130),'28.下列何者正確？'),((100,140,500,170),'(A)甲(B)乙(C)丙(D)以上皆可。'),
       ((100,500,500,530),'29.下列何者是本實驗的操縱變因？'),((1000,20,1100,40),'40.甲'),((1000,700,1100,720),'41.乙')]
  self.assertEqual(guess_qno((100,200,400,450),raw,1200),28)
 def test_qno_group_marker_cut_and_section_header(self):
  raw=[((700,10,1100,40),'貳：題組題（1-50題，每題1分）'),((700,50,1100,80),'光合作用的模式圖，請根據此圖回答41～43'),((700,90,760,120),'題：'),
       ((700,500,1100,530),'41.依據此圖判斷，下列何者錯？'),((100,20,500,50),'30.甲'),((100,300,500,330),'31.乙')]
  self.assertEqual(guess_qno((750,130,1100,480),raw,1200),41)
 def test_qno_figure_right_of_stem(self):
  raw=[((100,100,500,130),'3.下列何者正確？'),((100,150,500,180),'(A)甲(B)乙(C)丙(D)丁'),((100,300,500,330),'4.七年級同學製作微型桌椅，如右圖所示'),((100,600,500,630),'5.附圖是木塊'),((1000,20,1100,40),'20.甲'),((1000,900,1100,920),'21.乙')]
  self.assertEqual(guess_qno((520,270,800,600),raw,1200),4)
 def test_qno_figure_beside_own_stem_stays(self):
  raw=[((100,100,500,130),'3.如右圖，下列何者正確？'),((100,300,500,330),'4.七年級同學製作桌椅'),((100,340,500,370),'(A)甲(B)乙(C)丙(D)丁'),((100,600,500,630),'5.附圖是木塊'),((1000,20,1100,40),'20.甲'),((1000,900,1100,920),'21.乙')]
  self.assertEqual(guess_qno((520,90,800,400),raw,1200),3)
 def test_qno_figure_at_page_top_takes_next_below(self):
  raw=[((100,500,500,530),'40.下列何者正確？'),((100,700,500,730),'41.甲'),((800,500,1100,530),'45.乙'),((800,700,1100,730),'46.丙')]
  self.assertEqual(guess_qno((100,50,500,450),raw,1200),40)
 def test_unrotate_box(self):
  mark=np.zeros((60,100),bool)
  for k in (0,1,2,3):
   up=np.zeros(np.rot90(mark,k).shape,bool); b=(5,10,20,30); up[b[1]:b[3],b[0]:b[2]]=True
   ys,xs=np.where(np.rot90(up,-k))
   self.assertEqual(unrotate_box(b,k,mark.shape),(xs.min(),ys.min(),xs.max()+1,ys.max()+1))
if __name__=='__main__': unittest.main()
