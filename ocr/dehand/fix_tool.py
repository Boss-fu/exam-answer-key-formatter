#!/usr/bin/env python3
"""去筆跡人工修正工具（工讀生用）：模型先擦一遍，人用「消除筆／還原筆」修正，存下來就是新的訓練資料。

    python3 ocr/dehand/fix_tool.py 考卷.pdf [更多.pdf…|資料夾]  → 瀏覽器開 http://localhost:8790
    畫面：左＝原稿（紅色＝會被擦掉的筆跡），右＝擦完的樣子。
    消除筆（E）：塗到的墨跡標成筆跡　還原筆（R）：塗到的地方標回印刷　[ / ]：筆刷大小　S：存檔並下一頁
存檔：ocr/dehand/data/_manual/<檔名>_p<頁>_x.png / _m.png（train.py 會自動讀、權重 ×2）。
重新訓練：python3 ocr/dehand/train.py
"""
import base64
import glob
import hashlib
import http.server
import json
import os
import re
import socketserver
import sys
import webbrowser

import cv2
import numpy as np
import pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apply as dehand  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "_manual")
PORT = int(os.environ.get("PORT", 8790))
DPI = 150
PAGES = []          # [(pdf, page_index, out_stem)]


def collect(args):
    pdfs = []
    for a in args:
        pdfs += sorted(glob.glob(os.path.join(a, "**", "*.pdf"), recursive=True)) if os.path.isdir(a) else [a]
    for p in pdfs:
        stem = re.sub(r"[\\/:*?\"<>|\s]+", "_", os.path.splitext(os.path.basename(p))[0])[:50]
        stem += "_" + hashlib.md5(p.encode()).hexdigest()[:4]
        for i in range(len(pymupdf.open(p))):
            PAGES.append((p, i, f"{stem}_p{i + 1}"))


def png64(img):
    return "data:image/png;base64," + base64.b64encode(cv2.imencode(".png", img)[1]).decode()


def load(k):
    pdf, i, stem = PAGES[k]
    xp, mp = os.path.join(OUT, stem + "_x.png"), os.path.join(OUT, stem + "_m.png")
    if os.path.exists(mp):                       # 修過的頁：讀回上次的結果
        return cv2.imread(xp, 0), cv2.imread(mp, 0), True
    pix = pymupdf.open(pdf)[i].get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w).copy()
    s = dehand._load()
    if s:
        sess, th, _ = s
        m = (dehand.predict(gray, sess) >= th).astype(np.uint8) * 255
    else:
        m = np.zeros_like(gray)
    return gray, m, False


HTML = r"""<!doctype html><meta charset=utf-8><title>去筆跡修正</title>
<style>
body{margin:0;font:14px system-ui;background:#f4f4f2;color:#222}
#bar{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:8px 12px;display:flex;gap:10px;align-items:center;z-index:2;flex-wrap:wrap}
button{font:inherit;padding:5px 12px;border:1px solid #bbb;border-radius:6px;background:#fff;cursor:pointer}
button.on{background:#222;color:#fff;border-color:#222}
#wrap{display:flex;gap:8px;padding:8px;overflow:auto}
canvas{background:#fff;box-shadow:0 0 0 1px #ccc;cursor:crosshair;max-width:none}
#msg{color:#666}
</style>
<div id=bar>
 <button id=prev>← 上一頁</button><b id=pos></b><button id=next>下一頁 →</button>
 <button id=er class=on>消除筆 (E)</button><button id=rs>還原筆 (R)</button>
 筆刷 <input id=sz type=range min=3 max=60 value=14> <span id=szv>14</span>
 縮放 <input id=zm type=range min=30 max=150 value=70>
 <button id=save>存檔並下一頁 (S)</button><span id=msg></span>
</div>
<div id=wrap><canvas id=a></canvas><canvas id=b></canvas></div>
<script>
let k=0,N=0,gray,mask,ink,W,H,mode=1,down=false,last=null;
const A=a.getContext('2d'),B=b.getContext('2d');
function img(src){return new Promise(r=>{const i=new Image();i.onload=()=>r(i);i.src=src})}
function pix(i){const c=document.createElement('canvas');c.width=i.width;c.height=i.height;const x=c.getContext('2d');x.drawImage(i,0,0);return x.getImageData(0,0,i.width,i.height).data}
async function go(n){
 const r=await (await fetch('/page/'+n)).json();N=r.n;k=n;
 const gi=await img(r.x),mi=await img(r.m);W=gi.width;H=gi.height;
 const g=pix(gi),m=pix(mi);gray=new Uint8Array(W*H);mask=new Uint8Array(W*H);ink=new Uint8Array(W*H);
 for(let i=0;i<W*H;i++){gray[i]=g[i*4];mask[i]=m[i*4]>127?1:0;ink[i]=g[i*4]<170?1:0}
 for(const c of [a,b]){c.width=W;c.height=H}
 pos.textContent=(k+1)+' / '+N+(r.done?'（已修過）':'');msg.textContent=r.name;zoom();draw();
}
function draw(x0=0,y0=0,x1=W,y1=H){
 x0=Math.max(0,x0|0);y0=Math.max(0,y0|0);x1=Math.min(W,x1|0);y1=Math.min(H,y1|0);if(x1<=x0||y1<=y0)return;
 const w=x1-x0,h=y1-y0,da=A.createImageData(w,h),db=B.createImageData(w,h);
 for(let y=0;y<h;y++)for(let x=0;x<w;x++){const i=(y+y0)*W+x+x0,j=(y*w+x)*4,v=gray[i];
  if(mask[i]){da.data[j]=230;da.data[j+1]=40;da.data[j+2]=40;db.data[j]=db.data[j+1]=db.data[j+2]=255}
  else{da.data[j]=da.data[j+1]=da.data[j+2]=v;db.data[j]=db.data[j+1]=db.data[j+2]=v}
  da.data[j+3]=db.data[j+3]=255}
 A.putImageData(da,x0,y0);B.putImageData(db,x0,y0);
}
function zoom(){const s=zm.value/100;for(const c of [a,b]){c.style.width=W*s+'px'}}
function paint(e){
 const c=e.target,r=c.getBoundingClientRect(),x=(e.clientX-r.left)*W/r.width,y=(e.clientY-r.top)*H/r.height;
 const R=+sz.value,pts=last?[last,[x,y]]:[[x,y],[x,y]];last=[x,y];
 const st=Math.max(1,Math.hypot(pts[1][0]-pts[0][0],pts[1][1]-pts[0][1])/(R/2));
 for(let t=0;t<=st;t++){const cx=pts[0][0]+(pts[1][0]-pts[0][0])*t/st,cy=pts[0][1]+(pts[1][1]-pts[0][1])*t/st;
  for(let yy=Math.max(0,cy-R|0);yy<Math.min(H,cy+R);yy++)for(let xx=Math.max(0,cx-R|0);xx<Math.min(W,cx+R);xx++)
   if((xx-cx)**2+(yy-cy)**2<=R*R){const i=yy*W+xx;mask[i]=mode&&ink[i]?1:0}}
 draw(Math.min(pts[0][0],pts[1][0])-R,Math.min(pts[0][1],pts[1][1])-R,Math.max(pts[0][0],pts[1][0])+R+1,Math.max(pts[0][1],pts[1][1])+R+1);
}
for(const c of [a,b]){c.onpointerdown=e=>{down=true;last=null;paint(e)};c.onpointermove=e=>{if(down)paint(e)}}
onpointerup=()=>{down=false;last=null};
function setMode(m){mode=m;er.classList.toggle('on',m==1);rs.classList.toggle('on',m==0)}
er.onclick=()=>setMode(1);rs.onclick=()=>setMode(0);
sz.oninput=()=>szv.textContent=sz.value;zm.oninput=zoom;
prev.onclick=()=>k>0&&go(k-1);next.onclick=()=>k<N-1&&go(k+1);
save.onclick=async()=>{const c=document.createElement('canvas');c.width=W;c.height=H;const x=c.getContext('2d'),d=x.createImageData(W,H);
 for(let i=0;i<W*H;i++){const v=mask[i]?255:0;d.data[i*4]=d.data[i*4+1]=d.data[i*4+2]=v;d.data[i*4+3]=255}x.putImageData(d,0,0);
 const r=await fetch('/save/'+k,{method:'POST',body:c.toDataURL('image/png')});msg.textContent=r.ok?'已存 ✔':'存檔失敗';if(r.ok&&k<N-1)go(k+1)};
onkeydown=e=>{if(e.key=='e')setMode(1);if(e.key=='r')setMode(0);if(e.key=='s')save.onclick();
 if(e.key=='[')sz.value=Math.max(3,sz.value-3),sz.oninput();if(e.key==']')sz.value=+sz.value+3,sz.oninput()};
go(0);
</script>"""


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/":
            return self.send(HTML, "text/html")
        m = re.match(r"/page/(\d+)$", self.path)
        if m:
            k = int(m.group(1))
            g, mk, done = load(k)
            return self.send(json.dumps({"n": len(PAGES), "x": png64(g), "m": png64(mk), "done": done,
                                         "name": f"{os.path.basename(PAGES[k][0])} 第 {PAGES[k][1] + 1} 頁"}))
        self.send_error(404)

    def do_POST(self):
        m = re.match(r"/save/(\d+)$", self.path)
        if not m:
            return self.send_error(404)
        k = int(m.group(1))
        data = self.rfile.read(int(self.headers["Content-Length"])).decode()
        mk = cv2.imdecode(np.frombuffer(base64.b64decode(data.split(",", 1)[1]), np.uint8), cv2.IMREAD_GRAYSCALE)
        g, _, _ = load(k)
        os.makedirs(OUT, exist_ok=True)
        stem = PAGES[k][2]
        cv2.imwrite(os.path.join(OUT, stem + "_x.png"), g)
        cv2.imwrite(os.path.join(OUT, stem + "_m.png"), (mk > 127).astype(np.uint8) * 255)
        self.send("{}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    collect(sys.argv[1:])
    print(f"{len(PAGES)} 頁待修正 → http://localhost:{PORT}")
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), H) as srv:
        if os.environ.get("NO_BROWSER") != "1":
            webbrowser.open(f"http://localhost:{PORT}")
        srv.serve_forever()


if __name__ == "__main__":
    main()
