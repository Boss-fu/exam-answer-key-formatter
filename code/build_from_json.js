const fs=require('fs');
const {checkAnswers,buildDocxBuffer}=require('./render');
const [,,input,output]=process.argv;
if(!input||!output){console.error('Usage: node build_from_json.js <input.json> <output.docx>');process.exit(1);}
const data=JSON.parse(fs.readFileSync(input,'utf8')),bad=checkAnswers(data.questions);
if(bad.length){console.error('答案不一致，題號：'+bad.join(', '));process.exit(2);}
buildDocxBuffer(data).then(buf=>{fs.writeFileSync(output,buf);console.log('written '+output);}).catch(e=>{console.error(e);process.exitCode=1;});
