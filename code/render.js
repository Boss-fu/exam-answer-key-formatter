// 可重用的產檔模組（供本機網頁應用 local-app 與未來 qbank 整合使用）。
// 不改動 build_from_json.js（那是 bundle 定案的 CLI），這裡把同樣的邏輯抽成函式輸出。
const { Document, Packer, Paragraph, TextRun } = require("docx");
const {
  buildQuestionTable, spacer, run, FONT, FONT_SIZE, LINE_SPACING, LINE_RULE, PAGE_MARGIN,
} = require("./lib3");

// 極簡 CSV 解析（支援雙引號欄位內的換行、逗號、"" 轉義）——不外加相依套件。
function parseCsv(text) {
  const rows = []; let row = [], field = "", q = false;
  text = String(text).replace(/^﻿/, "").replace(/\r\n?/g, "\n");
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else q = false; }
      else field += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n") { row.push(field); rows.push(row); row = []; field = ""; }
    else field += c;
  }
  if (field.length || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.some((c) => c.trim() !== ""));
}

// 由題庫 23 欄 CSV 直接生「乾淨考卷 Word」（docx-js，純 Node，不需 Python）。
// 題目欄的 $LaTeX$ 原樣保留供 MathType；〔含圖〕留為佔位。
async function buildCleanExamBuffer(csvText) {
  const rows = parseCsv(csvText);
  if (!rows.length) throw new Error("CSV 是空的");
  const head = rows[0].map((h) => h.trim());
  const idx = (n) => head.indexOf(n);
  const iNo = idx("題號"), iQ = idx("題目"), iPassage = idx("題組文章");
  const data = rows.slice(1).filter((r) => /^\d+$/.test((r[iNo] || "").trim()))
    .sort((a, b) => Number(a[iNo]) - Number(b[iNo]));
  const sp = { line: LINE_SPACING, lineRule: LINE_RULE };
  const children = [new Paragraph({
    spacing: { after: 160, ...sp },
    children: [run(`單選題【共 ${data.length} 題】`, { bold: true })],
  })];
  const lineRuns = (txt, firstPrefix) => String(txt).split("\n").map((ln, i) =>
    new TextRun({ text: (i === 0 ? (firstPrefix || "") + ln : ln), break: i === 0 ? 0 : 1, font: FONT, size: FONT_SIZE }));
  data.forEach((r) => {
    if (iPassage >= 0 && (r[iPassage] || "").trim()) {
      children.push(new Paragraph({ spacing: { before: 108, after: 36, ...sp }, indent: { left: 360 },
        children: lineRuns("◎ " + r[iPassage].trim()) }));
    }
    children.push(new Paragraph({ spacing: { before: 72, after: 72, ...sp },
      children: lineRuns(r[iQ] || "", (r[iNo] || "") + ". ") }));
  });
  const doc = new Document({ sections: [{
    properties: { page: { size: { width: 11906, height: 16838 },
      margin: { top: PAGE_MARGIN, bottom: PAGE_MARGIN, left: PAGE_MARGIN, right: PAGE_MARGIN, gutter: 0 } } },
    children }] });
  return Packer.toBuffer(doc);
}

// 答案一致性自檢：每題「以正確開頭」的選項 label 必須等於 answer。
// 回傳不一致的題號陣列（空陣列代表通過）。
const { isMismatch } = require('../local-app/public/answer-rules');
function checkAnswers(questions) {
  return (questions || []).filter(isMismatch).map(q => q.no);
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

module.exports = { checkAnswers, buildSummaryLine, buildDocxBuffer, buildCleanExamBuffer, parseCsv };
