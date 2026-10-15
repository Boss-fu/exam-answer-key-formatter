const fs = require('fs');
const path = require('path');
const { tokenTotals } = require('./usage');

const busyError = () => Object.assign(new Error('目前已有一份考卷在處理中，請幾分鐘後再試'), { statusCode: 429 });
const limitError = limit => Object.assign(new Error(`今天的 ${limit} 份額度已用完，明天再來；急用請聯絡管理員`), { statusCode: 429 });

// One server process owns the in-flight lease. Used counts and records survive restarts.
function createQuota({ dir = path.join(__dirname, 'output', 'usage'), limit = process.env.ANSWER_KEY_DAILY_LIMIT ?? 10, now = () => new Date() } = {}) {
  limit = Number(limit);
  if (!Number.isInteger(limit) || limit < 0) throw new Error('ANSWER_KEY_DAILY_LIMIT 必須為非負整數');
  let busy = false;
  const date = () => new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(now());
  const file = day => path.join(dir, day + '.json');

  function read(day) {
    try { return JSON.parse(fs.readFileSync(file(day), 'utf8')); }
    catch (error) {
      if (error.code === 'ENOENT') return { date: day, used: 0, records: [] };
      throw error; // Never reset a damaged usage file and silently grant extra quota.
    }
  }
  function save(data) {
    fs.mkdirSync(dir, { recursive: true });
    const temp = file(data.date) + '.tmp';
    fs.writeFileSync(temp, JSON.stringify(data, null, 2));
    fs.renameSync(temp, file(data.date));
  }
  function status(identity) {
    const day = date(), data = read(day);
    const unlimited = identity?.role === 'admin';
    return { limit: unlimited ? null : limit, used: data.used, remaining: unlimited ? null : Math.max(0, limit - data.used), unlimited, busy, date: day, tokens: tokenTotals(data.records) };
  }
  function acquire(identity) {
    const unlimited = identity?.role === 'admin';
    if (busy) throw busyError();
    if (!unlimited && status().remaining === 0) throw limitError(limit);
    busy = true;
    let chargedDate = null, released = false;
    return {
      // Reserve concurrency first; consume quota only after preprocessing succeeds.
      charge() {
        if (chargedDate) return;
        const day = date(), data = read(day);
        if (!unlimited && data.used >= limit) throw limitError(limit);
        if (!unlimited) data.used++;
        save(data);
        chargedDate = day;
      },
      record(record) {
        if (!chargedDate) return;
        const data = read(chargedDate);
        data.records.push({ ...record, quotaExempt: unlimited }); // Keep jobs that cross midnight with their original charge.
        save(data);
      },
      release() {
        if (!released) { busy = false; released = true; }
      },
      get charged() { return !!chargedDate; },
    };
  }
  return { status, acquire };
}
module.exports = { createQuota };
