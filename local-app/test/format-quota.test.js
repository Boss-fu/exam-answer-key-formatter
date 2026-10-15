const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('fs'),os=require('os'),path=require('path'),Zip=require('jszip');
const {buildDocxBuffer,checkAnswers}=require('../../code/render');
const {createQuota}=require('../quota');
const {parseClaude,codexTokens,emptyUsage,addUsage}=require('../usage');
const options=[{label:'A',text:'正確：原理'},{label:'B',text:'錯誤：理由'}];
test('單一表格、四種題型、範本段落與列屬性',async()=>{
 const questions=[{no:1,answer:'A',detail:'共用',options},{no:2,answer:'B',kind:'calc',detail:'2+3=5',options:[]},{no:3,negative:true,answer:'B',options},{no:4,answer:'AC',options:[...options,{label:'C',text:'正確：理由'}]}];
 assert.deepEqual(checkAnswers(questions),[]);
 const zip=await Zip.loadAsync(await buildDocxBuffer({questions})),xml=await zip.file('word/document.xml').async('string');
 assert.equal((xml.match(/<w:tbl>/g)||[]).length,1);
 const body=xml.match(/<w:body>([\s\S]*?)<w:sectPr/)[1];
 assert.match(body,/^<w:p>[\s\S]*?<\/w:p><w:tbl>/);
 assert.ok(!body.includes('</w:tbl><w:p>'));
 for(const p of xml.matchAll(/<w:pPr>([\s\S]*?)<\/w:pPr>/g)){assert.match(p[1],/<w:snapToGrid w:val="0"\/>/);assert.match(p[1],/<w:adjustRightInd w:val="0"\/>/);assert.match(p[1],/<w:contextualSpacing w:val="0"\/>/);}
 assert.equal((xml.match(/<w:p>/g)||[]).length,(xml.match(/<w:pPr>/g)||[]).length);
 for(const row of xml.matchAll(/<w:tr>([\s\S]*?)<\/w:tr>/g))assert.match(row[1],/<w:cantSplit\/>/);
 const rows=[...xml.matchAll(/<w:tr>([\s\S]*?)<\/w:tr>/g)].map(x=>x[1]);
 const calc=rows.findIndex(r=>r.includes('<w:t xml:space="preserve">2</w:t>'));
 assert.ok(calc>=0);assert.ok(!rows.slice(calc,calc+2).join('').includes('(A)'));assert.ok(rows[calc+1].includes('2+3=5'));
 assert.match(xml,/<w:tblW w:type="dxa" w:w="10204"\/>/);
 assert.match(xml,/<w:gridCol w:w="8050"\/>/);
 const styles=await zip.file('word/styles.xml').async('string');assert.match(styles,/<w:docDefaults>[\s\S]*DFPYuanMedium-B5/);
});
test('否定題、多選、計算題與非字母答案的一致性',()=>{
 const rules=require('../public/answer-rules');
 assert.equal(rules.isMismatch({negative:true,answer:'B',options}),false);
 assert.equal(rules.isMismatch({negative:true,answer:'A',options}),true);
 assert.equal(rules.isMismatch({negative:true,answer:'BD',options:[...options,{label:'D',text:' 錯誤：說明'}]}),false);
 assert.equal(rules.isMismatch({kind:'calc',answer:'A',options:[]}),false);
 assert.equal(rules.isMismatch({answer:'12',options}),false);
});
test('額度不扣、扣一次、持久化、跨台北午夜與忙碌 429',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'answer-quota-'));let time=new Date('2026-10-15T15:59:00Z');
 try{const q=createQuota({dir,limit:1,now:()=>time});let lease=q.acquire();assert.equal(q.status().used,0);assert.throws(()=>q.acquire(),{statusCode:429,message:'目前已有一份考卷在處理中，請幾分鐘後再試'});lease.release();lease=q.acquire();lease.charge();lease.charge();lease.record({claude:{input:12,output:3},chatgpt:{tokens:4}});lease.release();assert.equal(q.status().used,1);assert.deepEqual(q.status().tokens,{claude:15,chatgpt:4});assert.equal(createQuota({dir,limit:1,now:()=>time}).status().used,1);assert.throws(()=>q.acquire(),{statusCode:429});time=new Date('2026-10-15T16:00:00Z');assert.equal(q.status().date,'2026-10-16');assert.equal(q.status().used,0);assert.deepEqual(q.status().tokens,{claude:0,chatgpt:0});}finally{fs.rmSync(dir,{recursive:true,force:true});}
});
test('Claude JSON、缺少 usage、純文字 fallback、Codex tokens used',()=>{
 const total=emptyUsage(),collect=u=>addUsage(total,u);
 assert.equal(parseClaude(JSON.stringify({result:'{"questions":[]}',usage:{input_tokens:2,output_tokens:3,cache_read_input_tokens:4,cache_creation_input_tokens:5},total_cost_usd:0.2}),collect),'{"questions":[]}');
 parseClaude('{"result":"{}"}',collect);assert.equal(parseClaude('{"questions":[]}',collect),'{"questions":[]}');
 assert.deepEqual(total.claude,{calls:3,input:2,output:3,cacheRead:4,cacheWrite:5,costUsd:0.2});assert.equal(codexTokens('tokens used\n12,345'),12345);assert.equal(codexTokens('unknown'),0);
});
test('計算題也交叉核對且可重算為空選項；否定題保留旗標',async()=>{
 const {runEnsemble}=require('../ensemble');let calls=0;
 const q={no:1,kind:'calc',answer:'A',detail:'計算',options:[]};
 const result=await runEnsemble({source:'text',text:'題目'}, {generate:async()=>({questions:[{...q,answer:++calls===1?'A':'B'}]}),chatgpt:async()=>({'1':'B'}),env:{}});
 assert.equal(calls,2);assert.equal(result.data.questions[0].answer,'B');assert.equal(result.ensemble.disputed[0].resolved,true);
 const negative=await runEnsemble({source:'text',text:'何者錯誤'}, {generate:async()=>({questions:[{no:1,negative:true,answer:'B',options}]}),chatgpt:async()=>({'1':'B'}),env:{}});assert.equal(negative.ensemble.agreed,1);assert.equal(checkAnswers(negative.data.questions).length,0);
});

test('真實 answer-key 預處理失敗不呼叫扣額度，文字與圖片成功各扣一次',async()=>{
 const {createAnswerKey}=require('../answer-key');let charges=0,calls=0;
 const deps={extractText:async()=>'',beforeAI:()=>charges++,runEnsemble:async()=>{calls++;return {data:{questions:[]},ensemble:{mode:'official',disputed:[]}};},checkAnswers:()=>[]};
 for(const input of [{filename:'bad.exe',dataBase64:'aA=='},{filename:'x.png',dataBase64:'bad'},{filename:'x.txt',dataBase64:'aA=='},{filename:'x.png',dataBase64:'aA==',officialAnswers:{x:'A'}}])await assert.rejects(()=>createAnswerKey(input,os.tmpdir(),deps));
 assert.equal(charges,0);assert.equal(calls,0);
 await createAnswerKey({filename:'x.png',dataBase64:'aA=='},os.tmpdir(),deps);
 await createAnswerKey({filename:'x.txt',dataBase64:'aA=='},os.tmpdir(),{...deps,extractText:async()=>'考卷'});
 assert.equal(charges,2);assert.equal(calls,2);
});
