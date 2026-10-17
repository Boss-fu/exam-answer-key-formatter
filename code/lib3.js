const {
  Table, TableRow, TableCell, Paragraph, TextRun,
  WidthType, VerticalMergeType, VerticalAlign, BorderStyle, TableLayoutType,
} = require('docx');

const FONT = { ascii: 'Times New Roman', hAnsi: 'Times New Roman', eastAsia: 'DFPYuanMedium-B5', cs: 'DFPYuanMedium-B5' };
const FONT_SIZE = 24, LINE_SPACING = 240, LINE_RULE = 'auto', PAGE_MARGIN = 850;
const WIDTHS = [397, 1247, 510, 8050];
const border = { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' };
const borders = { top: border, bottom: border, left: border, right: border, insideHorizontal: border, insideVertical: border };

function run(text, { bold = false } = {}) {
  return new TextRun({ text: String(text ?? ''), bold, font: FONT, size: FONT_SIZE });
}

// MathType imports CJK in TeX as bytes. Preserve such formulae as native Word
// runs, with common sub/superscripts and italic Latin variables.
const CJK_RE = /[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]/;
const LATEX_SYMBOLS = { times: '×', cdot: '·', le: '≤', leq: '≤', ge: '≥', geq: '≥', neq: '≠', pm: '±', to: '→' };

function nativeFormulaRuns(latex, { bold } = {}) {
  const runs = [];
  const add = (text, extra = {}) => {
    if (text) runs.push(new TextRun({ text, bold: !!bold, font: FONT, size: FONT_SIZE, ...extra }));
  };
  for (let i = 0; i < latex.length;) {
    const ch = latex[i];
    if (ch === '_' || ch === '^') {
      const extra = ch === '_' ? { subScript: true } : { superScript: true };
      i += 1;
      let value = '';
      if (latex[i] === '{') {
        const end = latex.indexOf('}', i + 1);
        value = latex.slice(i + 1, end < 0 ? latex.length : end);
        i = end < 0 ? latex.length : end + 1;
      } else value = latex[i++] || '';
      add(value.replace(/[{}]/g, ''), extra);
    } else if (ch === '\\') {
      const match = latex.slice(i + 1).match(/^[A-Za-z]+/);
      if (match) {
        const name = match[0];
        i += name.length + 1;
        add(LATEX_SYMBOLS[name] || name.replace(/^(mathrm|text|mathit)$/, ''));
      } else { add(ch); i += 1; }
    } else if (ch === '{' || ch === '}') i += 1;
    else { add(ch, /[A-Za-z]/.test(ch) ? { italics: true } : {}); i += 1; }
  }
  return runs;
}

function textRuns(text, { bold } = {}) {
  const value = String(text ?? '');
  const runs = [];
  const formula = /(\$\$?)([\s\S]*?)\1/g;
  let last = 0, match;
  while ((match = formula.exec(value))) {
    if (match.index > last) runs.push(run(value.slice(last, match.index), { bold }));
    if (CJK_RE.test(match[2])) runs.push(...nativeFormulaRuns(match[2], { bold }));
    else runs.push(run(match[0], { bold }));
    last = formula.lastIndex;
  }
  if (last < value.length || !runs.length) runs.push(run(value.slice(last), { bold }));
  return runs;
}

function cell(text, { width, merge, columnSpan, center = false, bold = false, keepNext = false, tight = false } = {}) {
  const horizontal = tight ? 10 : 60;
  return new TableCell({
    width: { size: width, type: WidthType.DXA }, verticalMerge: merge, columnSpan, borders,
    verticalAlign: VerticalAlign.TOP,
    margins: { top: 40, bottom: 40, left: horizontal, right: horizontal },
    children: [new Paragraph({
      keepNext, keepLines: true, contextualSpacing: false, alignment: center ? 'center' : undefined,
      spacing: { before: 0, after: 0, line: LINE_SPACING, lineRule: LINE_RULE },
      children: textRuns(text, { bold }),
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
        cell(i === 0 ? String(q.no) : '', { ...base, width: WIDTHS[0], merge: i === 0 ? VerticalMergeType.RESTART : VerticalMergeType.CONTINUE, center: true, bold: true, tight: true }),
        cell(line.label, { ...base, width: WIDTHS[1], center: true, bold: true, tight: true }),
        ...(line.option ? [
          cell(`(${line.option.label})`, { ...base, width: WIDTHS[2], center: true, tight: true }),
          cell(line.option.text, { ...base, width: WIDTHS[3] }),
        ] : [cell(line.text, { ...base, width: WIDTHS[2] + WIDTHS[3], columnSpan: 2, bold: i === 0 })]),
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

const buildQuestionTable = (question) => buildQuestionsTable([question]);

function spacer() {
  return new Paragraph({ spacing: { before: 60, after: 60 }, children: [] });
}

function sectionTitle(title) {
  return new Paragraph({
    spacing: { before: 180, after: 100, line: LINE_SPACING, lineRule: LINE_RULE },
    children: textRuns(title, { bold: true }),
  });
}

module.exports = { buildQuestionsTable, buildQuestionTable, spacer, run, sectionTitle, FONT, FONT_SIZE, LINE_SPACING, LINE_RULE, PAGE_MARGIN };
