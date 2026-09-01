// Usage: node build_from_json.js <input.json> <output.docx>
// input.json shape:
// {
//   "summaryLabel": "【單元/作業名稱】頁碼範圍",   // or "需人工確認" if unknown
//   "questions": [
//     { "no": 1, "answer": "A", "detail": "...", "options": [{"label":"A","text":"..."}, ...] },
//     ...
//   ]
// }
const fs = require("fs");
const { Document, Packer, Paragraph } = require("docx");
const { buildQuestionTable, spacer, run } = require("./lib3");

const [, , inputPath, outputPath] = process.argv;
if (!inputPath || !outputPath) {
  console.error("Usage: node build_from_json.js <input.json> <output.docx>");
  process.exit(1);
}

const data = JSON.parse(fs.readFileSync(inputPath, "utf8"));
const questions = data.questions;

// answer-consistency self-check before rendering
let mismatches = [];
questions.forEach((q) => {
  const correctOpt = q.options.find((o) => /正確/.test(o.text));
  if (!correctOpt || correctOpt.label !== q.answer) {
    mismatches.push(q.no);
  }
});
if (mismatches.length) {
  console.error("答案不一致，題號：" + mismatches.join(", "));
  process.exit(2);
}

const answerString = questions.map((q) => q.answer).join("");
const groups = [];
for (let i = 0; i < answerString.length; i += 5) groups.push(answerString.slice(i, i + 5));
const summaryLine = `${data.summaryLabel || "需人工確認"} ${groups.join("/")}`;

const children = [];
children.push(new Paragraph({ spacing: { after: 200 }, children: [run(summaryLine, { bold: true })] }));
questions.forEach((q, i) => {
  children.push(buildQuestionTable(q));
  if (i < questions.length - 1) children.push(spacer());
});

const doc = new Document({
  sections: [{ properties: { page: { size: { width: 11906, height: 16838 } } }, children }],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(outputPath, buf);
  console.log("written " + outputPath);
});
