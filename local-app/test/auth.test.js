const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {createAuth,normalizeCode,passwordHash,verifyPassword}=require('../auth');
test('密碼、邀請碼、session、限流與到期',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'auth-test-'));let time=0;const auth=createAuth({dir,now:()=>time});
 try{const stored=passwordHash('password123');assert.ok(verifyPassword('password123',stored));assert.ok(!verifyPassword('wrong',stored));assert.notDeepEqual(stored,passwordHash('password123'));assert.throws(()=>auth.adminLogin('x','unset'),/尚未設定/);auth.setPassword('password123');const admin=auth.adminLogin('password123','admin');assert.equal(auth.authenticate(admin.token).role,'admin');auth.logout(admin.token);assert.throws(()=>auth.authenticate(admin.token));
 const invite=auth.createInvite('王老師');assert.match(invite.code,/^[A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4}$/);assert.equal(normalizeCode(' ab-cd efgh '),'ABCDEFGH');assert.ok(!JSON.stringify(auth.listInvites()).includes(invite.code));assert.ok(!fs.readFileSync(path.join(dir,'auth.json'),'utf8').includes(normalizeCode(invite.code)));
 const teacher=auth.inviteLogin(' '+invite.code.toLowerCase().replace('-',' - ')+' ','teacher');const who=auth.authenticate(teacher.token);auth.recordJob(who);assert.equal(auth.listInvites()[0].jobs,1);auth.setDisabled(invite.id,true);assert.throws(()=>auth.authenticate(teacher.token),/停用/);auth.setDisabled(invite.id,false);assert.throws(()=>auth.authenticate(teacher.token),/停用/);const fresh=auth.inviteLogin(invite.code,'teacher');time=30*86400000;assert.throws(()=>auth.authenticate(fresh.token),/過期/);
 for(let i=0;i<10;i++)assert.throws(()=>auth.inviteLogin('bad','limit'),e=>e.statusCode===401);assert.throws(()=>auth.inviteLogin(invite.code,'limit'),e=>e.statusCode===429);time+=60000;assert.equal(auth.inviteLogin(invite.code,'limit').role,'teacher');
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
test('HTTP 授權邊界與 CORS',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'auth-http-'));const auth=createAuth({dir});require('../auth').createAuth=()=>auth;
 const server=require('../server');await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port;
 const post=(url,body={},token)=>fetch(base+url,{method:'POST',headers:{'content-type':'application/json',...(token?{authorization:'Bearer '+token}:{})},body:JSON.stringify(body)});
 try{assert.equal((await fetch(base+'/api/answer-key',{method:'POST'})).status,401);assert.equal((await post('/api/answer-key')).status,401);assert.equal((await post('/api/answer-key/render')).status,401);assert.equal((await fetch(base+'/api/answer-key/quota')).status,401);assert.equal((await post('/api/render')).status,400);assert.equal((await fetch(base+'/api/health')).status,200);
 const invite=auth.createInvite('老師'),login=await(await post('/api/auth/invite',{code:invite.code})).json();assert.equal((await fetch(base+'/api/admin/invites',{headers:{authorization:'Bearer '+login.token}})).status,403);assert.equal((await fetch(base+'/api/answer-key/quota',{headers:{authorization:'Bearer '+login.token}})).status,200);auth.setDisabled(invite.id,true);assert.equal((await fetch(base+'/api/auth/me',{headers:{authorization:'Bearer '+login.token}})).status,401);
 auth.setPassword('password123');const admin=await(await post('/api/auth/admin',{password:'password123'})).json();
 const created=await(await post('/api/admin/invites',{label:'新老師'},admin.token)).json();assert.ok(created.code);
 const listing=await(await fetch(base+'/api/admin/invites',{headers:{authorization:'Bearer '+admin.token}})).json();assert.ok(listing.invites.some(i=>i.id===created.id));assert.ok(!JSON.stringify(listing).includes(created.code));
 assert.equal((await post('/api/admin/invites/disable',{id:created.id},admin.token)).status,200);assert.equal((await post('/api/auth/invite',{code:created.code})).status,401);assert.equal((await post('/api/admin/invites/enable',{id:created.id},admin.token)).status,200);assert.equal((await post('/api/auth/invite',{code:created.code})).status,200);
 const deletedTeacher=await(await post('/api/auth/invite',{code:created.code})).json();
 assert.equal((await post('/api/admin/invites/delete',{id:created.id},deletedTeacher.token)).status,403);
 assert.equal((await post('/api/admin/invites/delete',{id:created.id})).status,401);
 assert.equal((await post('/api/admin/invites/delete',{id:'missing'},admin.token)).status,404);
 assert.equal((await post('/api/admin/invites/delete',{id:created.id},admin.token)).status,200);
 assert.equal((await fetch(base+'/api/auth/me',{headers:{authorization:'Bearer '+deletedTeacher.token}})).status,401);
 assert.equal((await post('/api/auth/invite',{code:created.code})).status,401);
 assert.equal((await post('/api/admin/invites/enable',{id:created.id},admin.token)).status,404);
 const persisted=createAuth({dir});assert.ok(!persisted.listInvites().some(i=>i.id===created.id));assert.equal(persisted.authenticate(admin.token).role,'admin');
 assert.equal((await post('/api/auth/logout',{},admin.token)).status,200);assert.equal((await fetch(base+'/api/auth/me',{headers:{authorization:'Bearer '+admin.token}})).status,401);
 const cors=await fetch(base+'/api/answer-key',{method:'OPTIONS',headers:{origin:'https://tzutzu-ocr.vercel.app','access-control-request-method':'POST','access-control-request-headers':'content-type, authorization'}});assert.equal(cors.status,204);assert.match(cors.headers.get('access-control-allow-headers'),/authorization/);
 }finally{await new Promise(r=>server.close(r));fs.rmSync(dir,{recursive:true,force:true});}
});
