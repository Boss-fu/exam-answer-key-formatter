// Shared validation for Node and the browser.
(function(root){
'use strict';
function answerSet(value){return [...new Set(String(value || '').match(/[A-Z]/g) || [])].sort().join('');}
function needsReview(q){return /需(?:要)?人工確認/.test(String(q.answer || ''));}
function isMismatch(q){
 if(needsReview(q) || !q.options || !q.options.length || !answerSet(q.answer))return false;
 const correct=q.options.filter(o=>(q.negative ? /^\s*錯誤/ : /^\s*正確/).test(o.text || '')).map(o=>o.label).join('');
 return answerSet(correct)!==answerSet(q.answer);
}
function sameAnswers(a,b){return answerSet(a)===answerSet(b);}
function parseOfficial(value){
 const text=String(value || '').trim().toUpperCase();
 if(!text)return [];
 if(!/^[A-Z\s,，]+$/.test(text))throw new Error('官方答案請填英文字母，並以空白或逗號分隔。');
 return /[\s,，]/.test(text)?text.split(/[\s,，]+/).filter(Boolean):[...text];
}
const rules={answerSet,needsReview,isMismatch,sameAnswers,parseOfficial};
if(typeof module==='object' && module.exports)module.exports=rules;else root.AnswerRules=rules;
})(globalThis);
