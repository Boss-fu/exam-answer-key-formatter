// Usage: node build_from_json.js <input.json> <output.docx>
// Supports both legacy {questions:[...]} and {sections:[{title, questions|items}]} input.
const fs = require('fs');
const { checkAnswers, buildDocxBuffer } = require('./render');

const [, , inputPath, outputPath] = process.argv;
if (!inputPath || !outputPath) {
  console.error('Usage: node build_from_json.js <input.json> <output.docx>');
  process.exit(1);
}

const data = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const mismatches = checkAnswers(data);
if (mismatches.length) {
  console.error('答案不一致，題號：' + mismatches.join(', '));
  process.exit(2);
}

buildDocxBuffer(data)
  .then((buffer) => {
    fs.writeFileSync(outputPath, buffer);
    console.log('written ' + outputPath);
  })
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
