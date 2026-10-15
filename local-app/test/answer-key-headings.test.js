const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),Zip=require('jszip');
const authModule=require('../auth');
const authDir=fs.mkdtempSync(path.join(os.tmpdir(),'answer-heading-auth-'));
const auth=authModule.createAuth({dir:authDir});
authModule.createAuth=()=>auth;
const token=auth.inviteLogin(auth.createInvite('測試老師').code,'test').token;
const server=require('../server');
const questions=Array.from('DCBBDDBDAC',(answer,i)=>({no:i+1,kind:'calc',answer,detail:'計算說明',options:[]}));
test('詳解 render 四種標題組合只放使用者標頭與答案，舊 render 保留標籤',async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+server.address().port;
 try{
  for(const route of ['/api/answer-key/render','/api/render'])for(const title of ['', '講義標題'])for(const unit of ['', '3-1 基礎練習']){
   const filename='heading-regression-'+Date.now()+'-'+Math.random();
   try{
    const response=await fetch(base+route,{method:'POST',headers:{'content-type':'application/json',authorization:'Bearer '+token},body:JSON.stringify({filename,data:{questions,docTitle:title,unitName:unit,summaryLabel:'需人工確認',answerKeyMode:false}})});
    assert.equal(response.status,200);
    const zip=await Zip.loadAsync(Buffer.from(await response.arrayBuffer()));
    const xml=await zip.file('word/document.xml').async('string');
    const header=xml.split('<w:body>')[1].split('<w:tbl>')[0];
    const texts=[...header.matchAll(/<w:p>[\s\S]*?<\/w:p>/g)].map(([p])=>[...p.matchAll(/<w:t\b[^>]*>(.*?)<\/w:t>/g)].map(m=>m[1]).join(''));
    const answer=route==='/api/answer-key/render'||unit?'DCBBD/DBDAC':'需人工確認 DCBBD/DBDAC';
    assert.deepEqual(texts,[title,unit&&'【 '+unit+' 詳解 】',answer].filter(Boolean));
    if(route==='/api/answer-key/render')assert.ok(!xml.includes('需人工確認'));
   }finally{fs.rmSync(path.join(__dirname,'..','output',filename+'.docx'),{force:true});}
  }
 }finally{await new Promise(resolve=>server.close(resolve));fs.rmSync(authDir,{recursive:true,force:true});}
});
