// Older iPhones (Safari < 16) lack some newer APIs; calling them throws and the page shows "cannot connect".
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

test('answer.html 不直接呼叫舊版 Safari 不支援的 AbortSignal.timeout', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'answer.html'), 'utf8');
  const direct = html.match(/AbortSignal\.timeout\(\d/g) || [];
  assert.strictEqual(direct.length, 0, '請改用 timeoutSignal(ms)');
  assert.match(html, /function timeoutSignal\(ms\)/);
});
