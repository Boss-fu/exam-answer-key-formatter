#!/usr/bin/env python3
"""zizi_bench_pair 的固定公平評測；基準圖只讀取，絕不作訓練資料。"""
import argparse, os, sys
import cv2, numpy as np
import onnxruntime as ort
sys.path.insert(0, os.path.dirname(__file__))
from pairs_build import ink

def run_onnx(path, gray):
    # U-Net 有四層下採樣；ONNX 軸可動態，但輸入仍補到 16 的倍數再裁回原大小。
    h,w=gray.shape; ph=(16-h%16)%16; pw=(16-w%16)%16
    padded=cv2.copyMakeBorder(gray,0,ph,0,pw,cv2.BORDER_CONSTANT,value=255)
    s=ort.InferenceSession(path,providers=['CPUExecutionProvider']); x=padded.astype('float32')[None,None]/255
    return np.clip(s.run(None,{'x':np.ascontiguousarray(x)})[0][0,0,:h,:w]*255,0,255).astype('uint8')
def score(out, gt, hand):
    mse=np.mean(((out.astype('float32')-gt.astype('float32'))/255)**2); oi=out<200; pr=ink(gt)
    return 10*np.log10(1/max(mse,1e-10)), (oi&pr).sum()/max(1,pr.sum()), (oi&hand).sum()/max(1,hand.sum())
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--model',required=True); ap.add_argument('--bench',default='ocr/results/zizi_bench_pair'); ap.add_argument('--out',default=None); a=ap.parse_args()
    inp=cv2.imread(os.path.join(a.bench,'001_in.jpg'),0); gt=cv2.imread(os.path.join(a.bench,'001_gt.png'),0)
    if inp is None or gt is None: raise SystemExit('找不到 001_in.jpg 或 001_gt.png')
    ours=run_onnx(a.model,inp); clean_d=cv2.dilate(ink(gt).astype('uint8'),np.ones((7,7),np.uint8))>0; hand=ink(inp)&~clean_d
    try:
        from apply import erase
        old=os.environ.get('DEHAND_MODE'); os.environ['DEHAND_MODE']='both'; masked,_=erase(cv2.cvtColor(inp,cv2.COLOR_GRAY2BGR),dpi=150); masked=cv2.cvtColor(masked,cv2.COLOR_BGR2GRAY)
        if old is not None: os.environ['DEHAND_MODE']=old
    except Exception as e: print('遮罩法失敗：',e); masked=inp
    for n,img in [('img2img',ours),('mask',masked)]:
        p,k,r=score(img,gt,hand); print(f'{n}: PSNR {p:.2f}；印刷保留 {k:.2%}；筆跡殘留 {r:.2%}')
    h=max(inp.shape[0],gt.shape[0],ours.shape[0]); panel=np.full((h,inp.shape[1]*3),255,np.uint8)
    for i,img in enumerate((inp,gt,ours)): panel[:img.shape[0],i*inp.shape[1]:(i+1)*inp.shape[1]]=img
    for i,t in enumerate(('input','zizi GT','ours')): cv2.putText(panel,t,(10+i*inp.shape[1],35),cv2.FONT_HERSHEY_SIMPLEX,1,(0,),2)
    target=a.out or os.path.join(a.bench,'compare.png'); cv2.imwrite(target,panel); print('三聯圖：',target)
if __name__=='__main__': main()
