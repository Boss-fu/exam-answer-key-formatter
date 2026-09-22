const {
  Table, TableRow, TableCell, Paragraph, TextRun,
  WidthType, VerticalAlign, VerticalMergeType, BorderStyle, TableLayoutType,
} = require("docx");

const CM = 566.9291339;
const dxa = (cm) => Math.round(cm * CM);

// 欄寬：題號/標籤/字母欄加寬過（原本 0.5/1.75/0.6cm 用 LibreOffice 預設字型量測沒問題，
// 但使用者實際 Word 用真正的華康細圓體(P)/Times New Roman 渲染時，"【答案】"「【詳解】」與
// "(A)"~"(D)" 都會被擠到換行。加寬到 0.7/2.2/0.9cm，內容欄 13.05cm 維持不變（使用者明確定案的欄寬）。
const COL_NUM = dxa(0.7);
const COL_LABEL = dxa(2.2);
const COL_LETTER = dxa(0.9);
// 內容欄 = 版面可用寬度(A4寬21cm - 左右各1.5cm邊界 = 18cm) 扣掉前三欄，讓整個表格跟頁面版心齊寬
const COL_CONTENT = dxa(18 - 0.7 - 2.2 - 0.9); // 14.2cm
const COL_MERGED = COL_LETTER + COL_CONTENT; // for 答案/詳解 rows (gridSpan=2)

// 字體（使用者最新指示，取代先前的華康細圓體(P)/華康細圓體）：中文用 DFPYuanMedium-B5，西文用 Times New Roman。
const FONT = {
  ascii: "Times New Roman",
  hAnsi: "Times New Roman",
  eastAsia: "DFPYuanMedium-B5",
  cs: "DFPYuanMedium-B5",
};
// 對應使用者真實 Word 範本（Paragraph 對話框截圖確認）：12pt 字體、行高 Exactly 12pt（不是1.5倍行高／multiple）、1.5cm 頁面邊界。
const FONT_SIZE = 24; // half-points => 12pt
const LINE_SPACING = 240; // dxa in twentieths of a point: 240 = 12pt
const LINE_RULE = "exact"; // "exact" = 固定行高剛好等於這個值，不是倍數
const PAGE_MARGIN = dxa(1.5); // 1.5cm margins on all sides

// 表格格線：無色無填滿（使用者最新指示，取代先前「黑色實線框線」定案）——邊框樣式設為 NONE，儲存格也不設底色。
// 注意：只設定儲存格層級的 borders 在某些真實 Word 版本會不夠——Word 預設的表格樣式（Table Grid）
// 本身帶有框線，若沒有在「表格層級」也明確覆寫成 NONE，開啟時仍可能顯示黑色細框線（即使 LibreOffice
// 預覽時看起來已經是無框線）。因此表格與儲存格都要設，且不要用 color: "auto"（在部分 Word 版本會被
// 解讀成黑色），改用 "FFFFFF"（白色，反正 style 是 NONE 不會印出來，純粹避免 auto 被誤判）。
const border = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const cellBorders = { top: border, bottom: border, left: border, right: border };
const tableBorders = {
  top: border, bottom: border, left: border, right: border,
  insideHorizontal: border, insideVertical: border,
};

function run(text, { bold } = {}) {
  return new TextRun({ text, bold: !!bold, font: FONT, size: FONT_SIZE });
}

function cell(text, { width, merge, columnSpan, bold, keepNext, tight, center, hMargin } = {}) {
  // hMargin overrides left/right margin (dxa). "tight" is a legacy alias for hMargin:10.
  const lr = hMargin !== undefined ? hMargin : (tight ? 10 : 60);
  const margins = { top: 40, bottom: 40, left: lr, right: lr };
  return new TableCell({
    width: { size: width, type: WidthType.DXA },
    columnSpan,
    borders: cellBorders,
    verticalAlign: VerticalAlign.TOP,
    verticalMerge: merge,
    margins,
    children: text === null ? [new Paragraph({ keepNext: !!keepNext, spacing: { line: LINE_SPACING, lineRule: LINE_RULE }, children: [] })] : [
      new Paragraph({
        keepNext: !!keepNext,
        keepLines: true,
        alignment: center ? "center" : undefined,
        spacing: { line: LINE_SPACING, lineRule: LINE_RULE },
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
      cell(String(q.no), { width: COL_NUM, merge: VerticalMergeType.RESTART, bold: true, keepNext: true, hMargin: 10, center: true }),
      cell("【答案】", { width: COL_LABEL, bold: true, keepNext: true, hMargin: 8 }),
      cell(q.answer, { width: COL_MERGED, columnSpan: 2, bold: true, keepNext: true }),
    ],
  }));

  // Row 2: (merged) | 【詳解】 | detail (spans col3+col4)
  rows.push(new TableRow({
    cantSplit: true,
    children: [
      cell(null, { width: COL_NUM, merge: VerticalMergeType.CONTINUE, keepNext: true }),
      cell("【詳解】", { width: COL_LABEL, bold: true, keepNext: true, hMargin: 8 }),
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
        cell(`(${opt.label})`, { width: COL_LETTER, keepNext: !isLast, hMargin: 0, center: true }),
        cell(opt.text, { width: COL_CONTENT, keepNext: !isLast }),
      ],
    }));
  });

  return new Table({
    layout: TableLayoutType.FIXED,
    width: { size: COL_NUM + COL_LABEL + COL_MERGED, type: WidthType.DXA },
    columnWidths: [COL_NUM, COL_LABEL, COL_LETTER, COL_CONTENT],
    borders: tableBorders,
    rows,
  });
}

function spacer() {
  return new Paragraph({ spacing: { before: 60, after: 60 }, children: [] });
}

module.exports = { buildQuestionTable, spacer, run, FONT, FONT_SIZE, LINE_SPACING, LINE_RULE, PAGE_MARGIN };
