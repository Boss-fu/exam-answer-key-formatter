// 主機自動「報到」：這台主機把自己的名稱與網址登錄到網站共用資料表（syllabus_doc，與 AI_CFG 等設定同一張），
// 匯卷頁的「OCR 運算主機」下拉選單會自動列出，所有老師都看得到，不必手動輸入網址。
// 每個主機一列：id = 100000 + FNV-1a(Tailscale 名稱) % 10 億（同一台永遠是同一列）；每 5 分鐘更新「最後上線時間」。
// 失敗（沒網路、資料庫拒絕）只記錄，不影響主機本身。HOST_REGISTRY=0 可關閉。
const os = require("os");
const fs = require("fs");
const path = require("path");

const SUPABASE_URL = process.env.HOST_REGISTRY_URL || "https://esfepufqyeplafzbeeag.supabase.co";
const SUPABASE_KEY = process.env.HOST_REGISTRY_KEY || "sb_publishable_P_OpGwKYuWxpHMHCZdQRPA_1lCCLfNf";   // 公開的 anon key，與網站前端相同
const ID_BASE = 100000;

function hostId(dns) {
  let h = 0x811c9dc5;
  for (const b of Buffer.from(String(dns).toLowerCase(), "utf8")) { h ^= b; h = Math.imul(h, 0x01000193) >>> 0; }
  return ID_BASE + (h % 1000000000);
}
function displayName(here) {
  try { const c = JSON.parse(fs.readFileSync(path.join(here, "host-config.json"), "utf8")); if (c.displayName && String(c.displayName).trim()) return String(c.displayName).trim().slice(0, 20); } catch (e) {}
  return os.hostname().slice(0, 20);
}
function buildRow(dns, name, extra) {
  const url = `https://${dns}:8443`;
  const obj = Object.assign({ kind: "ocr-host", v: 1, name, url, aiUrl: `https://${dns}:8790`, platform: process.platform, seen: Date.now(), online: true }, extra || {});
  return { id: hostId(dns), content: JSON.stringify(obj) };
}
async function upsert(row, fetchImpl) {
  const f = fetchImpl || fetch;
  const r = await f(`${SUPABASE_URL}/rest/v1/syllabus_doc?on_conflict=id`, {
    method: "POST",
    headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}`, "Content-Type": "application/json", Prefer: "resolution=merge-duplicates,return=minimal" },
    body: JSON.stringify([row]),
  });
  if (!r.ok) throw new Error(`登錄失敗 HTTP ${r.status}`);
  return true;
}
// getDns: async () => "主機.xxxx.ts.net" 或 ""；ready: async () => OCR 服務是否已起來
function start({ here, getDns, ready, log, revision, intervalMs, fetchImpl }) {
  if (process.env.HOST_REGISTRY === "0") { log("[登錄] 已關閉（HOST_REGISTRY=0）"); return { stop() {} }; }
  const stateFile = path.join(here, "output", "registry-state.json");
  const writeState = (o) => { try { fs.writeFileSync(stateFile, JSON.stringify(o)); } catch (e) {} };
  let last = null, stopped = false;
  async function tick(online = true) {
    try {
      const dns = await getDns();
      if (!dns) { writeState({ ok: false, at: Date.now(), error: "Tailscale 尚未連線" }); return; }
      if (online && !(await ready())) { writeState({ ok: false, at: Date.now(), error: "OCR 服務尚未啟動" }); return; }
      const name = displayName(here);
      const row = buildRow(dns, name, { revision: revision || "", online });
      await upsert(row, fetchImpl);
      if (!last || last.name !== name || last.dns !== dns) log(`[登錄] 已登錄到網站：${name}（https://${dns}:8443）`);
      last = { name, dns };
      writeState({ ok: true, at: Date.now(), name, url: `https://${dns}:8443`, online });
    } catch (e) { writeState({ ok: false, at: Date.now(), error: String(e.message || e).slice(0, 120) }); if (!stopped) log(`[登錄] ${e.message || e}`); }
  }
  setTimeout(tick, +process.env.HOST_REGISTRY_FIRST_MS || 15000);
  const iv = setInterval(tick, intervalMs || 300000);
  return {
    stop: async () => { stopped = true; clearInterval(iv); try { await tick(false); } catch (e) {} },   // 結束時標成離線（盡力而為）
    tick,
  };
}
module.exports = { start, hostId, buildRow, upsert, displayName, ID_BASE };
