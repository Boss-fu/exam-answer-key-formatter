const {test}=require('node:test');
const assert=require('node:assert/strict');
const {runEnsemble}=require('../ensemble');
const question=(answer='A',no=1,options=[{label:'A',text:'正確：理由'}])=>({no,answer,detail:'計算說明',options});
const input={source:'text',text:'考卷'};
function deps(overrides={}){return {env:{},generate:async()=>({questions:[question()]}),chatgpt:async()=>({'1':'A'}),gemini:()=>assert.fail('Gemini 預設關閉'),...overrides};}
test('官方答案物件及字串只呼叫 Claude',async()=>{
 for(const officialAnswers of [{'1':'CE'},'CE A']){
 let calls=0;const r=await runEnsemble({...input,officialAnswers},deps({generate:async(text,extra)=>{calls++;assert.match(extra,/以下為官方答案/);assert.match(extra,/CE/);return {questions:[question('CE')]};},chatgpt:()=>assert.fail('不可驗證')}));
 assert.equal(calls,1);assert.equal(r.ensemble.mode,'official');assert.equal(r.ensemble.engines.length,1);
 }
});
test('全一致與集合順序、Gemini 預設關閉',async()=>{
 const r=await runEnsemble(input,deps({generate:async()=>({questions:[question('CE')]}),chatgpt:async()=>({'1':'EC'})}));assert.equal(r.ensemble.agreed,1);assert.equal(r.ensemble.engines.length,2);assert.deepEqual(r.ensemble.disputed,[]);
});
test('2:1 重算後解決，只要求爭議題',async()=>{
 let count=0;const r=await runEnsemble(input,deps({env:{ENSEMBLE_GEMINI:'1'},generate:async(text,extra)=>{count++;if(count===2){assert.match(extra,/只重寫第 1 題/);return {questions:[question('B')]};}return {questions:[question(),question('C',2)]};},chatgpt:async()=>({'1':'B','2':'C'}),gemini:async()=>({'1':'B','2':'C'})}));
 assert.equal(count,2);assert.equal(r.data.questions[0].answer,'B');assert.equal(r.data.questions[1].answer,'C');assert.equal(r.ensemble.disputed[0].resolved,true);
});
test('重算後仍不一致保留 Claude 並加 review（2 票與 3 票）',async()=>{
 for(const env of [{},{ENSEMBLE_GEMINI:'1'}]){
 const r=await runEnsemble(input,deps({env,chatgpt:async()=>({'1':'B'}),gemini:async()=>({'1':'B'})}));assert.equal(r.data.questions[0].answer,'A');assert.match(r.data.questions[0].review,/Claude A／ChatGPT B/);assert.equal(r.ensemble.disputed[0].resolved,false);
 }
});
test('驗證者失敗及逾時略過，不把單一主筆算成交叉一致',async()=>{
 for(const chatgpt of [async()=>{throw new Error('失敗');},()=>new Promise(()=>{})]){
 const r=await runEnsemble(input,deps({chatgpt,timeoutMs:10}));assert.equal(r.ensemble.engines[1].ok,false);assert.ok(r.ensemble.engines[1].error);assert.equal(r.data.questions.length,1);assert.equal(r.ensemble.agreed,0);
 }
});
test('非選擇題略過',async()=>{
 const r=await runEnsemble(input,deps({generate:async()=>({questions:[question('42',1,[])]}),chatgpt:async()=>({'1':'非選'})}));assert.equal(r.ensemble.agreed,0);assert.deepEqual(r.ensemble.disputed,[]);
});
test('圖片官方模式與爭議重算保留原圖片',async()=>{
 const images=[{filename:'page.png',dataBase64:'YQ=='}];let calls=0;
 const r=await runEnsemble({source:'images',images},deps({generate:()=>assert.fail('不可文字呼叫'),generateFromImages:async(img,extra)=>{assert.equal(img,images);calls++;return {questions:[question(calls===1?'A':'B')]};},chatgpt:async()=>({'1':'B'})}));assert.equal(calls,2);assert.equal(r.ensemble.disputed[0].resolved,true);
});
test('重算缺題或失敗仍保留原題並標示',async()=>{
 let count=0;const r=await runEnsemble(input,deps({generate:async()=>{if(++count===2)throw new Error('重算失敗');return {questions:[question()]};},chatgpt:async()=>({'1':'B'})}));assert.match(r.data.questions[0].review,/AI 意見不一致/);assert.equal(r.ensemble.disputed[0].resolved,false);
});
test('buildPrompt 不傳附加指示保持原樣，傳入則置於尾端',()=>{
 const {buildPrompt}=require('../engine');assert.equal(buildPrompt('題目'),buildPrompt('題目',undefined));assert.ok(buildPrompt('題目','官方指示').endsWith('題目\n\n官方指示'));
});
test('answer-key 分流將官方答案交給 ensemble 並回傳報告',async()=>{
 const {createAnswerKey}=require('../answer-key');const ensemble={mode:'official',engines:[{name:'claude',ok:true}],agreed:0,disputed:[]};
 const r=await createAnswerKey({filename:'exam.txt',dataBase64:'YQ==',officialAnswers:{'1':'A'}},'',{extractText:async()=>'考卷',runEnsemble:async input=>{assert.equal(input.source,'text');assert.deepEqual(input.officialAnswers,{'1':'A'});return {data:{questions:[question()]},ensemble};},checkAnswers:()=>[]});assert.equal(r.ensemble,ensemble);
});
