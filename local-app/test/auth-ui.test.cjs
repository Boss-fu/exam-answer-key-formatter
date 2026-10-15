const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../public/answer.html'),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
test('前端 Bearer、401 清除 token 與邀請連結移除',async()=>{
 const elements=new Map(),storage=new Map();let options,historyUrl;
 const element=id=>{if(!elements.has(id))elements.set(id,{value:'',hidden:false,textContent:'',disabled:false,addEventListener(){},replaceChildren(){},classList:{},setAttribute(){}});return elements.get(id);};
 const context=vm.createContext({document:{getElementById:element},localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k),length:0},OcrServerConfig:{servers:{company:{}}},OcrApi:{async request(p,o){options=o;return {json:async()=>({token:'new-token',role:'teacher',label:'老師'})};}},URL,location:{href:'https://example.test/answer.html?invite=ABCD-EFGH&keep=1'},history:{replaceState(a,b,url){historyUrl=url;}},AbortSignal,console});
 vm.runInContext(source.replace('void initialize();',''),context);
 await vm.runInContext("token='old-token';request('/api/answer-key/quota')",context);assert.equal(options.headers.Authorization,'Bearer old-token');
 context.OcrApi.request=async()=>{throw Object.assign(new Error('disabled'),{status:401,detail:'此邀請碼已被管理員停用'});};
 storage.set('answer-token','old-token');await assert.rejects(vm.runInContext("request('/api/auth/me')",context));assert.equal(storage.has('answer-token'),false);assert.equal(element('login').hidden,false);assert.equal(element('loginError').textContent,'此邀請碼已被管理員停用');
 context.OcrApi.request=async()=>({json:async()=>({token:'new-token',role:'teacher',label:'老師'})});
 vm.runInContext('refreshQuota=async()=>{};restoreDraft=async()=>{};',context);
 await vm.runInContext("login('ABCD-EFGH')",context);assert.equal(storage.get('answer-token'),'new-token');assert.equal(historyUrl,'/answer.html?keep=1');assert.equal(element('identity').textContent,'老師');assert.equal(element('inviteManager').hidden,true);
 assert.ok(!/\bconfirm\(/.test(source));
});
