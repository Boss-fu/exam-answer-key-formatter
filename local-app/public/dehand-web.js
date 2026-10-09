/* Browser-only dehand inference.  Kept dependency-free except ORT's pinned CDN. */
window.DehandWeb = (() => {
  const CDN = "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.20.1/dist/ort.min.js";
  let worker;
  function startWorker() {
    if (worker) return worker;
    const source = `importScripts('${CDN}');ort.env.wasm.wasmPaths='${CDN.replace(/[^/]*$/, "")}';
let session, mode;
async function init(preferGpu) {
  if (session) return;
  const json = await (await fetch('/models/dehand-web.json')).json();
  const providers = preferGpu ? ['webgpu','wasm'] : ['wasm'];
  const model = preferGpu ? '/models/'+json.models.webgpu : '/models/'+json.models.wasm_int8;
  try { session=await ort.InferenceSession.create(model,{executionProviders:providers}); mode=providers[0]; }
  catch(e) { session=await ort.InferenceSession.create('/models/'+json.models.wasm_int8,{executionProviders:['wasm']}); mode='wasm'; }
}
function median(a){a.sort((x,y)=>x-y);return a[a.length>>1]||255}
function rule(p,w,h){const core=[];for(let i=0;i<p.length;i++)if(p[i]<150)core.push(p[i]);const t=median(core)+7,out=new Uint8Array(p.length);for(let i=0;i<p.length;i++)out[i]=(p[i]<200&&p[i]>t)?255:0;return out}
function flat(p,w,h){const s=8, sw=Math.ceil(w/s),sh=Math.ceil(h/s),bg=new Uint8Array(sw*sh);for(let y=0;y<sh;y++)for(let x=0;x<sw;x++){let m=0;for(let yy=y*s;yy<Math.min(h,y*s+s);yy++)for(let xx=x*s;xx<Math.min(w,x*s+s);xx++)m=Math.max(m,p[yy*w+xx]);bg[y*sw+x]=m}const o=new Uint8Array(p.length);for(let y=0;y<h;y++)for(let x=0;x<w;x++){let b=bg[(y/s|0)*sw+(x/s|0)]||255;o[y*w+x]=Math.min(255,p[y*w+x]*255/Math.max(80,b))}return o}
function weight(v, edge){return Math.min(1,Math.max(.001,v/edge,(512-v)/edge))}
async function run(p,w,h,preferGpu){await init(preferGpu);p=flat(p,w,h);const sum=new Float32Array(w*h),ws=new Float32Array(w*h),step=464;for(let y=0;y<h;y+=step)for(let x=0;x<w;x+=step){const tw=Math.min(512,w-x),th=Math.min(512,h-y),a=new Float32Array(512*512);for(let yy=0;yy<th;yy++)for(let xx=0;xx<tw;xx++)a[yy*512+xx]=p[(y+yy)*w+x+xx]/255;const r=await session.run({x:new ort.Tensor('float32',a,[1,1,512,512])}),z=r.logits.data;for(let yy=0;yy<th;yy++)for(let xx=0;xx<tw;xx++){const i=(y+yy)*w+x+xx,q=yy*512+xx,ww=weight(xx,48)*weight(yy,48);sum[i]+=z[q]*ww;ws[i]+=ww}}
const pencil=rule(p,w,h),mask=new Uint8Array(w*h);for(let i=0;i<mask.length;i++)mask[i]=(sum[i]/ws[i]>0||pencil[i])?255:0;return {mask,gray:p,mode}}
self.onmessage=async e=>{try{const r=await run(new Uint8Array(e.data.pixels),e.data.w,e.data.h,e.data.preferGpu);postMessage({ok:true,...r},[r.mask.buffer,r.gray.buffer])}catch(err){postMessage({ok:false,error:String(err.message||err)})}};`;
    worker = new Worker(URL.createObjectURL(new Blob([source.split("'/models/").join("'" + new URL("./models/", location.href).href)], { type: "text/javascript" })));
    return worker;
  }
  function suggest(imageData, preferGpu = !!navigator.gpu) {
    const p = new Uint8Array(imageData.width * imageData.height);
    for (let i = 0; i < p.length; i++) { const j = i * 4; p[i] = (imageData.data[j] * 299 + imageData.data[j + 1] * 587 + imageData.data[j + 2] * 114) / 1000; }
    return new Promise((resolve, reject) => { const w = startWorker(); w.onmessage = e => e.data.ok ? resolve(e.data) : reject(new Error(e.data.error)); w.postMessage({ pixels: p.buffer, w: imageData.width, h: imageData.height, preferGpu }, [p.buffer]); });
  }
  return { suggest };
})();
