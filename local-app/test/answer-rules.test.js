const {test}=require('node:test');
const assert=require('node:assert/strict');
const {checkAnswers}=require('../../code/render');
const rules=require('../public/answer-rules');
for(const [name,answer,correct,expected] of [
 ['單選正確','A','A',false],['單選矛盾','B','A',true],['多選正確','CE','CE',false],
 ['多選矛盾','ACE','CE',true],['順序不同','EC','CE',false],['重複字母','CCE','EC',false],
 ['非選擇題','42','A',false],['需人工確認','需人工確認 A','A',false],['需要人工確認','需要人工確認','',false]
])test(name,()=>{const q={no:7,answer,options:[...'ACE'].map(label=>({label,text:correct.includes(label)?' 正確：理由':'錯誤：含正確'}))};assert.equal(rules.isMismatch(q),expected);assert.deepEqual(checkAnswers([q]),expected?[7]:[]);});
test('需要人工確認',()=>assert.deepEqual(checkAnswers([{no:1,answer:'A'},{no:2,answer:'CE',options:[]}]),[]));
test('人工確認另行標記',()=>{assert.equal(rules.needsReview({answer:'需人工確認'}),true);assert.equal(rules.needsReview({answer:'42'}),false);});
test('官方答案解析',()=>{assert.deepEqual(rules.parseOfficial('A D B CE ACE'),['A','D','B','CE','ACE']);assert.deepEqual(rules.parseOfficial('a,d，ce'),['A','D','CE']);assert.deepEqual(rules.parseOfficial('ADB'),['A','D','B']);assert.equal(rules.sameAnswers('EC','CE'),true);assert.equal(rules.sameAnswers('ACE','CE'),false);assert.throws(()=>rules.parseOfficial('A 3'));});
