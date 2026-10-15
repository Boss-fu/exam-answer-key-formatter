const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('fs'),os=require('os'),path=require('path');
test('回饋需登入、老師不能讀管理員資料、未讀持久化與標記已讀',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'feedback-http-'));let time=new Date();
 const mod=require('../feedback'),store=mod.createFeedback({dir,now:()=>time});mod.createFeedback=()=>store;
 const authmod=require('../auth'),auth=authmod.createAuth({dir:path.join(dir,'auth')});authmod.createAuth=()=>auth;auth.setPassword('test-only-password');const admin=auth.adminLogin('test-only-password','test').token,invite=auth.createInvite('回報老師'),teacher=auth.inviteLogin(invite.code,'test').token;
 const server=require('../server');await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port;
 const req=(p,token,body)=>fetch(base+p,{method:body?'POST':'GET',headers:{authorization:'Bearer '+(token||''),'content-type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
 try{
 assert.equal((await req('/api/feedback','',{message:'test'})).status,401);
 assert.equal((await req('/api/admin/feedback',teacher)).status,403);
 assert.equal((await req('/api/feedback',teacher,{message:''})).status,400);
 const sent=await req('/api/feedback',teacher,{message:'<script>不是程式，是使用者原文</script>'});assert.equal(sent.status,200);const {id}=await sent.json();
 assert.equal((await req('/api/feedback',teacher,{message:'第二則'})).status,429);
 let data=await(await req('/api/admin/feedback',admin)).json();assert.equal(data.unread,1);assert.equal(data.items[0].label,'回報老師');assert.ok(!('senderId'in data.items[0]));
 assert.equal((await req('/api/admin/feedback/read',teacher,{id})).status,403);
 assert.equal((await req('/api/admin/feedback/read',admin,{id})).status,200);data=store.list({role:'admin'});assert.equal(data.unread,0);
 assert.equal(JSON.parse(fs.readFileSync(path.join(dir,'feedback.json')))[0].message,'<script>不是程式，是使用者原文</script>');
 time=new Date(+time+61000);assert.equal((await req('/api/feedback',teacher,{message:'新的問題'})).status,200);
 const quota=await(await req('/api/answer-key/quota',admin)).json();assert.equal(quota.unlimited,true);assert.equal(quota.remaining,null);
 const tq=await(await req('/api/answer-key/quota',teacher)).json();assert.equal(tq.unlimited,false);assert.equal(tq.limit,10);
 }finally{await new Promise(r=>server.close(r));fs.rmSync(dir,{recursive:true,force:true});}
});
