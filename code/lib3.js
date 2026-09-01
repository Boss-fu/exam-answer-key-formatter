const {
  Table, TableRow, TableCell, Paragraph, TextRun,
  WidthType, VerticalAlign, VerticalMergeType, BorderStyle, TableLayoutType,
} = require("docx");

const CM = 566.9291339;
const dxa = (cm) => Math.round(cm * CM);

const COL_NUM = dxa(0.5);
const COL_LABEL = dxa(1.75);
const COL_LETTER = dxa(0.6);
const COL_CONTENT = dxa(13.05);
const COL_MERGED = COL_LETTER + COL_CONTENT; // 13.65cm, for 答案/詳解 rows

const FONT = {
  ascii: "Times New Roman",
  hAnsi: "Times New Roman",
  eastAsia: "華康細圓體(P)",
  cs: "華康細圓體",
};

const border = { style: BorderStyle.SINGLE, size: 4, color: "000000" };
const cellBorders = { top: border, bottom: border, left: border, right: border };

function run(text, { bold } = {}) {
  return new TextRun({ text, bold: !!bold, font: FONT });
}

function cell(text, { width, merge, columnSpan, bold, keepNext, tight, center } = {}) {
  const margins = tight
    ? { top: 60, bottom: 60, left: 10, right: 10 }
    : { top: 60, bottom: 60, left: 60, right: 60 };
  return new TableCell({
    width: { size: width, type: WidthType.DXA },
    columnSpan,
    borders: cellBorders,
    verticalAlign: VerticalAlign.TOP,
    verticalMerge: merge,
    margins,
    children: text === null ? [new Paragraph({ keepNext: !!keepNext, children: [] })] : [
      new Paragraph({
        keepNext: !!keepNext,
        keepLines: true,
        alignment: center ? "center" : undefined,
        children: [run(text, { bold })],
      }),
    ],
  });
}

function buildQuestionTable(q) {
  const rows = [];

  // Row 1: 題號(merge start) | 【答案】 | A  (last cell spans col3+col4)
  rows.push(new TableRow({
    cantSplit: true,
    children: [
      cell(String(q.no), { width: COL_NUM, merge: VerticalMergeType.RESTART, bold: true, keepNext: true }),
      cell("【答案】", { width: COL_LABEL, bold: true, keepNext: true }),
      cell(q.answer, { width: COL_MERGED, columnSpan: 2, bold: true, keepNext: true }),
    ],
  }));

  // Row 2: (merged) | 【詳解】 | detail (spans col3+col4)
  rows.push(new TableRow({
    cantSplit: true,
    children: [
      cell(null, { width: COL_NUM, merge: VerticalMergeType.CONTINUE, keepNext: true }),
      cell("【詳解】", { width: COL_LABEL, bold: true, keepNext: true }),
      cell(q.detail, { width: COL_MERGED, columnSpan: 2, keepNext: true }),
    ],
  }));

  // Option rows: (merged) | blank label | (A) | content
  q.options.forEach((opt, i) => {
    const isLast = i === q.options.length - 1;
    rows.push(new TableRow({
      cantSplit: true,
      children: [
        cell(null, { width: COL_NUM, merge: VerticalMergeType.CONTINUE, keepNext: !isLast }),
        cell(null, { width: COL_LABEL, keepNext: !isLast }),
        cell(`(${opt.label})`, { width: COL_LETTER, keepNext: !isLast, tight: true, center: true }),
        cell(opt.text, { width: COL_CONTENT, keepNext: !isLast }),
      ],
    }));
  });

  return new Table({
    layout: TableLayoutType.FIXED,
    width: { size: COL_NUM + COL_LABEL + COL_MERGED, type: WidthType.DXA },
    columnWidths: [COL_NUM, COL_LABEL, COL_LETTER, COL_CONTENT],
    rows,
  });
}

function spacer() {
  return new Paragraph({ spacing: { before: 60, after: 60 }, children: [] });
}

module.exports = { buildQuestionTable, spacer, run, FONT };
