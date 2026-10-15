'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const fail=(message,statusCode=400)=>{throw Object.assign(new Error(message),{statusCode});};
function createFeedback({dir=path.join(__dirname,'output','feedback'),now=()=>new Date()}={}){
 const file=path.join(dir,'feedback.json');
 function read(){try{return JSON.parse(fs.readFileSync(file,'utf8'));}catch(e){if(e.code==='ENOENT')return [];throw e;}}
 function save(items){fs.mkdirSync(dir,{recursive:true});const tmp=file+'.tmp';fs.writeFileSync(tmp,JSON.stringify(items,null,2));fs.renameSync(tmp,file);}
 return {
  submit(identity,input){
   if(!identity?.role)fail('請先登入。',401);
   const message=typeof input?.message==='string'?input.message.trim():'';
   if(!message||message.length>3000)fail('請填寫回饋內容，最多3000字。');
   const items=read(),who=identity.inviteId||identity.role;
   if(items.some(x=>x.senderId===who&&now()-new Date(x.createdAt)<60000))fail('回饋已送出，請一分鐘後再送下一則。',429);
   const item={id:crypto.randomUUID(),senderId:who,label:identity.label||'管理員',role:identity.role,message,createdAt:now().toISOString(),readAt:null};items.unshift(item);save(items);return {ok:true,id:item.id};
  },
  list(identity){if(identity?.role!=='admin')fail('只有管理員可以查看回饋。',403);const items=read();return {items:items.map(({senderId,...item})=>item),unread:items.filter(x=>!x.readAt).length};},
  markRead(identity,id){if(identity?.role!=='admin')fail('只有管理員可以處理回饋。',403);const items=read(),item=items.find(x=>x.id===id);if(!item)fail('找不到這則回饋。',404);item.readAt=now().toISOString();save(items);return {ok:true};}
 };
}
module.exports={createFeedback};
