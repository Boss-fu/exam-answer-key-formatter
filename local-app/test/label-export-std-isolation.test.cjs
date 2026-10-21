// 匯出端只可由 job 推回自己的考卷；不可透過 ZIP 檔名跨卷讀取。
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { Writable } = require("node:stream");
const { execFileSync } = require("node:child_process");
const server = require("../server");

const app = path.join(__dirname, "..");
const repo = path.join(app, "..");
const pyOcr = path.join(app, "output", "py-ocr");
const std = path.join(repo, "ocr", "dehand", "data", "std");
const jobA = "111111111111111111111111";
const jobB = "222222222222222222222222";
const jobEmpty = "333333333333333333333333";
const examA = "route-isolation-A";
const examB = "route-isolation-B";
const examEmpty = "route-empty";

function makeJob(job, exam) {
  const root = path.join(pyOcr, job);
  fs.mkdirSync(path.join(root, "result"), { recursive: true });
  fs.writeFileSync(path.join(root, exam + ".pdf"), "fixture");
  fs.writeFileSync(path.join(root, "result", "ocr.txt"), "fixture");
}
function makeStd(exam, marker) {
  const root = path.join(std, exam);
  fs.mkdirSync(root, { recursive: true });
  fs.writeFileSync(path.join(root, "001_in.jpg"), marker + "_INPUT");
  fs.writeFileSync(path.join(root, "001_gt.png"), marker + "_GROUND_TRUTH");
  fs.writeFileSync(path.join(root, "001_info.txt"), `${exam} 第1頁\n修補過：是\n`);
}
function request(method, route, body) {
  return new Promise((resolve) => {
    const req = new EventEmitter();
    req.method = method; req.url = route; req.headers = { host: "localhost" };
    if (body) req.headers["content-type"] = "application/json";
    const chunks = [];
    const res = new Writable({ write(chunk, _encoding, done) { chunks.push(Buffer.from(chunk)); done(); } });
    res.headers = {}; res.writeHead = (status, headers) => { res.status = status; res.headers = headers || {}; };
    res.on("finish", () => resolve({ status: res.status, headers: res.headers, body: Buffer.concat(chunks) }));
    server.emit("request", req, res);
    process.nextTick(() => { if (body) req.emit("data", Buffer.from(body)); req.emit("end"); });
  });
}

(async () => {
  makeJob(jobA, examA); makeJob(jobB, examB); makeJob(jobEmpty, examEmpty);
  makeStd(examA, "A_ONLY"); makeStd(examB, "B_ONLY");
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "label-export-"));
  try {
    const created = await request("POST", "/api/label/export-std", JSON.stringify({ job: jobA }));
    const body = JSON.parse(created.body);
    assert.equal(created.status, 200);
    assert.equal(body.downloadUrl, "/api/label/export-std/download?job=" + jobA);

    // 即使舊版可猜的 name 參數被附上，A 的 job 仍只能得到 A 的三檔 ZIP。
    const download = await request("GET", body.downloadUrl + "&name=" + encodeURIComponent(examB + "_訓練資料.zip"));
    assert.equal(download.status, 200);
    const zip = path.join(temp, "export.zip");
    fs.writeFileSync(zip, download.body);
    const listing = execFileSync("unzip", ["-Z1", zip], { encoding: "utf8" });
    assert.deepEqual(listing.trim().split("\n").sort(), ["001_gt.png", "001_in.jpg", "001_info.txt"]);
    assert.doesNotMatch(listing, new RegExp(examB));
    const entries = execFileSync("python3", ["-c", "import sys, zipfile; print(*[i.flag_bits & 0x800 for i in zipfile.ZipFile(sys.argv[1]).infolist()])", zip], { encoding: "utf8" }).trim().split(/\s+/);
    assert.deepEqual(entries, ["2048", "2048", "2048"]);

    const empty = await request("POST", "/api/label/export-std", JSON.stringify({ job: jobEmpty }));
    assert.equal(empty.status, 404);
    assert.equal(JSON.parse(empty.body).error, "這份考卷還沒有提交任何頁");
    const emptyDownload = await request("GET", "/api/label/export-std/download?job=" + jobEmpty);
    assert.equal(emptyDownload.status, 404);
    assert.equal(emptyDownload.body.toString(), "這份考卷還沒有提交任何頁");
    assert.equal((await request("GET", "/api/label/export-std/download?job=bad")).status, 400);
    console.log("PASS: ZIP 平放且 UTF-8 旗標正確，未提交頁回傳 404，A job 無法下載 B 卷");
  } finally {
    for (const job of [jobA, jobB, jobEmpty]) fs.rmSync(path.join(pyOcr, job), { recursive: true, force: true });
    fs.rmSync(path.join(std, examA), { recursive: true, force: true });
    fs.rmSync(path.join(std, examB), { recursive: true, force: true });
    fs.rmSync(path.join(std, examEmpty), { recursive: true, force: true });
    fs.rmSync(path.join(std, "_exports", examA + "_訓練資料.zip"), { force: true });
    fs.rmSync(path.join(std, "_exports", examB + "_訓練資料.zip"), { force: true });
    fs.rmSync(temp, { recursive: true, force: true });
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
