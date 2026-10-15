const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'../public/answer.html'),'utf8');
const source=html.match(/<script>([\s\S]*?)<\/script>/)[1];
function browser({ua='Chrome',platform='',touch=0,image=true,health=false,same=false,storageError=false}={}){
 const elements=new Map(),storage=new Map(),calls=[],images=[],timers=[];
 const el=id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);};
 const context=vm.createContext({URL,Date,Promise,navigator:{userAgent:ua,platform,maxTouchPoints:touch},
 location:{href:(same?'https://company.test:8443':'https://portal.test')+'/answer.html?invite=A%2BB&other=1&other=2#review',replace(url){calls.push(url);}},
 target:{baseUrl:'https://company.test:8443'},$:el,
 sessionStorage:{getItem(k){if(storageError)throw Error();return storage.get(k);},setItem(k,v){if(storageError)throw Error();storage.set(k,v);}},
 Image:class{set src(url){images.push(url);if(image!==null)queueMicrotask(()=>image?this.onload():this.onerror());}},
 setTimeout(fn,ms){timers.push({fn,ms});return timers.length;},clearTimeout(){},setInterval(){return 1;},timeoutSignal(){},
 updateButton(){},async refreshQuota(){},OcrApi:{async request(){calls.push('health');if(!health)throw Object.assign(Error('offline'),{connectionFailure:true});return {json:async()=>({ok:true,capabilities:['answer-key']})};}}
 });
 vm.runInContext(source.slice(source.indexOf('function directAnswerUrl'),source.indexOf("let token=''"))+ '\nlet quotaPoll=0,ready=false,busy=false,quota=null;\n'+source.slice(source.indexOf('async function connect(){'),source.indexOf('function choose(')),context);
 return {context,calls,images,timers,storage,el,connect:()=>vm.runInContext('connect()',context)};
}
test('iOS / iPadOS WebKit: image success redirects before fetch and retains query/hash',async()=>{
 for(const options of [{ua:'iPhone Safari'},{ua:'iPad Safari'},{ua:'iPhone CriOS'},{ua:'iPhone FxiOS'},{ua:'Mac browser',platform:'MacIntel',touch:5}]){
  const b=browser(options);await b.connect();assert.equal(b.calls.length,1);assert.equal(b.calls[0],'https://company.test:8443/answer.html?invite=A%2BB&other=1&other=2#review');assert.match(b.images[0],/\/logo.png\?probe=\d+$/);assert.equal(b.timers[0].ms,6000);assert.equal(b.el('connection').textContent,'正在切換到公司電腦直連頁面…');
 }
});
test('desktop redirects only on fetch failure with successful image probe',async()=>{
 const blocked=browser();await blocked.connect();assert.equal(blocked.calls.length,2);
 const healthy=browser({health:true});await healthy.connect();assert.deepEqual(healthy.calls,['health']);
 for(const ua of ['Chrome','iPhone Safari']){const offline=browser({image:false,ua});await offline.connect();assert.deepEqual(offline.calls,['health']);assert.equal(offline.el('directConnection').hidden,false);assert.equal(offline.el('reconnect').hidden,false);assert.equal(offline.el('connection').textContent,'offline');}
});
test('image timeout resolves false after six seconds',async()=>{
 const b=browser({image:null});const probe=vm.runInContext('probeCompanyImage()',b.context);assert.equal(b.timers[0].ms,6000);b.timers[0].fn();assert.equal(await probe,false);
});
test('same-origin skips image probe and automatic redirect',async()=>{
 const b=browser({same:true,ua:'iPhone Safari'});await b.connect();assert.deepEqual(b.calls,['health']);assert.equal(b.images.length,0);assert.equal(vm.runInContext('switchToDirect()',b.context),false);assert.equal(b.el('directConnection').hidden,true);
});
test('sessionStorage prevents repeated redirects for 30 seconds; unavailable storage is tolerated',async()=>{
 const b=browser({ua:'iPhone Safari'});await b.connect();await b.connect();assert.equal(b.calls.filter(c=>c.startsWith('https:')).length,1);
 b.storage.set('answer-direct-redirect',String(Date.now()-30001));assert.equal(vm.runInContext('switchToDirect()',b.context),true);
 const restricted=browser({ua:'iPhone Safari',storageError:true});await restricted.connect();assert.equal(restricted.calls.length,1);
});
