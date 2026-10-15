const {
  Table, TableRow, TableCell, Paragraph, TextRun,
  WidthType, VerticalMergeType, BorderStyle, TableLayoutType,
} = require('docx');

// Values inspected in ref_format.docx; the user-supplied template overrides older rules.
const FONT = { ascii: 'Times New Roman', hAnsi: 'Times New Roman', eastAsia: 'DFPYuanMedium-B5', cs: 'DFPYuanMedium-B5' };
const FONT_SIZE = 24, LINE_SPACING = 240, LINE_RULE = 'auto', PAGE_MARGIN = 850;
const WIDTHS = [397, 1247, 510, 8050];
const border = { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' };
const borders = { top: border, bottom: border, left: border, right: border, insideHorizontal: border, insideVertical: border };

function run(text, { bold = false } = {}) {
  return new TextRun({ text: String(text ?? ''), bold, font: FONT, size: FONT_SIZE });
}
function cell(text, width, { merge, columnSpan, center = false, bold = false, keepNext = false } = {}) {
  return new TableCell({
    width: { size: width, type: WidthType.DXA }, verticalMerge: merge, columnSpan, borders,
    margins: { top: 40, bottom: 40, left: 10, right: 10 },
    children: [new Paragraph({
      keepNext, keepLines: true, contextualSpacing: false, alignment: center ? 'center' : undefined,
      spacing: { before: 0, after: 0, line: LINE_SPACING, lineRule: LINE_RULE },
      children: [run(text, { bold })],
    })],
  });
}
function questionRows(q) {
  const options = q.kind === 'calc' ? [] : (q.options || []);
  const lines = [{ label: '【答案】', text: q.answer }];
  if (q.detail || !options.length) lines.push({ label: '【詳解】', text: q.detail });
  options.forEach((option, i) => lines.push({ label: !q.detail && i === 0 ? '【詳解】' : '', option }));
  return lines.map((line, i) => {
    const base = { keepNext: i < lines.length - 1 };
    return new TableRow({
      cantSplit: true,
      children: [
        cell(i === 0 ? String(q.no) : '', WIDTHS[0], {
          ...base, merge: i === 0 ? VerticalMergeType.RESTART : VerticalMergeType.CONTINUE, center: true, bold: true,
        }),
        cell(line.label, WIDTHS[1], { ...base, center: true, bold: true }),
        ...(line.option ? [
          cell('(' + line.option.label + ')', WIDTHS[2], { ...base, center: true }),
          cell(line.option.text, WIDTHS[3], base),
        ] : [cell(line.text, WIDTHS[2] + WIDTHS[3], { ...base, columnSpan: 2, bold: i === 0 })]),
      ],
    });
  });
}
function buildQuestionsTable(questions) {
  return new Table({
    layout: TableLayoutType.FIXED, width: { size: 10204, type: WidthType.DXA }, columnWidths: WIDTHS,
    borders, margins: { left: 10, right: 10 }, rows: questions.flatMap(questionRows),
  });
}
const buildQuestionTable = q => buildQuestionsTable([q]);
module.exports = { buildQuestionsTable, buildQuestionTable, run, FONT, FONT_SIZE, LINE_SPACING, LINE_RULE, PAGE_MARGIN };
