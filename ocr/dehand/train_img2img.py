#!/usr/bin/env python3
"""訓練灰階原稿→乾淨稿 U-Net；不影響 apply.py 既有遮罩預設行為。"""
import argparse, glob, json, os, random, sys, time
import cv2, numpy as np, torch
import torch.nn as nn
import torch.nn.functional as F
from train import UNet
from pairs_build import ink

PATCH = 256

class RestoreNet(nn.Module):
    def __init__(self, width=24, multitask=False):
        super().__init__(); self.body = UNet((width, width*2, width*4, width*8))
        self.body.head = nn.Conv2d(width, 1, 1); self.multitask = multitask
        if multitask: self.mask_head = nn.Conv2d(width, 1, 1)  # body 的 decoder 最後特徵不外露，改由 hook 取得
    def forward(self, x):
        # UNet head 是殘差；sigmoid 確保輸出像素仍在 [0,1]。
        return torch.sigmoid(x + self.body(x))

def manifests(data):
    def load(p): return json.load(open(p, encoding='utf8')) if os.path.exists(p) else []
    real = {(r['pair'], 'p'+str(r['page'])): r.get('group', r['pair']) for r in load(os.path.join(data,'manifest.json'))}
    synth = {(r['pair'], r['page']): r.get('group', r['pair']) for r in load(os.path.join(data,'_synth','manifest.json'))}
    manual = load(os.path.join(data,'_manual','manifest.json'))
    return real, synth, manual if isinstance(manual,dict) else {}

def load_pages(data):
    real, synth, manual = manifests(data); pages=[]
    for x in glob.glob(os.path.join(data,'**','*_x.png'),recursive=True):
        rel=os.path.relpath(x,data); bits=rel.split(os.sep); parent=os.path.basename(os.path.dirname(x)); stem=os.path.basename(x)[:-6]
        source='synth' if '_synth' in bits else ('manual' if '_manual' in bits else 'real')
        c=x[:-6]+'_c.png'; m=x[:-6]+'_m.png'
        if not os.path.exists(m): continue
        xi=cv2.imread(x,0); mi=cv2.imread(m,0)>127
        if xi is None: continue
        if os.path.exists(c): ci=cv2.imread(c,0); weight=0.7 if source=='synth' else 1.0
        elif source=='manual': ci=xi.copy(); ci[mi]=255; weight=.25  # 人工遮罩只有近似乾淨答案，勿主導模型
        else: continue
        if ci is None: continue
        grp=synth.get((parent,stem),parent) if source=='synth' else (manual.get(stem, 'manual:'+stem.rsplit('_p',1)[0]) if source=='manual' else real.get((parent,stem),parent))
        pages.append(dict(x=xi,c=ci,m=mi,group=grp,w=weight,name=rel,source=source))
    return pages

def crop(p, rng):
    h,w=p['x'].shape; ps=min(PATCH,h,w); ys,xs=np.nonzero(p['m'])
    if len(ys) and rng.random()<.7:
        k=rng.randrange(len(ys)); y=int(np.clip(ys[k]-ps//2,0,h-ps)); x=int(np.clip(xs[k]-ps//2,0,w-ps))
    else: y=rng.randint(0,h-ps); x=rng.randint(0,w-ps)
    def rs(a, interp): return cv2.resize(a[y:y+ps,x:x+ps],(PATCH,PATCH),interpolation=interp)
    return rs(p['x'],cv2.INTER_AREA)/255., rs(p['c'],cv2.INTER_AREA)/255., rs(p['m'].astype('uint8'),cv2.INTER_NEAREST), p['w']

def sobel(x):
    k=torch.tensor([[-1.,0,1],[-2,0,2],[-1,0,1]],device=x.device).view(1,1,3,3)
    return F.conv2d(x,k,padding=1), F.conv2d(x,k.transpose(2,3),padding=1)

@torch.no_grad()
def metrics(model,pages,dev):
    ps=[]; keep=[]; remain=[]
    for p in pages:
        x=torch.from_numpy(p['x'][None,None].astype('float32')/255).to(dev); y=model(x)[0,0].cpu().numpy()
        c=p['c']/255.; ps.append(np.mean((y-c)**2))
        printed=ink(p['c']); outink=y<.78; keep += [np.logical_and(printed,outink).sum(),printed.sum()]
        # 僅在筆跡區評估殘留（輸出仍暗），GT 筆畫壓字也依此嚴格計入。
        remain += [np.logical_and(p['m'],outink).sum(),p['m'].sum()]
    return 10*np.log10(1/max(np.mean(ps),1e-10)), keep[0]/max(1,keep[1]), remain[0]/max(1,remain[1])

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--data',default='ocr/dehand/data'); ap.add_argument('--out',default='ocr/dehand/runs/img2img'); ap.add_argument('--epochs',type=int,default=30); ap.add_argument('--steps',type=int,default=150); ap.add_argument('--batch',type=int,default=12); ap.add_argument('--val-frac',type=float,default=.2); ap.add_argument('--seed',type=int,default=7); ap.add_argument('--width',type=int,default=24); ap.add_argument('--multitask',action='store_true'); a=ap.parse_args()
    rng=random.Random(a.seed); torch.manual_seed(a.seed); dev=torch.device('cuda' if torch.cuda.is_available() else ('mps' if torch.backends.mps.is_available() else 'cpu'))
    pages=load_pages(a.data); groups=sorted({p['group'] for p in pages})
    if len(groups)<2: sys.exit('至少需要兩場考試且有 *_c.png；先 pairs_build/backfill_clean/synth。')
    rng.shuffle(groups); vg=set(groups[:max(1,min(len(groups)-1,round(len(groups)*a.val_frac)))])
    tr=[p for p in pages if p['group'] not in vg]; va=[p for p in pages if p['group'] in vg]
    print(f'裝置 {dev}；訓練 {len(tr)} 頁、驗證 {len(va)} 頁；來源 '+str({s:sum(p['source']==s for p in pages) for s in set(p['source'] for p in pages)}))
    model=RestoreNet(a.width,a.multitask).to(dev); opt=torch.optim.AdamW(model.parameters(),lr=2e-3,weight_decay=1e-4); best=(1e9,None)
    for ep in range(1,a.epochs+1):
        model.train(); total=0.
        for _ in range(a.steps):
            b=[crop(rng.choices(tr,weights=[p['w'] for p in tr])[0],rng) for _ in range(a.batch)]
            x=torch.tensor(np.stack([z[0] for z in b]))[:,None].float().to(dev); c=torch.tensor(np.stack([z[1] for z in b]))[:,None].float().to(dev); m=torch.tensor(np.stack([z[2] for z in b]))[:,None].float().to(dev); sw=torch.tensor([z[3] for z in b],device=dev).view(-1,1,1,1)
            y=model(x); printed=(c<.78).float(); l1=(torch.abs(y-c)*(1+4*printed+2*m)*sw).mean(); sy,sx=sobel(y); cy,cx=sobel(c); loss=l1+.25*(torch.abs(sy-cy).mean()+torch.abs(sx-cx).mean())
            opt.zero_grad(); loss.backward(); opt.step(); total+=loss.item()
        model.eval(); score=metrics(model,va,dev); print(f'epoch {ep}/{a.epochs} loss {total/a.steps:.4f} PSNR {score[0]:.2f} 印刷保留 {score[1]:.1%} 筆跡殘留 {score[2]:.1%}',flush=True)
        if score[0]>-best[0]: best=(-score[0],{k:v.detach().cpu().clone() for k,v in model.state_dict().items()})
    model.load_state_dict(best[1]); model.eval(); os.makedirs(a.out,exist_ok=True); model=model.cpu()
    torch.onnx.export(model,torch.ones(1,1,256,256),os.path.join(a.out,'dehand_img2img.onnx'),input_names=['x'],output_names=['clean'],opset_version=17,dynamic_axes={'x':{2:'h',3:'w'},'clean':{2:'h',3:'w'}})
    score=metrics(model,va,torch.device('cpu')); json.dump({'dpi':150,'validation_groups':sorted(vg),'validation_pages':[p['name'] for p in va],'psnr':score[0],'print_keep':score[1],'hand_residual':score[2],'note':'image-to-image residual U-Net; 未接 apply.py'},open(os.path.join(a.out,'dehand_img2img.json'),'w'),ensure_ascii=False,indent=2); print('完成：',a.out)
if __name__=='__main__': main()
