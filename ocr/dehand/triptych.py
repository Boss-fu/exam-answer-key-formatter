#!/usr/bin/env python3
"""輸出原稿｜去筆跡｜被擦掉標紅的三聯檢視圖。"""
import argparse, os, sys
import cv2, numpy as np, pymupdf
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from apply import erase
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('pdf'); ap.add_argument('--page',type=int,default=1); ap.add_argument('--out',default=None); ap.add_argument('--mode',choices=('model','rule','both')); a=ap.parse_args()
 if a.mode: os.environ['DEHAND_MODE']=a.mode
 p=pymupdf.open(a.pdf)[a.page-1]; pix=p.get_pixmap(dpi=300, colorspace=pymupdf.csRGB); rgb=np.frombuffer(pix.samples,np.uint8).reshape(pix.h,pix.w,3).copy(); bgr=cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR)
 clean,_=erase(bgr,300); red=bgr.copy(); hit=np.any(clean != bgr,axis=2); red[hit]=(0,0,255)
 out=a.out or os.path.splitext(a.pdf)[0]+f'_p{a.page}_triptych.png'; cv2.imwrite(out,np.hstack((bgr,clean,red))); print(out)
if __name__=='__main__': main()
