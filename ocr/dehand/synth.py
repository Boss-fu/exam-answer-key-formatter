#!/usr/bin/env python3
"""把筆畫庫貼到乾淨國中卷，產生可追溯的去筆跡訓練資料。"""
import argparse, hashlib, json, os, random, re
import cv2
import numpy as np
import pymupdf
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pairs_build as pb  # noqa: E402

CLEAN = re.compile(r"乾淨|重製|空白|blank|教師用", re.I); BAD = re.compile(r"原稿|學生", re.I)

def pdfs(roots):
    for root in roots:
        for d, _, fs in os.walk(root):
            for f in fs:
                if not f.lower().endswith(".pdf") or BAD.search(f): continue
                p = os.path.join(d, f)
                try:
                    doc = pymupdf.open(p); text = sum(len(x.get_text()) for x in doc)
                    if CLEAN.search(f) or text > 200: yield p
                except Exception: pass

def anchors(gray):
    ink = gray < min(200, cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[0])
    n, _, st, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), 8)
    # 字元／短字串的起點，作為題號附近的保護壓力測試位置。
    return [(int(s[0]), int(s[1])) for s in st[1:] if 8 <= s[4] <= 1500 and s[2] < gray.shape[1] *.15]

def paste(base, stroke, x, y, rng, pencil):
    scale = rng.uniform(.6, 1.6); angle = rng.uniform(-15, 15)
    st = cv2.resize(stroke, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    M = cv2.getRotationMatrix2D((st.shape[1]/2, st.shape[0]/2), angle, 1); st = cv2.warpAffine(st, M, (st.shape[1], st.shape[0]), borderValue=(255,255,255,0))
    h,w = st.shape[:2]; x=max(0,min(base.shape[1]-w,x)); y=max(0,min(base.shape[0]-h,y))
    # 成組算式可能比窄欄還寬；先縮小至頁面內，不能讓 ROI 與 alpha 尺寸錯位。
    if h >= base.shape[0] or w >= base.shape[1]:
        fit = min((base.shape[0] - 2) / max(1, h), (base.shape[1] - 2) / max(1, w))
        st = cv2.resize(st, None, fx=fit, fy=fit, interpolation=cv2.INTER_AREA)
        h, w = st.shape[:2]
    x=max(0,min(base.shape[1]-w,x)); y=max(0,min(base.shape[0]-h,y))
    if h < 2 or w < 2: return np.zeros_like(base, bool), False
    alpha=st[:,:,3].astype(float)/255; alpha = cv2.GaussianBlur(alpha,(0,0),rng.uniform(.5,1.2)) if pencil else alpha
    color=rng.randint(110,185) if pencil else rng.randint(15,70)
    roi=base[y:y+h,x:x+w]; original_ink=roi<200; hand=alpha>.08
    roi[:]=np.minimum(roi, (255*(1-alpha)+color*alpha).astype(np.uint8))
    mask=np.zeros_like(base,bool); mask[y:y+h,x:x+w]=hand & ~original_ink # 印刷重疊絕不可標為筆跡
    return mask, True

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--clean-roots", nargs="+", required=True); ap.add_argument("--data",default="ocr/dehand/data"); ap.add_argument("--pages",type=int,default=800); ap.add_argument("--seed",type=int,default=7)
    ap.add_argument("--cluster-rate", type=float, default=.35, help="貼入整段算式／作答群的比例")
    a=ap.parse_args(); rng=random.Random(a.seed); sd=os.path.join(a.data,"_strokes"); sm=json.load(open(os.path.join(sd,"manifest.json"),encoding="utf-8")); rows=[]
    for r in sm:
        s=cv2.imread(os.path.join(sd,r["file"]),cv2.IMREAD_UNCHANGED)
        if s is not None: rows.append((r,s))
    strokes=[s for _,s in rows]; clusters=[s for r,s in rows if r.get("kind")=="cluster"]
    if not strokes: raise SystemExit("筆畫庫是空的；先跑 stroke_bank.py")
    out=os.path.join(a.data,"_synth"); os.makedirs(out,exist_ok=True); manifest=[]; made=0
    for path in pdfs(a.clean_roots):
        if made>=a.pages: break
        doc=pymupdf.open(path)  # 分組與 pairs_build 同一套（同卷不跨訓練/驗證）
        key=hashlib.md5(pb.key(os.path.basename(path)).encode()).hexdigest()[:8]; name=re.sub(r"[^\w.-]+","_",os.path.basename(path))[:55]
        for pi,page in enumerate(doc):
            if made>=a.pages: break
            pix=page.get_pixmap(dpi=150,colorspace=pymupdf.csGRAY); base=np.frombuffer(pix.samples,np.uint8).reshape(pix.h,pix.w).copy(); clean=base.copy(); mask=np.zeros_like(base,bool); qmask=np.zeros_like(base,bool); aa=anchors(base)
            count=rng.randint(15,80); qanchors=[]
            for j in range(count):
                # 真實連續字串在原資料中遠少於單一筆畫，若均勻抽樣模型仍學
                # 不到完整算式；刻意提高它在合成集的出現率，但保留小記號。
                st=(clusters[rng.randrange(len(clusters))] if clusters and rng.random()<a.cluster_rate else strokes[rng.randrange(len(strokes))]); mode=rng.random()
                if mode<.4 and aa:
                    x,y=rng.choice(aa); qanchors.append((x,y));
                    qmask[max(0,y-18):y+35,max(0,x-18):x+65] |= base[max(0,y-18):y+35,max(0,x-18):x+65] < 200
                    x+=rng.randint(-8,8); y+=rng.randint(-8,8)
                elif mode<.7: x=rng.randint(base.shape[1]//5,base.shape[1]*4//5); y=rng.randint(base.shape[0]//6,base.shape[0]*5//6)
                else: x=rng.randrange(base.shape[1]); y=rng.randrange(base.shape[0])
                m,ok=paste(base,st,x,y,rng,j%2==0); mask|=m
            # x 是貼筆跡後的題卷；c 保留貼前底稿，作為 image-to-image 的精確答案。
            d=os.path.join(out,name); os.makedirs(d,exist_ok=True); stem=f"p{pi+1}_{made:04d}"; cv2.imwrite(os.path.join(d,stem+"_x.png"),base); cv2.imwrite(os.path.join(d,stem+"_c.png"),clean); cv2.imwrite(os.path.join(d,stem+"_m.png"),(mask*255).astype(np.uint8)); cv2.imwrite(os.path.join(d,stem+"_q.png"),(qmask*255).astype(np.uint8))
            manifest.append({"pair":name,"page":stem,"group":key,"source":path,"question_anchors":len(qanchors),"hand_ratio":round(float(mask.mean()),5)}); made+=1
    with open(os.path.join(out,"manifest.json"),"w",encoding="utf-8") as f: json.dump(manifest,f,ensure_ascii=False,indent=1)
    print(f"合成 {made} 頁；{out}/manifest.json")

if __name__=="__main__": main()
