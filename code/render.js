// 可重用的產檔模組（供本機網頁應用 local-app 與未來 qbank 整合使用）。
// 不改動 build_from_json.js（那是 bundle 定案的 CLI），這裡把同樣的邏輯抽成函式輸出。
const { Document, Packer, Paragraph } = require("docx");
const {
  buildQuestionTable, spacer, run, LINE_SPACING, LINE_RULE, PAGE_MARGIN,
} = require("./lib3");

// 答案一致性自檢：每題「以正確開頭」的選項 label 必須等於 answer。
// 回傳不一致的題號陣列（空陣列代表通過）。
function checkAnswers(questions) {
  const mismatches = [];
  (questions || []).forEach((q) => {
    const correctOpt = (q.options || []).find((o) => /^\s*正確/.test(o.text || ""));
    if (!correctOpt || correctOpt.label !== q.answer) mismatches.push(q.no);
  });
  return mismatches;
}

// 由題目答案組出簡答速查列（每 5 題一組，以 / 分隔）。
function buildSummaryLine(data) {
  const questions = data.questions || [];
  const answerString = questions.map((q) => q.answer).join("");
  const groups = [];
  for (let i = 0; i < answerString.length; i += 5) groups.push(answerString.slice(i, i + 5));
  return `${data.summaryLabel || "需人工確認"} ${groups.join("/")}`;
}

// data → docx Buffer。簡答速查列在最上方，接著逐題詳解表格（同一份文件）。
async function buildDocxBuffer(data) {
  const questions = data.questions || [];
  const children = [];
  children.push(new Paragraph({
    spacing: { after: 200, line: LINE_SPACING, lineRule: LINE_RULE },
    children: [run(buildSummaryLine(data), { bold: true })],
  }));
  questions.forEach((q, i) => {
    children.push(buildQuestionTable(q));
    if (i < questions.length - 1) children.push(spacer());
  });

  const doc = new Document({
    sections: [{
      properties: {
        page: {
          size: { width: 11906, height: 16838 }, // A4
          margin: { top: PAGE_MARGIN, bottom: PAGE_MARGIN, left: PAGE_MARGIN, right: PAGE_MARGIN, gutter: 0 },
        },
      },
      children,
    }],
  });
  return Packer.toBuffer(doc);
}

module.exports = { checkAnswers, buildSummaryLine, buildDocxBuffer };
