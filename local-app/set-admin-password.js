'use strict';
const { createAuth } = require('./auth');
function hiddenInput(prompt) {
  return new Promise((resolve, reject) => {
    if (!process.stdin.isTTY) return reject(new Error('請在互動式終端機執行，以隱藏方式輸入密碼。'));
    process.stdout.write(prompt);
    let value = '';
    process.stdin.setRawMode(true); process.stdin.resume(); process.stdin.setEncoding('utf8');
    function finish(error) {
      process.stdin.removeListener('data', onData); process.stdin.setRawMode(false); process.stdin.pause(); process.stdout.write('\n');
      if (error) reject(error); else resolve(value);
    }
    function onData(chunk) {
      for (const char of chunk) {
        if (char === '\u0003') { finish(new Error('已取消設定。')); return; }
        if (char === '\r' || char === '\n') { finish(); return; }
        if (char === '\u007f' || char === '\b') value = Array.from(value).slice(0, -1).join('');
        else if (char >= ' ') value += char;
      }
    }
    process.stdin.on('data', onData);
  });
}
(async () => {
  const first = await hiddenInput('請輸入管理員密碼（至少 8 個字元）：');
  const second = await hiddenInput('請再次輸入密碼：');
  if (first !== second) throw new Error('兩次密碼不一致，未變更設定。');
  createAuth().setPassword(first); console.log('管理員密碼已設定。');
})().catch(error => { console.error(error.message); process.exitCode = 1; });
