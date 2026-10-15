const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('fs'),os=require('os'),path=require('path');
const {createQuota}=require('../quota');
test('管理員不限額且不占老師額度，仍記錄、跨午夜且不能繞過忙碌限制',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'admin-quota-'));let time=new Date('2026-10-15T15:59:00Z');
 try{
 const q=createQuota({dir,limit:1,now:()=>time}),admin={role:'admin'},teacher={role:'teacher'};
 let lease=q.acquire(teacher);lease.charge();lease.release();assert.equal(q.status(teacher).remaining,0);assert.throws(()=>q.acquire(teacher),{statusCode:429});
 for(let i=0;i<12;i++){lease=q.acquire(admin);assert.throws(()=>q.acquire(admin),{statusCode:429});lease.charge();lease.charge();lease.record({who:'管理員',claude:{input:1}});lease.release();}
 const status=q.status(admin);assert.equal(status.unlimited,true);assert.equal(status.limit,null);assert.equal(status.remaining,null);assert.equal(q.status(teacher).used,1);assert.equal(q.status(teacher).tokens.claude,12);
 assert.equal(createQuota({dir,limit:1,now:()=>time}).status(teacher).remaining,0);
 const records=JSON.parse(fs.readFileSync(path.join(dir,status.date+'.json'))).records;assert.equal(records.length,12);assert.ok(records.every(x=>x.quotaExempt));
 time=new Date('2026-10-15T16:01:00Z');assert.equal(q.status(teacher).remaining,1);assert.equal(q.status(admin).unlimited,true);
 assert.throws(()=>createQuota({dir,limit:0}).acquire({role:'teacher',unlimited:true}),{statusCode:429});
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
