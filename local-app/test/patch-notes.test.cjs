const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../public/patch-notes.js'), 'utf8');

test('詳解公告欄位完整、版本遞減、圖示為既有集合', () => {
  const { entries } = require('../public/answer-changelog.json');
  assert.ok(entries.length > 0);
  const icons = ['clock', 'gift', 'edit', 'spark', 'shield', 'image'];
  entries.forEach((entry, i) => {
    for (const key of ['version', 'date', 'tag', 'icon', 'title', 'body']) {
      assert.equal(typeof entry[key], 'string');
      assert.ok(entry[key].trim());
    }
    if(i)assert.ok(entries[i-1].version.split('.').map(Number).reduce((a,n)=>a*100+n,0) > entry.version.split('.').map(Number).reduce((a,n)=>a*100+n,0));
    assert.match(entry.date, /^2026 年 10 月 (15|16) 日$/);
    assert.ok(icons.includes(entry.icon));
    assert.ok(source.includes('    ' + entry.icon + ':'));
    assert.ok(!/\?{3,}/.test(JSON.stringify(entry)));
  });
});

function browser(lastRead) {
  const calls = [], reads = [], writes = [], dialogs = [], events = {};
  const storage = new Map(Object.entries(lastRead || {}));
  function element() {
    return { appendChild() {}, addEventListener() {}, setAttribute() {}, querySelector: element, remove() {},
      set textContent(value) { this.innerHTML = value; } };
  }
  const document = {
    readyState: 'loading', head: { appendChild() {} },
    body: { appendChild(el) { dialogs.push(el); } },
    getElementById() { return true; }, createElement: element,
    querySelector() { return null; }, removeEventListener() {},
    addEventListener(name, callback) { events[name] = callback; }
  };
  const window = { location: { search: '' },
    localStorage: { getItem(key) { reads.push(key); return storage.get(key); }, setItem(key, value) { writes.push([key, value]); } },
    setTimeout(fn) { fn(); }, setInterval() {} };
  vm.runInNewContext(source, { window, document, fetch: async url => {
    calls.push(url.split('?')[0]);
    return { ok: true, json: async () => url.startsWith('version.json') ? { version: '1' } : { entries: [{ version: '2', date: 'today' }] } };
  } });
  return { window, events, calls, reads, writes, dialogs };
}

test('預設仍讀取 OCR 公告及原本已讀 key，首次自動顯示', async () => {
  const b = browser();
  b.events.DOMContentLoaded();
  await new Promise(setImmediate);
  assert.deepEqual(b.calls, ['version.json', 'changelog.json']);
  assert.deepEqual(b.reads, ['tzutzu-patch-notes-last-read']);
  assert.deepEqual(b.writes, [['tzutzu-patch-notes-last-read', '2']]);
  assert.equal(b.dialogs.length, 1);
});

test('詳解設定隔離公告與已讀紀錄，已讀後不自動顯示', async () => {
  for (const read of [false, true]) {
    const b = browser(read ? { 'answer-read': '2' } : { 'tzutzu-patch-notes-last-read': '99' });
    b.window.TzutzuPatchNotes.mountButton({ appendChild() {} }, { url: 'answer-changelog.json', storageKey: 'answer-read', title: '詳解更新' });
    b.events.DOMContentLoaded();
    await new Promise(setImmediate);
    assert.deepEqual(b.calls, ['version.json', 'answer-changelog.json']);
    assert.deepEqual(b.reads, ['answer-read']);
    assert.equal(b.dialogs.length, read ? 0 : 1);
    if (!read) {
      assert.deepEqual(b.writes, [['answer-read', '2']]);
      assert.ok(b.dialogs[0].innerHTML.includes('<h2>詳解更新</h2>'));
    }
  }
});
