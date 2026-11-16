const assert = require("assert");
const { createHostQueue } = require("../host-queue");
const { patchArgs } = require("../hide-windows");
(async () => {
  // 預設 1：一次只跑一個，先進先出
  let q = createHostQueue(1), order = [], running = 0, maxRun = 0;
  const job = (n, ms) => q.run(async () => { running++; maxRun = Math.max(maxRun, running); order.push("s" + n); await new Promise((r) => setTimeout(r, ms)); order.push("e" + n); running--; });
  await Promise.all([job(1, 40), job(2, 10), job(3, 10)]);
  assert.deepStrictEqual(order, ["s1", "e1", "s2", "e2", "s3", "e3"]); assert.strictEqual(maxRun, 1);
  // 2 個位置：最多同時 2 個，且 isQueued 在滿位時為 true
  q = createHostQueue(2); running = 0; maxRun = 0;
  const p = [job(1, 40), job(2, 40), job(3, 10)]; assert.strictEqual(q.isQueued(), true);
  await Promise.all(p); assert.strictEqual(maxRun, 2); assert.strictEqual(q.isQueued(), false);
  // 失敗的工作不會卡住佇列
  q = createHostQueue(1); await assert.rejects(q.run(async () => { throw new Error("x"); }));
  assert.strictEqual(await q.run(async () => 7), 7);
  // busy 回呼
  const seen = []; q = createHostQueue(1, (w) => seen.push(w)); await q.run(async () => 1); assert.deepStrictEqual(seen, [1, 0]);
  // 亂填的並行數退回 1
  assert.strictEqual(createHostQueue("abc").slots, 1); assert.strictEqual(createHostQueue(0).slots, 1);
  // windowsHide 參數補齊（各種呼叫形式）
  assert.deepStrictEqual(patchArgs(["a"]), ["a", { windowsHide: true }]);
  assert.deepStrictEqual(patchArgs(["a", ["x"]]), ["a", ["x"], { windowsHide: true }]);
  const cb = () => {}; assert.deepStrictEqual(patchArgs(["a", ["x"], cb]), ["a", ["x"], { windowsHide: true }, cb]);
  assert.deepStrictEqual(patchArgs(["a", cb]), ["a", { windowsHide: true }, cb]);
  assert.deepStrictEqual(patchArgs(["a", ["x"], { cwd: "c" }, cb]), ["a", ["x"], { windowsHide: true, cwd: "c" }, cb]);
  assert.deepStrictEqual(patchArgs(["a", ["x"], { windowsHide: false }]), ["a", ["x"], { windowsHide: false }]);
  console.log("host-queue-and-hide: OK");
})().catch((e) => { console.error(e); process.exit(1); });
