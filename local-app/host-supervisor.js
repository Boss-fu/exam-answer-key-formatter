// 茲茲主機背景程式（Windows／Mac 通用）：一個程式同時管三件事，視窗關掉也不會停
//   1) OCR 主機（local-app/server.js，埠 8787；啟動時自動設好 https://<主機名稱>:8443）
//   2) AI 橋接（local-app/ai-bridge.cjs，埠 8790，BRIDGE_OPEN=1 老師免輸入連線碼）
//   3) Tailscale HTTPS 入口（8790 → 橋接）；8443 由 server.js 自己設定
// 掛掉會自動重開；只允許同時一份在跑（鎖檔）；紀錄寫在 local-app/output/host.log。
// 用法：node local-app/host-supervisor.js   （正式使用請走「安裝」腳本，用工作排程器隱藏執行）
const { spawn, execFile } = require("child_process");
const fs = require("fs");
const net = require("net");
const registry = require("./host-registry");
const os = require("os");
const path = require("path");

const HERE = __dirname;
const OUT = path.join(HERE, "output");
const LOG = path.join(OUT, "host.log");
const LOCK = path.join(OUT, "host-supervisor.lock");
const OCR_PORT = +process.env.PORT || 8787;
const BRIDGE_PORT = +process.env.BRIDGE_PORT || 8790;
fs.mkdirSync(OUT, { recursive: true });

function log(msg) {
  const line = `${new Date().toISOString().replace("T", " ").slice(0, 19)}  ${msg}\n`;
  try { if (fs.existsSync(LOG) && fs.statSync(LOG).size > 2e6) fs.renameSync(LOG, LOG + ".old"); fs.appendFileSync(LOG, line); } catch (e) {}
  if (process.stdout.isTTY) process.stdout.write(line);
}

// ---- 單一實例鎖：已有另一份在跑就直接離開（工作排程器每 5 分鐘叫一次當看門狗）
function alive(pid) { try { process.kill(pid, 0); return true; } catch (e) { return e.code === "EPERM"; } }
function takeLock() {
  try {
    const old = parseInt(fs.readFileSync(LOCK, "utf8"), 10);
    if (old && old !== process.pid && alive(old)) return false;
  } catch (e) {}
  fs.writeFileSync(LOCK, String(process.pid));
  return true;
}
function releaseLock() { try { if (parseInt(fs.readFileSync(LOCK, "utf8"), 10) === process.pid) fs.unlinkSync(LOCK); } catch (e) {} }

// ---- 子程式管理：掛掉就重開，連續快速失敗會拉長等待（最多 60 秒），穩定跑 60 秒後歸零
function keep(name, file, env) {
  let delay = 2000, stopped = false, child = null;
  const start = () => {
    const t0 = Date.now();
    child = spawn(process.execPath, [file], { cwd: path.dirname(file), env: { ...process.env, ...env }, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    log(`[${name}] 已啟動 pid=${child.pid}`);
    const sink = (d) => { try { fs.appendFileSync(path.join(OUT, `${name}.log`), d); } catch (e) {} };
    child.stdout.on("data", sink); child.stderr.on("data", sink);
    child.on("exit", (code) => {
      if (stopped) return;
      delay = Date.now() - t0 > 60000 ? 2000 : Math.min(delay * 2, 60000);
      log(`[${name}] 結束（code=${code}），${Math.round(delay / 1000)} 秒後重開`);
      setTimeout(start, delay);
    });
  };
  start();
  return { stop() { stopped = true; try { child && child.kill(); } catch (e) {} } };
}

// ---- Tailscale：找執行檔、設定 8790 的私有 HTTPS 入口（絕不用 Funnel，不動別人的設定）
function tailscaleBin() {
  if (process.env.TAILSCALE_BIN) return process.env.TAILSCALE_BIN;
  const c = process.platform === "darwin" ? "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    : path.join(process.env.ProgramFiles || "C:\\Program Files", "Tailscale", "tailscale.exe");
  return fs.existsSync(c) ? c : "tailscale";
}
function ts(args) {
  return new Promise((ok, no) => execFile(tailscaleBin(), args, { timeout: 15000, windowsHide: true, maxBuffer: 1 << 20, env: { ...process.env, TAILSCALE_BE_CLI: "1" } },
    (e, out, err) => (e ? no(new Error((err || e.message).toString().slice(0, 200))) : ok(String(out)))));
}
async function ensureBridgeHttps() {
  try {
    const st = JSON.parse(await ts(["status", "--json"]));
    const dns = (st.Self?.DNSName || "").replace(/\.$/, "");
    if (st.BackendState !== "Running" || !dns) throw new Error("Tailscale 尚未連線");
    const cfg = JSON.parse((await ts(["serve", "status", "--json"])) || "{}");
    const used = (cfg.TCP || {})[BRIDGE_PORT] || Object.keys(cfg.Web || {}).some((k) => k.endsWith(`:${BRIDGE_PORT}`));
    const want = `http://127.0.0.1:${BRIDGE_PORT}`;
    const h = cfg.Web?.[`${dns}:${BRIDGE_PORT}`]?.Handlers;
    if (used && !(h && h["/"]?.Proxy === want)) throw new Error(`${BRIDGE_PORT} 已被其他 Tailscale 服務使用，未更動`);
    if (!used) await ts(["serve", "--bg", `--https=${BRIDGE_PORT}`, "--yes", want]);
    log(`[AI 橋接 HTTPS] https://${dns}:${BRIDGE_PORT}　[OCR HTTPS] https://${dns}:8443`);
    return dns;
  } catch (e) { log(`[HTTPS] 尚未完成：${e.message}（Tailscale 要開著並登入，且後台要開啟 HTTPS 憑證）`); return ""; }
}

async function tsDns() {
  try { const st = JSON.parse(await ts(["status", "--json"])); if (st.BackendState === "Running") return (st.Self?.DNSName || "").replace(/\.$/, ""); } catch (e) {}
  return "";
}
function portOpen(port) { return new Promise((ok) => { const s = net.connect(port, "127.0.0.1"); s.once("connect", () => { s.destroy(); ok(true); }); s.once("error", () => ok(false)); s.setTimeout(1500, () => { s.destroy(); ok(false); }); }); }

// ---- 自動更新（選用）：資料夾是 git clone 時，每 10 分鐘 fast-forward 一次，有新版就整個重開
function gitPull() {
  return new Promise((ok) => execFile("git", ["pull", "--ff-only"], { cwd: path.join(HERE, ".."), timeout: 60000, windowsHide: true }, (e, out) => ok(e ? "" : String(out))));
}

async function main() {
  if (!takeLock()) { log("已有另一份在跑，結束"); return process.exit(0); }
  log(`=== 啟動（${os.hostname()}，pid ${process.pid}）===`);
  // 同時處理幾份：local-app/host-config.json 例如 {"ocrConcurrency":2,"aiConcurrency":3}（沒有這個檔＝都是 1）
  let cfg = {}; try { cfg = JSON.parse(fs.readFileSync(path.join(HERE, "host-config.json"), "utf8")); } catch (e) {}
  const num = (v) => String(Math.max(1, Math.min(8, parseInt(v, 10) || 1)));
  log(`並行數：OCR/切圖 ${num(cfg.ocrConcurrency)}、AI 分類 ${num(cfg.aiConcurrency)}`);
  const kids = [
    keep("ocr", path.join(HERE, "server.js"), { HOST_CONCURRENCY: num(cfg.ocrConcurrency) }),
    keep("ai-bridge", path.join(HERE, "ai-bridge.cjs"), { BRIDGE_OPEN: process.env.BRIDGE_OPEN || "1", BRIDGE_PORT: String(BRIDGE_PORT), BRIDGE_CONCURRENCY: num(cfg.aiConcurrency) }),
  ];
  // 自動報到：把這台主機（名稱＋網址）登錄到網站，下拉選單就會自動出現；顯示名稱用 host-config.json 的 displayName
  let rev = ""; try { rev = require("child_process").execFileSync("git", ["rev-parse", "--short", "HEAD"], { cwd: path.join(HERE, ".."), encoding: "utf8", timeout: 4000, windowsHide: true }).trim(); } catch (e) {}
  const reg = registry.start({ here: HERE, getDns: tsDns, ready: () => portOpen(OCR_PORT), log, revision: rev });
  const stopAll = async () => { kids.forEach((k) => k.stop()); try { await Promise.race([reg.stop(), new Promise((r) => setTimeout(r, 4000))]); } catch (e) {} releaseLock(); process.exit(0); };
  process.on("SIGINT", stopAll); process.on("SIGTERM", stopAll); process.on("exit", releaseLock);
  // 等橋接起來再設 HTTPS；Tailscale 還沒連上就每分鐘再試，連上後每 10 分鐘複查
  let dns = "";
  const tryHttps = async () => { if (await portOpen(BRIDGE_PORT)) dns = await ensureBridgeHttps(); };
  setTimeout(tryHttps, 8000); setInterval(() => { tryHttps(); }, dns ? 600000 : 60000);
  if (process.env.HOST_AUTOUPDATE !== "0" && fs.existsSync(path.join(HERE, "..", ".git"))) {
    setInterval(async () => { const out = await gitPull(); if (out && !/Already up to date|已經是最新/i.test(out)) { log("有新版，重開服務"); stopAll(); } }, 600000);
  }
}
if (require.main === module) main();
module.exports = { takeLock, releaseLock, keep };
