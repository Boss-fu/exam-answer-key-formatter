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
    await page.locator('[data-target="preflight"]').click();
    await page.locator("#ocrFile").setInputFiles(sample);
    const begin = Date.now();
    await page.locator("#ocrBtn").click();
    await page.waitForFunction(() => /OCR 預檢完成|預檢未完成/.test(document.querySelector("#ocrStatus").textContent), {}, { timeout: 240000 });
    const status = await page.locator("#ocrStatus").textContent();
    assert(status.includes("OCR 預檢完成"), status);
    await page.waitForFunction(() => Array.from(document.querySelectorAll("#ocrResult img")).every((i) => i.complete && i.naturalWidth > 0));
    const result = await page.evaluate(() => ({ summary: ocrReport.summary, job: ocrJob, server: ocrServer }));
    console.log(JSON.stringify({ ...result, seconds: (Date.now() - begin) / 1000 }, null, 2));
    // Switch without losing the old report. Review must still target its owner.
    await page.locator("#serverSelect").selectOption(host === "mac" ? "company" : "mac");
    assert(await page.locator("#ocrResult").isVisible());
    const saved = page.waitForResponse((r) => r.url().includes("/api/ocr-review"));
    await page.locator("#ocrResult .ocr-actions button").first().click();
    const response = await saved;
    assert(response.url().startsWith(config.servers[host].baseUrl));
    assert(response.ok());
    await page.locator("#serverSelect").selectOption(host);
    await page.waitForFunction(() => document.querySelector("#engineBadge").textContent.includes("已連線"));
    fs.mkdirSync("output/connection-check", { recursive: true });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: `output/connection-check/vercel-${host}-ocr.png` });
    assert.deepEqual(errors, []);
    console.log("PASS: Vercel → selected host upload, images, review after switching hosts");
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
