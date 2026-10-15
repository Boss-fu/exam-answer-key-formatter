const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const server=require('../server');
test('直連靜態檔白名單與影片 Range',async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const base='http://127.0.0.1:'+server.address().port;
 try{
 const notes=await fetch(base+'/answer-changelog.json');assert.equal(notes.status,200);assert.match(notes.headers.get('content-type'),/application\/json/);assert.equal((await notes.json()).entries[0].version,'2026.10.15.8');
 const bytes=fs.readFileSync(path.join(__dirname,'../public/answer-tutorial.mp4'));
 const full=await fetch(base+'/answer-tutorial.mp4');assert.equal(full.status,200);assert.equal(full.headers.get('content-type'),'video/mp4');assert.deepEqual(Buffer.from(await full.arrayBuffer()),bytes);
 for(const [range,start,end] of [['bytes=0-9',0,9],['bytes=10-',10,bytes.length-1],['bytes=-8',bytes.length-8,bytes.length-1]]){
 const res=await fetch(base+'/answer-tutorial.mp4',{headers:{Range:range}});assert.equal(res.status,206);assert.equal(res.headers.get('content-range'),`bytes ${start}-${end}/${bytes.length}`);assert.deepEqual(Buffer.from(await res.arrayBuffer()),bytes.subarray(start,end+1));}
 for(const range of ['bytes=99999999-','bytes=8-2','bytes=0-1,5-6','bytes=-0'])assert.equal((await fetch(base+'/answer-tutorial.mp4',{headers:{Range:range}})).status,416);
 for(const name of ['answer-private.json','other.mp4','answer.html.bak','server.js','public/answer-changelog.json'])assert.equal((await fetch(base+'/'+name)).status,404);
 }finally{await new Promise(r=>server.close(r));}
});
const html=fs.readFileSync(path.join(__dirname,'../public/answer.html'),'utf8'),source=html.match(/<script>([\s\S]*?)<\/script>/)[1];
function browser(href,ua,touch=0){const els=new Map(),el=id=>{if(!els.has(id))els.set(id,{hidden:true});return els.get(id);};const c=vm.createContext({URL,location:{href},navigator:{userAgent:ua,maxTouchPoints:touch},target:{baseUrl:'https://company.test:8443'},$:el});const helpers=source.slice(source.indexOf('function directAnswerUrl'),source.indexOf("let token=''"));vm.runInContext(helpers,c);return {c,el};}
test('直連按鈕：Safari 預先顯示、失敗顯示、同源隱藏並保留 invite',()=>{
 for(const [ua,touch] of [['iPhone Version/18 Safari/605',0],['Macintosh Version/18 Safari/605',5]]){const b=browser('https://portal.test/answer.html?invite=A%2BB&other=1',ua,touch);vm.runInContext('updateDirectConnection()',b.c);assert.equal(b.el('directConnection').hidden,false);assert.equal(new URL(b.el('directLink').href).searchParams.get('invite'),'A+B');}
 const b=browser('https://portal.test/answer.html?invite=ABCD-EFGH','Chrome');vm.runInContext('updateDirectConnection()',b.c);assert.equal(b.el('directConnection').hidden,true);vm.runInContext('updateDirectConnection(true)',b.c);assert.equal(b.el('directConnection').hidden,false);
 b.c.location.href='https://company.test:8443/answer.html?invite=ABCD-EFGH';vm.runInContext('updateDirectConnection(true)',b.c);assert.equal(b.el('directConnection').hidden,true);
 assert.match(source,/updateDirectConnection\(error.connectionFailure===true\)/);assert.match(source,/createdDirectLink'\).value=directAnswerUrl\(item.code\)/);assert.match(html,/id="copyDirectInvite"/);
});
