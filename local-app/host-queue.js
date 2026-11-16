// 主機工作佇列：同時最多跑 slots 份（預設 1），其餘排隊（先進先出）。
// OCR／切圖很吃記憶體，預設一次只跑一份；主機電腦夠強時可用環境變數 HOST_CONCURRENCY 調高。
function createHostQueue(slots, onBusyChange) {
  const max = Math.max(1, parseInt(slots, 10) || 1);
  let active = 0, waiting = 0;
  const q = [];
  const notify = () => { try { onBusyChange && onBusyChange(waiting); } catch (e) {} };
  function run(work) {
    waiting += 1; notify();
    return new Promise((resolve, reject) => {
      const start = () => {
        active += 1;
        const finish = () => { active -= 1; waiting -= 1; notify(); const next = q.shift(); if (next) next(); };
        Promise.resolve().then(work).then((v) => { finish(); resolve(v); }, (e) => { finish(); reject(e); });
      };
      if (active < max) start(); else q.push(start);
    });
  }
  return { run, slots: max, isQueued: () => active >= max, stats: () => ({ active, waiting, queued: q.length, slots: max }) };
}
module.exports = { createHostQueue };
