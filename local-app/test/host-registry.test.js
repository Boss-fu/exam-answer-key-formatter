const assert = require("assert");
const { hostId, buildRow, upsert, ID_BASE } = require("../host-registry");
(async () => {
  assert.strictEqual(hostId("a.tail1.ts.net"), hostId("A.TAIL1.ts.net"));            // 大小寫無關、同一台永遠同一列
  assert.notStrictEqual(hostId("a.tail1.ts.net"), hostId("b.tail1.ts.net"));
  const id = hostId("jiantan.tail1.ts.net"); assert.ok(id >= ID_BASE && id < 2147483647);
  const row = buildRow("jiantan.tail1.ts.net", "劍潭", { revision: "abc" });
  const o = JSON.parse(row.content);
  assert.strictEqual(o.url, "https://jiantan.tail1.ts.net:8443"); assert.strictEqual(o.aiUrl, "https://jiantan.tail1.ts.net:8790");
  assert.strictEqual(o.name, "劍潭"); assert.strictEqual(o.online, true); assert.strictEqual(o.kind, "ocr-host");
  let seen; await upsert(row, async (u, init) => { seen = { u, init }; return { ok: true, status: 201 }; });
  assert.ok(/on_conflict=id$/.test(seen.u)); assert.strictEqual(seen.init.method, "POST");
  assert.ok(/merge-duplicates/.test(seen.init.headers.Prefer));
  await assert.rejects(upsert(row, async () => ({ ok: false, status: 403 })), /403/);
  console.log("host-registry: OK");
})().catch((e) => { console.error(e); process.exit(1); });
