// 靜態 UI 契約：不開瀏覽器，只盤點首頁與人工修正頁宣告的 API 與按鈕。
// 首次或刻意更新基準：node local-app/test/ui-contract.cjs --write-baseline-from-head
const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const pages = ["public/index.html", "public/label.html"];
const baselinePath = path.join(__dirname, "ui-contract.baseline.json");

function inventorySource(source) {
  return {
    endpoints: [...new Set(source.match(/\/api\/[A-Za-z0-9_./-]+/g) || [])].sort(),
    buttonIds: [...new Set([...source.matchAll(/<button\b[^>]*\bid=["']([^"']+)["']/gi)].map((m) => m[1]))].sort(),
  };
}

function inventory(relativePath) {
  return inventorySource(fs.readFileSync(path.join(root, relativePath), "utf8"));
}

const current = Object.fromEntries(pages.map((page) => [page, inventory(page)]));
if (process.argv.includes("--write-baseline-from-head")) {
  const baseline = Object.fromEntries(pages.map((page) => [page, inventorySource(
    childProcess.execFileSync("git", ["show", `HEAD:local-app/${page}`], { cwd: root, encoding: "utf8" })
  )]));
  fs.writeFileSync(baselinePath, `${JSON.stringify(baseline, null, 2)}\n`);
  console.log(`已由 HEAD 改版前內容寫入基準：${path.relative(process.cwd(), baselinePath)}`);
  console.log(JSON.stringify(baseline, null, 2));
  process.exit(0);
}

assert.ok(fs.existsSync(baselinePath), "找不到 UI 契約基準；先加 --write-baseline-from-head");
const baseline = JSON.parse(fs.readFileSync(baselinePath, "utf8"));
for (const page of pages) {
  const missingEndpoints = (baseline[page]?.endpoints || []).filter((item) => !current[page].endpoints.includes(item));
  const missingButtons = (baseline[page]?.buttonIds || []).filter((item) => !current[page].buttonIds.includes(item));
  assert.deepEqual(missingEndpoints, [], `${page} 少了 API 端點：${missingEndpoints.join(", ")}`);
  assert.deepEqual(missingButtons, [], `${page} 少了按鈕 id：${missingButtons.join(", ")}`);
}
console.log("PASS: UI 契約端點與既有按鈕 id 均未減少");
console.log(JSON.stringify(current, null, 2));
