const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'../public/answer-workspace.js'),'utf8');
const source=html.split('/* ANSWER_CORE_START */')[1].split('/* ANSWER_CORE_END */')[0];
function browser({ua='Chrome',platform='',touch=0,image=true,health=false,same=false,storageError=false}={}){
 const elements=new Map(),storage=new Map(),calls=[],images=[],timers=[];
 const el=id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);};
 const context=vm.createContext({URL,Date,Promise,navigator:{userAgent:ua,platform,maxTouchPoints:touch},
 location:{href:(same?'https://company.test:8443':'https://portal.test')+'/answer.html?invite=A%2BB&other=1&other=2#review',replace(url){calls.push(url);}},
 target:{baseUrl:'https://company.test:8443'},$:el,
 sessionStorage:{getItem(k){if(storageError)throw Error();return storage.get(k);},setItem(k,v){if(storageError)throw Error();storage.set(k,v);}},
 Image:class{set src(url){images.push(url);if(image!==null)queueMicrotask(()=>image?this.onload():this.onerror());}},
 setTimeout(fn,ms){timers.push({fn,ms});return timers.length;},clearTimeout(){},setInterval(){return 1;},timeoutSignal(){},
 syncHostConnection(){},updateButton(){},async refreshQuota(){},OcrApi:{async request(){calls.push('health');if(!health)throw Object.assign(Error('offline'),{connectionFailure:true});return {json:async()=>({ok:true,capabilities:['answer-key']})};}}
 });
 vm.runInContext(source.slice(source.indexOf('function directAnswerUrl'),source.indexOf("let token=''"))+ '\nlet quotaPoll=0,ready=false,busy=false,quota=null;\n'+source.slice(source.indexOf('async function connect(){'),source.indexOf('function choose(')),context);
 return {context,calls,images,timers,storage,el,connect:()=>vm.runInContext('connect()',context)};
}
test('iPhone、iPad 與桌面連線成功均留在原頁，不發圖片探測或跳轉',async()=>{for(const ua of ['Chrome','iPhone Safari','iPhone CriOS','iPhone FxiOS','iPad Safari','Macintosh']){const b=browser({ua,health:true,touch:5});await b.connect();assert.deepEqual(b.calls,['health']);assert.equal(b.images.length,0);assert.equal(b.el('connectionBadge').textContent,'已連線');}});
test('跨站連線失敗仍留原頁，提供重試與手動備用連結',async()=>{for(const ua of ['Chrome','iPhone Safari']){const b=browser({ua});await b.connect();assert.deepEqual(b.calls,['health']);assert.equal(b.images.length,0);assert.equal(b.el('directConnection').hidden,false);assert.equal(b.el('reconnect').hidden,false);assert.equal(b.el('connection').textContent,'offline');assert.equal(new URL(b.el('directLink').href).searchParams.get('invite'),'A+B');}});
test('公司同源連線也不跳轉，不顯示相同目的地備用連結',async()=>{const b=browser({same:true,health:true,ua:'iPhone Safari'});await b.connect();assert.deepEqual(b.calls,['health']);assert.equal(b.el('directConnection').hidden,true);});
