// Usage: NODE_PATH=<playwright modules> BROWSER_BIN=<Chrome executable>
// node local-app/test/portal-smoke.cjs <sample.pdf> [mac|company]
// Actual upload is intentional; writes a new OCR job on the selected host.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const config = require("../public/server-config");

(async () => {
  const [sample, host = "mac"] = process.argv.slice(2);
  if (!sample || !config.servers[host]) throw new Error("請提供測試 PDF 與 mac 或 company");
  const browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_BIN ? { executablePath: process.env.BROWSER_BIN } : {}) });
  try {
    const context = await browser.newContext({ permissions: ["local-network-access"], viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage(), errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(config.portalOrigins[0], { waitUntil: "domcontentloaded" });
    await page.locator("#serverSelect").selectOption(host);
    await page.waitForFunction(() => !document.querySelector("#engineBadge").textContent.includes("檢查中"), {}, { timeout: 25000 });
    assert((await page.locator("#engineBadge").textContent()).includes("已連線"), await page.locator("#connectionText").textContent());
    await page.locator("#pyFile").setInputFiles(sample);
    const begin = Date.now();
    await page.locator("#pyBtn").click();
    await page.waitForFunction(() => /完成！|錯誤：/.test(document.querySelector("#pyStatus").textContent), {}, { timeout: 240000 });
    const status = await page.locator("#pyStatus").textContent();
    assert(status.includes("完成！"), status);
    await page.waitForFunction(() => Array.from(document.querySelectorAll("#pyResult img")).every((i) => i.complete && i.naturalWidth > 0));
    const links = await page.locator("#pyResult a.btn").evaluateAll((as) => as.map((a) => a.href));
    assert(links.length > 0 && links.every((href) => href.startsWith(config.servers[host].baseUrl)), JSON.stringify(links));
    console.log(JSON.stringify({ links, figures: await page.locator("#pyResult img").count(), seconds: (Date.now() - begin) / 1000 }, null, 2));
    // Switching hosts must keep the finished result and its download links on the original host.
    await page.locator("#serverSelect").selectOption(host === "mac" ? "company" : "mac");
    assert(await page.locator("#pyResult").isVisible());
    const download = await fetch(links[0]);
    assert(download.ok, "download " + download.status);
    await page.locator("#serverSelect").selectOption(host);
    await page.waitForFunction(() => document.querySelector("#engineBadge").textContent.includes("已連線"));
    fs.mkdirSync("output/connection-check", { recursive: true });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: `output/connection-check/vercel-${host}-ocr.png` });
    assert.deepEqual(errors, []);
    console.log("PASS: Vercel → selected host conversion, figures, downloads after switching hosts");
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
