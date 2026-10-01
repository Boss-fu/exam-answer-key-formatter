// Reserve a dedicated private HTTPS port for OCR; never use Funnel or overwrite
// another service's Serve configuration. Called asynchronously after startup.
const { execFile } = require("child_process");
const fs = require("fs");
const path = require("path");
const { promisify } = require("util");
const exec = promisify(execFile);
const HTTPS_PORT = 8443;

function plan(config, dns, target) {
  const key = `${dns}:${HTTPS_PORT}`;
  const web = config.Web || {}, tcp = config.TCP || {};
  if (config.AllowFunnel && Object.values(config.AllowFunnel).some(Boolean)) return "conflict";
  if (!tcp[HTTPS_PORT] && !Object.keys(web).some((k) => k.endsWith(`:${HTTPS_PORT}`))) return "create";
  const handlers = web[key]?.Handlers;
  return tcp[HTTPS_PORT]?.HTTPS && handlers && Object.keys(handlers).length === 1 && handlers["/"]?.Proxy === target ? "ready" : "conflict";
}

async function ensurePrivateHttps(port = 8787) {
  if (process.env.OCR_HTTPS_AUTO === "0") return;
  let bin = process.env.TAILSCALE_BIN;
  if (!bin) {
    const candidate = process.platform === "darwin" ? "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
      : path.join(process.env.ProgramFiles || "C:\\Program Files", "Tailscale", "tailscale.exe");
    bin = fs.existsSync(candidate) ? candidate : "tailscale";
  }
  const run = (args) => exec(bin, args, { timeout: 12000, maxBuffer: 1024 * 1024, windowsHide: true,
    env: { ...process.env, TAILSCALE_BE_CLI: "1" } });
  try {
    const status = JSON.parse((await run(["status", "--json"])).stdout);
    const dns = (status.Self?.DNSName || "").replace(/\.$/, "");
    if (status.BackendState !== "Running" || !dns) throw new Error("Tailscale 尚未連線");
    const config = JSON.parse((await run(["serve", "status", "--json"])).stdout || "{}");
    const target = `http://127.0.0.1:${port}`;
    const action = plan(config, dns, target);
    if (action === "conflict") throw new Error("8443 已由其他服務使用，或存在 Funnel 設定；未更動任何現有設定");
    if (action === "create") await run(["serve", "--bg", `--https=${HTTPS_PORT}`, "--yes", target]);
    console.log(`[HTTPS] 私有 OCR 入口：https://${dns}:${HTTPS_PORT}（僅限 Tailscale）`);
  } catch (error) {
    console.warn(`[HTTPS] 自動設定未完成：${error.message}。可在主機執行 tailscale serve --bg --https=8443 http://127.0.0.1:${port}；請勿使用 Funnel。`);
  }
}
module.exports = { plan, ensurePrivateHttps };
