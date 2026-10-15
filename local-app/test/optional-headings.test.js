const { test } = require('node:test');
const assert = require('node:assert/strict');
const Zip = require('jszip');
const { buildDocxBuffer } = require('../../code/render');
const base = { summaryLabel: '原簡答標籤', questions: Array.from('DCBBDD', (answer, i) =>
  ({ no: i + 1, kind: 'calc', answer, detail: '說明' })) };
async function xml(data) {
  const zip = await Zip.loadAsync(await buildDocxBuffer(data));
  return zip.file('word/document.xml').async('string');
}
for (const title of [false, true]) for (const unit of [false, true]) {
  test(`選填標題=${title}、單元=${unit}：段落、框線、簡答與既有表格`, async () => {
    const original = await xml(base);
    const result = await xml({ ...base, docTitle: title ? '  講義標題  ' : '  ', unitName: unit ? '  3-1 基礎練習  ' : '' });
    const header = result.split('<w:body>')[1].split('<w:tbl>')[0];
    const paragraphs = header.match(/<w:p>[\s\S]*?<\/w:p>/g) || [];
    const texts = paragraphs.map(p => [...p.matchAll(/<w:t\b[^>]*>(.*?)<\/w:t>/g)].map(m => m[1]).join(''));
    assert.deepEqual(texts, [title && '講義標題', unit && '【 3-1 基礎練習 詳解 】', unit ? 'DCBBD/D' : '原簡答標籤 DCBBD/D'].filter(Boolean));
    assert.equal(header.includes('<w:pBdr>'), title);
    if (title) {
      assert.match(paragraphs[0], /<w:sz w:val="44"\/>/);
      assert.match(paragraphs[0], /華康細圓體/);
      for (const side of ['top', 'bottom', 'left', 'right']) assert.match(paragraphs[0], new RegExp(`<w:${side}[^>]*w:val="single"`));
    }
    if (unit) assert.match(paragraphs[title ? 1 : 0], /<w:sz w:val="28"\/>/);
    for (const p of paragraphs) {
      assert.match(p, /<w:snapToGrid w:val="0"\/>/);
      assert.match(p, /<w:adjustRightInd w:val="0"\/>/);
      assert.match(p, /w:line="240" w:lineRule="auto"/);
      assert.match(p, /<w:jc w:val="center"\/>/);
      assert.match(p, /<w:b\/>/);
    }
    assert.match(paragraphs.at(-1), /w:after="180"/);
    assert.equal(result.split('<w:tbl>')[1], original.split('<w:tbl>')[1]);
    if (!title && !unit) assert.equal(result, original);
  });
}
test('共用 renderer 限制標題與單元為 60 字，空白單元沿用 summaryLabel', async () => {
  const result = await xml({ ...base, docTitle: '  ' + '題'.repeat(70), unitName: '   ' });
  assert.ok(result.includes('題'.repeat(60)));
  assert.ok(!result.includes('題'.repeat(61)));
  assert.ok(result.includes('原簡答標籤 DCBBD/D'));
});
