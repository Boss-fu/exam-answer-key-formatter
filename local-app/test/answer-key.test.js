const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { routeAnswerKey, createAnswerKey } = require('../answer-key');

for (const [name, text, expected] of [
  ['exam.docx', '', 'text'], ['exam.txt', '題目', 'text'],
  ['exam.pdf', '字'.repeat(200), 'images'], ['exam.pdf', '', 'images'],
  ['exam.PDF', '字 '.repeat(199), 'images'], ['exam.png', '', 'images'],
  ['exam.jpg', '', 'images'], ['exam.JPEG', '', 'images'],
]) test(`分流 ${name} ${text.length} 字元`, () => assert.equal(routeAnswerKey(name, text), expected));

test('不支援的副檔名回中文 400', () => {
  for (const name of ['exam.doc', 'exam.exe', 'exam.webp', 'exam']) {
    assert.throws(() => routeAnswerKey(name), error => error.statusCode === 400 && /只接受/.test(error.message));
  }
});
test('掃描卷 12 頁可用，13 頁拒絕；文字卷不受限制', () => {
  assert.equal(routeAnswerKey('exam.pdf', '', 12), 'images');
  assert.throws(() => routeAnswerKey('exam.pdf', '', 13), error => error.statusCode === 400 && /分批/.test(error.message));
  assert.equal(routeAnswerKey('exam.pdf', '字'.repeat(200), 30), 'text');
});

const data = { questions: [{ no: 1, answer: 'A', options: [] }] };
function dependencies(overrides = {}) {
  return { extractText: async () => '題目', generate: async () => data,
    generateFromImages: async () => data, checkAnswers: () => [1], ...overrides };
}
test('文字／圖片流程使用既有引擎及答案檢查', async () => {
  const input = { filename: 'exam.txt', dataBase64: Buffer.from('題目').toString('base64') };
  const result = await createAnswerKey(input, '', dependencies({ generate: async text => { assert.equal(text, '題目'); return data; } }));
  assert.deepEqual(result, { ok: true, source: 'text', pages: null, data, mismatches: [1] });
  await createAnswerKey({ ...input, filename: 'exam.png' }, '', dependencies({
    extractText: () => assert.fail('圖片不可擷取文字'),
    generateFromImages: async images => { assert.equal(images[0].dataBase64, input.dataBase64); return data; }
  }));
});
test('掃描 PDF 排序、頁數限制，以及成功／失敗均清除暫存檔', async () => {
  const output = path.join(__dirname, '..', 'output');
  fs.mkdirSync(output, { recursive: true });
  for (const count of [2, 13]) {
    let directory;
    const promise = createAnswerKey({ filename: 'scan.pdf', dataBase64: 'YQ==' }, output, dependencies({
      extractText: async () => { throw new Error('這個 PDF 讀不到文字（可能是掃描成圖片的）。'); },
      execFile: async (bin, args) => {
        assert.deepEqual(args.slice(0, 7), ['-r', '200', '-png', '-f', '1', '-l', '13']);
        directory = path.dirname(args[7]);
        for (let n = count; n >= 1; n--) fs.writeFileSync(path.join(directory, `page-${n}.png`), 'image');
      },
      generateFromImages: async images => { assert.equal(images.length, 2); assert.equal(images[0].filename, 'page-1.png'); return data; }
    }));
    if (count === 13) await assert.rejects(promise, /超過 12 頁/);
    else assert.equal((await promise).pages, 2);
    assert.equal(fs.existsSync(directory), false);
  }
});
test('PDF 擷取工具失敗不誤判成掃描卷', async () => {
  await assert.rejects(createAnswerKey({ filename: 'exam.pdf', dataBase64: 'YQ==' }, '', dependencies({
    extractText: async () => { throw new Error('spawn ENOENT'); },
    generateFromImages: () => assert.fail('不可呼叫圖片引擎')
  })), /讀取考卷失敗/);
});

test('圖片引擎失敗後仍清除 PDF 暫存', async () => {
  let directory;
  await assert.rejects(createAnswerKey({ filename: 'exam.pdf', dataBase64: 'YQ==' }, path.join(__dirname, '..', 'output'), dependencies({
    extractText: async () => '',
    execFile: async (bin, args) => { directory = path.dirname(args[7]); fs.writeFileSync(path.join(directory, 'page-1.png'), 'image'); },
    generateFromImages: async () => { throw new Error('引擎失敗'); }
  })), /引擎失敗/);
  assert.equal(fs.existsSync(directory), false);
});

test('HTTP health、獨立頁面及中文輸入錯誤', async () => {
  const authModule=require('../auth'), authDir=fs.mkdtempSync(path.join(require('os').tmpdir(),'answer-http-auth-'));
  const auth=authModule.createAuth({dir:authDir});authModule.createAuth=()=>auth;const invite=auth.createInvite('測試老師'),token=auth.inviteLogin(invite.code,'test').token;
  const server = require('../server');
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const health = await (await fetch(base + '/api/health')).json();
    assert.ok(health.capabilities.includes('answer-key'));
    assert.match(await (await fetch(base + '/answer.html')).text(), /理化詳解產生器/);
    for (const body of ['{', '{}', JSON.stringify({ filename: 'exam.exe', dataBase64: 'YQ==' }), JSON.stringify({ filename: 'exam.txt', dataBase64: '?!' })]) {
      const response = await fetch(base + '/api/answer-key', { method: 'POST', headers: { 'content-type': 'application/json', authorization: 'Bearer '+token }, body });
      assert.equal(response.status, 400);
      const result = await response.json();
      assert.equal(result.ok, false);
      assert.match(result.error, /[\u4e00-\u9fff]/);
      assert.doesNotMatch(result.error, /stack|SyntaxError| at /);
    }
  } finally { await new Promise(resolve => server.close(resolve)); fs.rmSync(authDir,{recursive:true,force:true}); }
});

test('有文字 PDF 12 頁看圖，13 頁退回文字；200 字門檻去空白', async () => {
 const output=path.join(__dirname,'..','output');fs.mkdirSync(output,{recursive:true});
 for(const count of [12,13]){
  let directory;
  const result=await createAnswerKey({filename:'text.pdf',dataBase64:'YQ=='},output,dependencies({
   extractText:async()=>'字 '.repeat(200),
   execFile:async(bin,args)=>{directory=path.dirname(args[7]);for(let n=1;n<=count;n++)fs.writeFileSync(path.join(directory,'page-'+n+'.png'),'image');},
   generate:async text=>{assert.equal(count,13);assert.equal(text.replace(/\s/g,'').length,200);return data;},
   generateFromImages:async images=>{assert.equal(count,12);assert.equal(images.length,12);return data;}
  }));
  assert.equal(result.source,count===12?'images':'text');assert.equal(fs.existsSync(directory),false);
 }
 assert.throws(()=>routeAnswerKey('short.pdf','字 '.repeat(199),13),error=>error.statusCode===400);
});
