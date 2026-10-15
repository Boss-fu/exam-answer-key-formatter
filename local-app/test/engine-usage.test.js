const {test}=require('node:test'),assert=require('node:assert/strict'),{EventEmitter}=require('events'),fs=require('fs');
const childProcess=require('child_process');let invocations=[],reply,tokenLog='';
childProcess.spawn=(bin,args)=>{invocations.push(args);const child=new EventEmitter();child.stdout=new EventEmitter();child.stderr=new EventEmitter();child.stdin={on(){},end(){}};child.kill=()=>{};process.nextTick(()=>{if(args.includes('-o'))fs.writeFileSync(args[args.indexOf('-o')+1],'{"1":"B"}');child.stdout.emit('data',reply);child.stderr.emit('data',tokenLog);child.emit('close',0);});return child;};
const engine=require('../engine'),{runEnsemble,runValidator}=require('../ensemble');
const {emptyUsage,addUsage}=require('../usage');
test('answer-key Claude 文字、圖片、重算皆使用 JSON telemetry；舊路線保持文字',async()=>{
 const total=emptyUsage(),onUsage=u=>addUsage(total,u),q={no:1,kind:'calc',answer:'B',detail:'計算',options:[]};
 reply=JSON.stringify({result:JSON.stringify({questions:[q]}),usage:{input_tokens:10,output_tokens:2,cache_read_input_tokens:3,cache_creation_input_tokens:4},total_cost_usd:0.1});
 const image={filename:'exam.png',dataBase64:'aGVsbG8='};
 assert.equal((await engine.generateWithClaudeCode('題目',undefined,{onUsage})).questions[0].answer,'B');
 assert.equal((await engine.generateFromImagesClaudeCode([image],undefined,{onUsage})).questions[0].kind,'calc');
 await runEnsemble({source:'text',text:'題目',onUsage},{chatgpt:async()=>({'1':'A'}),env:{}});
 assert.equal(invocations.length,4);assert.ok(invocations.every(a=>a.includes('--output-format') && a.includes('json')));
 assert.equal(total.claude.calls,4);assert.equal(total.claude.input,40);assert.equal(total.claude.cacheRead,12);assert.equal(total.claude.costUsd,0.4);
 reply=JSON.stringify({questions:[q]});await engine.generateWithClaudeCode('舊功能');assert.ok(!invocations.at(-1).includes('--output-format'));
 reply=JSON.stringify({result:JSON.stringify({questions:[q]})});await engine.generateFromImagesClaudeCode([image],undefined,{onUsage});assert.equal(total.claude.calls,5);
});
test('Codex validator tokens used 由 stderr 讀取',async()=>{
 reply='';tokenLog='tokens used\n12,345';const total=emptyUsage();const answers=await runValidator('chatgpt',{source:'text',text:'考卷',onUsage:u=>addUsage(total,u)},{});assert.deepEqual(answers,{'1':'B'});assert.equal(total.chatgpt.tokens,12345);
});
test('Claude 失敗時等驗證者結束，讓 quota 鎖及用量生命週期一致',async()=>{
 let finished=false;
 await assert.rejects(()=>runEnsemble({source:'text',text:'考卷'},{generate:async()=>{throw Error('Claude failed');},chatgpt:async()=>{await new Promise(r=>setTimeout(r,15));finished=true;return {'1':'A'};},env:{}}),/Claude failed/);
 assert.equal(finished,true);
});
