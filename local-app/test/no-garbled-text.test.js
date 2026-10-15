// Guards against Chinese text being written out as "???" by tools with the wrong encoding.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const roots = [path.join(__dirname, '..', 'public'), path.join(__dirname, '..')];

test('前端與後端原始碼沒有被編碼弄壞的連續問號', () => {
  const bad = [];
  for (const dir of roots) {
    for (const name of fs.readdirSync(dir)) {
      if (!/\.(html|js|cjs)$/.test(name)) continue;
      const text = fs.readFileSync(path.join(dir, name), 'utf8');
      if (/\?{3,}/.test(text)) bad.push(path.relative(path.join(__dirname, '..'), path.join(dir, name)));
    }
  }
  assert.deepStrictEqual(bad, []);
});
