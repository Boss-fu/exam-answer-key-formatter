const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const engine = require('./engine');
const rules = require('./public/answer-rules');
const {codexTokens}=require('./usage');
const REPO = path.resolve(__dirname, '..');
const TIMEOUT = 360000;

function officialMap(value) {
  if (typeof value === 'string') return Object.fromEntries(rules.parseOfficial(value).map((a, i) => [i + 1, a]));
  if (!value) return {};
  if (typeof value !== 'object' || Array.isArray(value)) throw Object.assign(new Error('官方答案必須是題號對答案物件或字串。'), { statusCode: 400 });
  return Object.fromEntries(Object.entries(value).map(([no, a]) => {
    if (!/^\d+$/.test(no) || typeof a !== 'string' || !a.trim()) throw Object.assign(new Error('官方答案格式不正確。'), { statusCode: 400 });
    return [no, a.toUpperCase()];
  }));
}

// Each validator owns its temporary files and kills its process on timeout.
async function runValidator(name, input, env = process.env) {
  const dir = fs.mkdtempSync(path.join(REPO, '.ensemble-tmp-'));
  try {
    const images = input.source === 'images' ? (input.images || []).map((img, i) => {
      const file = path.join(dir, `page${i + 1}${path.extname(img.filename || '') || '.png'}`);
      fs.writeFileSync(file, Buffer.from(img.dataBase64, 'base64'));
      return file;
    }) : [];
    const prompt = '請獨立解答考卷，只輸出題號對答案的 JSON 物件，例如 {"1":"A","2":"CE","3":"非選"}。多選字母合併，非選擇題填「非選」，不要解說。\n' +
      (input.source === 'text' ? input.text : '考卷圖片：\n' + images.join('\n'));
    const output = path.join(dir, 'answers.json');
    let bin, args;
    if (name === 'chatgpt') {
      bin = env.CODEX_BIN || 'codex';
      // `-i` takes multiple values, so the stdin marker must come before it.
      args = ['exec', '-s', 'read-only', '--skip-git-repo-check', '-o', output, '-', ...images.flatMap(p => ['-i', p])];
    } else {
      bin = env.AGY_BIN || 'agy';
      const extra = JSON.parse(env.AGY_ARGS || '["--model","gemini-3.8-flash-high"]');
      if (!Array.isArray(extra) || extra.some(a => typeof a !== 'string')) throw new Error('AGY_ARGS 必須是字串陣列');
      args = [...extra, '-p', prompt, '--print-timeout', '300s'];
    }
    const raw = await new Promise((resolve, reject) => {
      const child = spawn(bin, args, { cwd: REPO, env, windowsHide: true });
      let out = '', err = '';
      const timer = setTimeout(() => { child.kill(); reject(new Error('驗證逾時（6 分鐘）')); }, TIMEOUT);
      child.stdout.on('data', d => { out += d; });
      child.stderr.on('data', d => { err += d; });
      child.on('error', e => { clearTimeout(timer); reject(e); });
      child.on('close', code => {
        clearTimeout(timer);
        input.onUsage?.({chatgpt:{tokens:name==='chatgpt'?codexTokens(out+'\n'+err):0}});
        if (code !== 0) return reject(new Error(`驗證失敗（${code}）：${err.slice(0, 300)}`));
        try { resolve(name === 'chatgpt' ? fs.readFileSync(output, 'utf8') : out); } catch (e) { reject(e); }
      });
      child.stdin.on('error', () => {});
      child.stdin.end(name === 'chatgpt' ? prompt : undefined);
    });
    return engine.extractJson(raw);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
}

async function runEnsemble(input, deps = {}) {
  const env = deps.env || process.env;
  const write = extra => input.source === 'images'
    ? (deps.generateFromImages || engine.generateFromImagesClaudeCode)(input.images, extra, {onUsage:input.onUsage})
    : (deps.generate || engine.generateWithClaudeCode)(input.text, extra, {onUsage:input.onUsage});
  let official;
  try { official = officialMap(input.officialAnswers); }
  catch (e) { e.statusCode = 400; throw e; }
  const report = { mode: Object.keys(official).length ? 'official' : 'vote', engines: [{ name: 'claude', ok: true }], agreed: 0, disputed: [] };
  if (report.mode === 'official') {
    const data = await write('以下為官方答案，請以官方答案為準撰寫詳解：' + JSON.stringify(official));
    return { data, ensemble: report };
  }
  const names = ['chatgpt', ...(env.ENSEMBLE_GEMINI === '1' ? ['gemini'] : [])];
  const [written, ...validators] = await Promise.all([Promise.resolve().then(() => write()).then(data => ({data}), error => ({error})), ...names.map(async name => {
    let timer;
    try {
      const answers = await Promise.race([
        Promise.resolve().then(() => (deps[name] || (i => runValidator(name, i, env)))(input)),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('驗證逾時（6 分鐘）')), deps.timeoutMs || TIMEOUT); })
      ]);
      if (!answers || typeof answers !== 'object' || Array.isArray(answers) || !Object.values(answers).some(a => typeof a === 'string')) throw new Error('驗證答案 JSON 格式不正確');
      return { name, ok: true, answers };
    } catch (e) { return { name, ok: false, error: String(e.message) }; }
    finally { clearTimeout(timer); }
  })]);
  // Wait for validators to settle even if Claude failed: their usage belongs to this job.
  if (written.error) throw written.error;
  const data = written.data;
  report.engines.push(...validators.map(({ name, ok, error }) => ({ name, ok, ...(error ? { error } : {}) })));
  if (!data || !Array.isArray(data.questions)) throw new Error('Claude 沒有回傳題目資料');
  for (const q of data.questions) {
    if (!rules.answerSet(q.answer) || rules.needsReview(q)) continue;
    const votes = { claude: q.answer };
    for (const v of validators) if (v.ok && typeof v.answers[q.no] === 'string' && /^[A-Z\s,，()]+$/i.test(v.answers[q.no]) && rules.answerSet(v.answers[q.no].toUpperCase())) votes[v.name] = v.answers[q.no].toUpperCase();
    if (Object.keys(votes).length < 2) continue;
    if (Object.values(votes).every(a => rules.sameAnswers(a, q.answer))) report.agreed++;
    else report.disputed.push({ no: q.no, votes, final: q.answer, resolved: false });
  }
  if (report.disputed.length) {
    let revised;
    try {
      revised = await write('只重寫第 ' + report.disputed.map(d => d.no).join('、') + ' 題，其他 AI 的答案分別是：' + JSON.stringify(report.disputed.map(d => ({ no: d.no, votes: d.votes }))) + '。請重新仔細計算，回傳同樣 JSON 結構但只含這些題。');
    } catch (e) { report.recalculationError = String(e.message); }
    for (const d of report.disputed) {
      const index = data.questions.findIndex(q => String(q.no) === String(d.no));
      const replacement = revised?.questions?.find(q => String(q.no) === String(d.no));
      const valid = replacement && rules.answerSet(replacement.answer) && Array.isArray(replacement.options) && (replacement.options.length || replacement.kind === 'calc') && typeof replacement.detail === 'string';
      d.recalculated=!!valid;
      if (valid) data.questions[index] = replacement;
      const q = data.questions[index];
      d.final = q.answer;
      const others = Object.entries(d.votes).filter(([n]) => n !== 'claude').map(([, a]) => a);
      d.resolved = !!valid && others.filter(a => rules.sameAnswers(a, q.answer)).length + 1 > (others.length + 1) / 2;
      if (!d.resolved) q.review = [q.review, 'AI 意見不一致：' + Object.entries({ ...d.votes, claude: q.answer }).map(([n, a]) => `${({ claude: 'Claude', chatgpt: 'ChatGPT', gemini: 'Gemini' })[n]} ${a}`).join('／')].filter(Boolean).join('；');
    }
  }
  return { data, ensemble: report };
}
module.exports = { runEnsemble, runValidator, officialMap };
