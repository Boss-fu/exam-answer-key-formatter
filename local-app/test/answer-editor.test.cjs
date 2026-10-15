const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const source = fs.readFileSync(path.join(__dirname, '../public/answer.html'), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];

function browser() {
  const ids = new Map();
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.value = ''; this.textContent = ''; this.className = ''; this.style = {}; this.classList = { toggle() {}, add() {}, remove() {} }; }
    set id(value) { this._id = value; ids.set(value, this); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    addEventListener(name, callback) { this['on' + name] = callback; }
    setAttribute(name, value) { this[name] = value; }
    querySelectorAll(selector) {
      const parts = selector.split(' '), target = parts.at(-1);
      const matches = el => target.startsWith('.') ? el.className.split(' ').includes(target.slice(1)) : el.tag === target;
      const all = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
      return target === '*' ? all : all.filter(matches);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0]; }
  }
  const document = { getElementById(id) { if (!ids.has(id)) { const e = new Element('div'); e.id = id; } return ids.get(id); }, createElement: tag => new Element(tag) };
  const context = vm.createContext({ document, OcrServerConfig: { servers: { company: {} } }, AnswerRules: require('../public/answer-rules'), console, localStorage: { setItem() {} } });
  vm.runInContext(source.replace('restoreDraft();connect();', ''), context);
  return { context, document };
}

test('計算題編輯器只顯示答案與詳解，不建立選項欄', () => {
  const { context, document } = browser();
  vm.runInContext(`draft={official:[],data:{questions:[{no:1,kind:'calc',answer:'B',detail:'計算',options:[]}]}};showReview();`, context);
  const card = document.getElementById('q0');
  assert.equal(card.querySelectorAll('textarea').length, 1);
  assert.equal(card.querySelectorAll('.answers').length, 0);
  assert.equal(card.querySelectorAll('input').length, 1);
  const answer = card.querySelector('.answer-value');
  answer.value = 'ce'; answer.oninput();
  assert.equal(vm.runInContext('draft.data.questions[0].answer', context), 'CE');
});

test('否定題核取即時重算一致性；修改答案及選項同樣生效', () => {
  const { context, document } = browser();
  vm.runInContext(`draft={official:[],data:{questions:[{no:1,answer:'B',detail:'',options:[{label:'A',text:'正確：原因'},{label:'B',text:'錯誤：原因'}]}]}};showReview();`, context);
  const card = document.getElementById('q0');
  assert.equal(vm.runInContext('issues(draft.data.questions[0]).mismatch', context), true);
  const negative = card.querySelectorAll('input').find(input => input.type === 'checkbox');
  negative.checked = true; negative.onchange();
  assert.equal(vm.runInContext('draft.data.questions[0].negative', context), true);
  assert.equal(vm.runInContext('issues(draft.data.questions[0]).mismatch', context), false);
  card.querySelectorAll('button').find(button => button.textContent === 'A').onclick();
  assert.equal(card.querySelector('.answer-value').value, 'A');
  assert.equal(vm.runInContext('issues(draft.data.questions[0]).mismatch', context), true);
});
