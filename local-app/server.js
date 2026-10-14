// 本機小網頁應用：在你自己的電腦跑 `npm start`，瀏覽器開 http://localhost:8787
// 丟考卷文字 → 產生詳解 JSON → 產出 Word(.docx) 並存到 local-app/output/。
// 這是「本機」伺服器，才有權限讀寫你的檔案；沙箱 artifact 做不到這件事。
const http = require("http");
const fs = require("fs");
const path = require("path");
const { applyCors } = require("./cors");
const portalConfig = require("./public/server-config");
const { generate, generateDirtyExam, generateCleanExam, generateExamPackage, generateFromImages } = require("./engine");
const { extractText } = require("./extract");
const { preflight } = require("./ocr-preflight");
const { runPyOcr, pyBin } = require("./py-ocr");
const { execFile } = require("child_process");
// Word 相關套件只在需要輸出時載入，避免新版 Node 啟動服務時被舊套件阻塞。
function renderer() { return require("../code/render"); }

const PORT = process.env.PORT || 8787;
const HOST = process.env.HOST || undefined;
const PUBLIC = path.join(__dirname, "public");
const OUTPUT = path.join(__dirname, "output");
const DIRTY_OUTPUT = path.join(OUTPUT, "dirty-exams");
const CLEAN_OUTPUT = path.join(OUTPUT, "clean-exams");
const PKG_OUTPUT = path.join(OUTPUT, "exam-packages");
const OCR_OUTPUT = path.join(OUTPUT, "ocr-review");
const PYOCR_OUTPUT = path.join(OUTPUT, "py-ocr");
const LABEL_OUTPUT = path.join(OUTPUT, "label");
let revision = "unknown";
try { revision = require("child_process").execFileSync("git", ["rev-parse", "--short", "HEAD"], { cwd: path.join(__dirname, ".."), encoding: "utf8", timeout: 2000 }).trim(); } catch {}
fs.mkdirSync(OUTPUT, { recursive: true });
fs.mkdirSync(DIRTY_OUTPUT, { recursive: true });
fs.mkdirSync(CLEAN_OUTPUT, { recursive: true });
fs.mkdirSync(PKG_OUTPUT, { recursive: true });
fs.mkdirSync(OCR_OUTPUT, { recursive: true });
fs.mkdirSync(PYOCR_OUTPUT, { recursive: true });
fs.mkdirSync(LABEL_OUTPUT, { recursive: true });

function runLabel(args) {
  return new Promise((resolve, reject) => execFile(pyBin(), [path.join(__dirname, "..", "ocr", "label_api.py"), ...args], { cwd: path.join(__dirname, ".."), maxBuffer: 48 * 1024 * 1024, timeout: 120000 }, (err, out, stderr) => {
    let data; try { data = JSON.parse(String(out).trim().split("\n").pop()); } catch {}
    // Python/ONNX 的 stderr 可能只有 CoreML 警告；工作台協定只認退出碼與 stdout JSON。
    if (err || !data || !data.ok) return reject(new Error((data && data.error) || (err && err.message) || "工作台處理失敗"));
    resolve(data);
  }));
}
function runCleanExam(sourcePath, outputPath) {
  return new Promise((resolve, reject) => {
    const script = path.join(__dirname, "..", "ocr", "clean_pdf.py");
    execFile(pyBin(), [script, sourcePath, outputPath, "--dpi", "200"], {
      cwd: path.join(__dirname, ".."), timeout: 900000, maxBuffer: 4 * 1024 * 1024
    }, (err, stdout, stderr) => {
      if (err) return reject(new Error(String(stderr || err.message).slice(-600)));
      const lines = String(stdout || "").trim().split(/\r?\n/).filter(Boolean);
      let stats = {}; try { stats = JSON.parse(lines[lines.length - 1]); } catch {}
      if (!stats.ok) return reject(new Error("清理器沒有回傳完成狀態"));
      resolve(stats);
    });
  });
}
function labelPdf(id) {
  if (!/^[a-f0-9]{24}$/i.test(String(id || ""))) throw new Error("無效的工作編號");
  const source = path.join(LABEL_OUTPUT, id, "source.pdf");
  if (!fs.existsSync(source)) throw new Error("找不到上傳的 PDF");
  return source;
}

function send(res, code, type, body) {
  res.writeHead(code, { "content-type": type, "cache-control": "no-store" });
  res.end(body);
}
function sendJson(res, code, obj) { send(res, code, "application/json; charset=utf-8", JSON.stringify(obj)); }

function readBody(req, limit = 24 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    let data = "", size = 0;
    req.on("data", (c) => {
      size += c.length;
      if (size > limit) { reject(new Error("內容過大")); req.destroy(); return; }
      data += c;
    });
    req.on("end", () => resolve(data));
    req.on("error", reject);
  });
}

function safeName(name) {
  return String(name || "詳解").replace(/[\/\\:*?"<>|]/g, "_").slice(0, 120) || "詳解";
}
function safeJobId(id) { return /^[a-f0-9]{24}$/i.test(String(id || "")) ? String(id) : null; }
function safeSourceName(name) { return safeName(name).replace(/\.+$/g, "") || "source"; }
function pyOcrResultFile(job, kind) {
  const safeJob = safeJobId(job);
  const fields = { docx: "docx", txt: "txt", review: "review" };
  const extensions = { docx: ".docx", txt: ".txt", review: ".txt" };
  if (!safeJob || !fields[kind]) return null;
  let report;
  try { report = JSON.parse(fs.readFileSync(path.join(PYOCR_OUTPUT, safeJob, "report.json"), "utf8")); } catch { return null; }
  const filename = report[fields[kind]];
  if (typeof filename !== "string" || path.extname(filename).toLowerCase() !== extensions[kind]) return null;
  const base = path.join(PYOCR_OUTPUT, safeJob, "result");
  const file = path.resolve(base, filename);
  return file.startsWith(base + path.sep) && fs.existsSync(file) ? file : null;
}
function pyOcrDownloadUrl(job, kind) {
  return pyOcrResultFile(job, kind) ? `/api/py-ocr/download?job=${job}&kind=${kind}` : null;
}
function pyOcrResponse(job, result) {
  let reviewText = "";
  if (result.reviewFile) { try { reviewText = fs.readFileSync(path.join(PYOCR_OUTPUT, job, "result", result.reviewFile), "utf8"); } catch {} }
  return { ok: true, job, text: result.text, log: result.log, stats: result.stats, reviewText,
    reviewUrl: result.reviewFile ? `/api/py-ocr/download?job=${job}&kind=review` : null,
    figures: result.figures.map((name) => ({ name, url: `/api/py-ocr/figure?job=${job}&name=${encodeURIComponent(name)}` })),
    txtUrl: result.txtFile ? `/api/py-ocr/download?job=${job}&kind=txt` : null,
    docxUrl: result.docxFile ? `/api/py-ocr/download?job=${job}&kind=docx` : null };
}
function pyOcrJobPaths(job) {
  const jobDir = path.join(PYOCR_OUTPUT, job);
  return { jobDir, resultDir: path.join(jobDir, "result") };
}
function readProgress(resultDir, since) {
  try { return fs.readFileSync(path.join(resultDir, "progress.jsonl"), "utf8").split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line)); }
  catch { return []; }
}

const server = http.createServer(async (req, res) => {
  if (!applyCors(req, res)) return;
  if (req.method === "POST" && !(req.headers["content-type"] || "").toLowerCase().startsWith("application/json")) return sendJson(res, 415, { ok: false, error: "API 僅接受 application/json" });
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);


    // --- 靜態首頁 ---
    if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/index.html")) {
      return send(res, 200, "text/html; charset=utf-8", fs.readFileSync(path.join(PUBLIC, "index.html")));
    }
    if (req.method === "GET" && url.pathname === "/label.html") return send(res, 200, "text/html; charset=utf-8", fs.readFileSync(path.join(PUBLIC, "label.html")));
    if (req.method === "GET" && url.pathname === "/manifest.webmanifest") return send(res, 200, "application/manifest+json; charset=utf-8", fs.readFileSync(path.join(PUBLIC, "manifest.webmanifest")));
    if (req.method === "GET" && url.pathname === "/sw.js") return send(res, 200, "application/javascript; charset=utf-8", fs.readFileSync(path.join(PUBLIC, "sw.js")));
    if (req.method === "GET" && url.pathname === "/eraser.html") return send(res, 200, "text/html; charset=utf-8", fs.readFileSync(path.join(PUBLIC, "eraser.html")));
    if (req.method === "GET" && ["/api-client.js", "/server-config.js", "/patch-notes.js", "/dehand-web.js"].includes(url.pathname)) {
      return send(res, 200, "application/javascript; charset=utf-8", fs.readFileSync(path.join(PUBLIC, url.pathname.slice(1))));
    }
    if (req.method === "GET" && url.pathname.startsWith("/models/")) {
      const name = path.basename(url.pathname);
      if (!/^dehand-web(?:-int8)?\.(?:onnx|json)$/.test(name)) return send(res, 404, "text/plain", "Not found");
      const file = path.join(PUBLIC, "models", name);
      if (!fs.existsSync(file)) return send(res, 404, "text/plain", "Not found");
      return send(res, 200, name.endsWith(".json") ? "application/json; charset=utf-8" : "application/octet-stream", fs.readFileSync(file));
    }
    if (req.method === "GET" && ["/changelog.json", "/version.json"].includes(url.pathname)) {
      res.setHeader("cache-control", "no-store");     // 更新公告／版本偵測要拿最新的
      return send(res, 200, "application/json; charset=utf-8", fs.readFileSync(path.join(PUBLIC, url.pathname.slice(1))));
    }
    if (req.method === "GET" && url.pathname === "/favicon.ico") {
      return send(res, 200, "image/x-icon", fs.readFileSync(path.join(PUBLIC, "favicon.ico")));
    }
    if (req.method === "GET" && url.pathname === "/logo.png") {
      return send(res, 200, "image/png", fs.readFileSync(path.join(PUBLIC, "logo.png")));
    }
    if (req.method === "GET" && url.pathname === "/apple-touch-icon.png") return send(res, 200, "image/png", fs.readFileSync(path.join(PUBLIC, "apple-touch-icon.png")));
    if (req.method === "GET" && url.pathname === "/api/health") {
      return sendJson(res, 200, { ok: true, service: portalConfig.service, apiVersion: portalConfig.apiVersion, revision,
        host: require("os").hostname(), platform: process.platform, engine: process.env.ENGINE || "claude-code",
        ocrEngine: "tesseract", capabilities: ["ocr-preflight", "ocr-review", "extract", "exam-package", "generate", "render", "csv-to-word", "py-ocr"] });
    }

    // --- 本機考卷清理：沿用 dehand 模型，輸出影像 PDF，不上傳外部服務 ---
    if (req.method === "POST" && url.pathname === "/api/eraser/clean") {
      const { filename, dataBase64 } = JSON.parse(await readBody(req, 96 * 1024 * 1024) || "{}");
      if (!dataBase64) return sendJson(res, 400, { ok: false, error: "缺少考卷檔案" });
      const ext = path.extname(String(filename || "")).toLowerCase();
      if (![".pdf", ".png", ".jpg", ".jpeg"].includes(ext)) return sendJson(res, 400, { ok: false, error: "只接受 PDF、PNG 或 JPG 檔案" });
      const id = require("crypto").randomBytes(12).toString("hex");
      const dir = path.join(CLEAN_OUTPUT, id);
      fs.mkdirSync(dir, { recursive: true });
      const source = path.join(dir, safeSourceName(path.basename(String(filename))))
      const output = path.join(dir, safeName(path.basename(String(filename), ext)) + "_乾淨.pdf");
      fs.writeFileSync(source, Buffer.from(String(dataBase64), "base64"));
      try {
        const stats = await runCleanExam(source, output);
        return sendJson(res, 200, { ok: true, name: path.basename(output), pages: stats.pages, clearedRatio: stats.cleared_ratio,
          downloadUrl: `/api/eraser/download?id=${id}` });
      } catch (error) {
        try { fs.rmSync(dir, { recursive: true, force: true }); } catch {}
        return sendJson(res, 500, { ok: false, error: error.message || "清理失敗" });
      }
    }
    if (req.method === "GET" && url.pathname === "/api/eraser/download") {
      const id = String(url.searchParams.get("id") || "");
      if (!/^[a-f0-9]{24}$/i.test(id)) return send(res, 404, "text/plain; charset=utf-8", "Not found");
      const dir = path.resolve(CLEAN_OUTPUT, id);
      let target = null;
      try { target = fs.readdirSync(dir).map((name) => path.join(dir, name)).find((name) => name.endsWith("_乾淨.pdf")); } catch {}
      if (!target || !fs.existsSync(target) || !target.startsWith(dir + path.sep)) return send(res, 404, "text/plain; charset=utf-8", "Not found");
      res.writeHead(200, { "content-type": "application/pdf", "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent(path.basename(target))}`, "cache-control": "no-store" });
      return fs.createReadStream(target).pipe(res);
    }

    if (req.method === "POST" && url.pathname === "/api/label/list") {
      return sendJson(res, 200, await runLabel(["list"]));
    }
    if (req.method === "POST" && url.pathname === "/api/label/queue") return sendJson(res, 200, await runLabel(["queue"]));
    if (req.method === "POST" && url.pathname === "/api/label/upload") {
      const { dataBase64 } = JSON.parse(await readBody(req, 48 * 1024 * 1024) || "{}");
      if (!dataBase64) return sendJson(res, 400, { ok: false, error: "缺少 PDF" });
      const id = require("crypto").randomBytes(12).toString("hex");
      const directory = path.join(LABEL_OUTPUT, id);
      fs.mkdirSync(directory, { recursive: true });
      fs.writeFileSync(path.join(directory, "source.pdf"), Buffer.from(dataBase64, "base64"));
      return sendJson(res, 200, { ok: true, id });
    }
    if (req.method === "POST" && url.pathname === "/api/label/render") {
      const { id, job, pseudo, page } = JSON.parse(await readBody(req) || "{}");
      if (pseudo) return sendJson(res, 200, await runLabel(["render", "--pseudo", String(pseudo), "--page", String(Number(page) || 1)]));
      if (job) {
        return sendJson(res, 200, await runLabel(["render", "--job", safeJobId(job), "--page", String(Number(page) || 1)]));
      }
      return sendJson(res, 200, await runLabel(["render", "--pdf", labelPdf(id), "--page", String(Number(page) || 1)]));
    }
    if (req.method === "POST" && url.pathname === "/api/label/save-mask") {
      const { id, job, pseudo, page, mask } = JSON.parse(await readBody(req, 48 * 1024 * 1024) || "{}");
      if (!mask) return sendJson(res, 400, { ok: false, error: "缺少遮罩" });
      const temporary = path.join(LABEL_OUTPUT, safeJobId(id || job) || "bad", "mask.png");
      fs.mkdirSync(path.dirname(temporary), { recursive: true });
      fs.writeFileSync(temporary, Buffer.from(String(mask).split(",").pop(), "base64"));
      if (pseudo) return sendJson(res, 200, await runLabel(["save-mask", "--pseudo", String(pseudo), "--page", String(Number(page) || 1), "--mask", temporary]));
      if (job) {
        return sendJson(res, 200, await runLabel(["save-mask", "--job", safeJobId(job), "--page", String(Number(page) || 1), "--mask", temporary]));
      }
      return sendJson(res, 200, await runLabel(["save-mask", "--pdf", labelPdf(id), "--page", String(Number(page) || 1), "--mask", temporary]));
    }
    if (req.method === "POST" && url.pathname === "/api/label/draft") {
      const { job } = JSON.parse(await readBody(req) || "{}");
      return sendJson(res, 200, await runLabel(["draft", "--job", safeJobId(job) || ""]));
    }
    if (req.method === "POST" && url.pathname === "/api/label/save-questions") {
      const { job, data } = JSON.parse(await readBody(req) || "{}");
      const temporary = path.join(LABEL_OUTPUT, "questions-" + Date.now() + ".json");
      fs.writeFileSync(temporary, JSON.stringify(data || {}));
      try {
        return sendJson(res, 200, await runLabel(["save-questions", "--job", safeJobId(job) || "", "--data", temporary]));
      } finally {
        try { fs.unlinkSync(temporary); } catch {}
      }
    }
    if (req.method === "POST" && url.pathname === "/api/label/rebuild") {
      const { job } = JSON.parse(await readBody(req) || "{}");
      const result = await runLabel(["rebuild", "--job", safeJobId(job) || ""]);
      return sendJson(res, 200, { ...result, downloadUrl: `/api/label/download?job=${encodeURIComponent(job)}&file=${encodeURIComponent(result.docx)}` });
    }
    if (req.method === "GET" && url.pathname === "/api/label/download") {
      const job = safeJobId(url.searchParams.get("job"));
      const file = url.searchParams.get("file") || "";
      const base = path.join(PYOCR_OUTPUT, job || "", "result");
      const target = path.resolve(base, file);
      if (!job || !target.startsWith(base + path.sep) || !target.endsWith(".docx") || !fs.existsSync(target)) {
        return send(res, 404, "text/plain; charset=utf-8", "Not found");
      }
      res.writeHead(200, { "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent(path.basename(target))}` });
      return fs.createReadStream(target).pipe(res);
    }

    // --- 上傳 PDF/Word/文字檔 → 取出純文字（給前端放進考卷欄位確認後再產詳解）---
    if (req.method === "POST" && url.pathname === "/api/extract") {
      const { filename, dataBase64 } = JSON.parse(await readBody(req) || "{}");
      if (!dataBase64) return sendJson(res, 400, { ok: false, error: "缺少檔案內容" });
      const buffer = Buffer.from(dataBase64, "base64");
      const text = await extractText(filename || "", buffer);
      return sendJson(res, 200, { ok: true, filename, chars: text.length, text });
    }

    // --- 本機 OCR 試跑：先回傳題目候選區與信心，讓使用者覆核後再匯出 ---
    if (req.method === "POST" && url.pathname === "/api/ocr-preflight") {
      const { filename, dataBase64 } = JSON.parse(await readBody(req, 48 * 1024 * 1024) || "{}");
      if (!dataBase64) return sendJson(res, 400, { ok: false, error: "缺少考卷檔案" });
      const ext = path.extname(filename || "").toLowerCase();
      if (![".pdf", ".png", ".jpg", ".jpeg"].includes(ext)) return sendJson(res, 400, { ok: false, error: "OCR 試跑只接受 PDF、PNG 或 JPG 檔案" });
      const job = require("crypto").randomBytes(12).toString("hex");
      const jobDir = path.join(OCR_OUTPUT, job);
      fs.mkdirSync(jobDir, { recursive: true });
      const source = path.join(jobDir, safeSourceName(path.basename(filename || "考卷", ext)) + ext);
      fs.writeFileSync(source, Buffer.from(dataBase64, "base64"));
      const report = preflight({ sourcePath: source, jobDir });
      fs.writeFileSync(path.join(jobDir, "report.json"), JSON.stringify(report, null, 2));
      return sendJson(res, 200, { ok: true, job, report });
    }

    if (req.method === "POST" && url.pathname === "/api/ocr-review") {
      const { job, questionId, text, status } = JSON.parse(await readBody(req) || "{}");
      if (!safeJobId(job) || !questionId) return sendJson(res, 400, { ok: false, error: "缺少工作或題目編號" });
      const reportPath = path.join(OCR_OUTPUT, job, "report.json");
      const report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
      const question = report.questions.find((q) => q.id === questionId);
      if (!question) return sendJson(res, 404, { ok: false, error: "找不到這一題" });
      question.correctedText = String(text || "");
      question.status = status === "ok" ? "ok" : "review";
      report.corrections[questionId] = { text: question.correctedText, status: question.status, updatedAt: new Date().toISOString() };
      report.summary.reviewCount = report.questions.filter((q) => q.status !== "ok").length;
      fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
      return sendJson(res, 200, { ok: true, question, reviewCount: report.summary.reviewCount });
    }

    if (req.method === "GET" && url.pathname === "/api/ocr-page") {
      const job = safeJobId(url.searchParams.get("job"));
      const image = path.basename(url.searchParams.get("image") || "");
      if (!job || !/\.(png|jpe?g)$/i.test(image)) return send(res, 400, "text/plain; charset=utf-8", "Invalid request");
      const file = path.join(OCR_OUTPUT, job, "pages", image);
      if (!fs.existsSync(file)) return send(res, 404, "text/plain; charset=utf-8", "Not found");
      return fs.createReadStream(file).pipe(res);
    }

    // --- 髒考卷：掃描 PDF / 圖片 → Claude Code 重建茲茲格式 Word ---
    if (req.method === "POST" && url.pathname === "/api/dirty-exam") {
      const { filename, dataBase64 } = JSON.parse(await readBody(req, 48 * 1024 * 1024) || "{}");
      if (!dataBase64) return sendJson(res, 400, { ok: false, error: "缺少考卷檔案" });
      const ext = path.extname(filename || "").toLowerCase();
      if (![".pdf", ".png", ".jpg", ".jpeg"].includes(ext)) return sendJson(res, 400, { ok: false, error: "髒考卷整理只接受 PDF、PNG 或 JPG 檔案" });
      const job = require("crypto").randomBytes(12).toString("hex");
      const jobDir = path.join(DIRTY_OUTPUT, job);
      const workDir = path.join(jobDir, "work");
      const resultDir = path.join(jobDir, "result");
      fs.mkdirSync(workDir, { recursive: true });
      fs.mkdirSync(resultDir, { recursive: true });
      const source = path.join(jobDir, safeSourceName(path.basename(filename || "考卷", ext)) + ext);
      fs.writeFileSync(source, Buffer.from(dataBase64, "base64"));
      const result = await generateDirtyExam({ sourcePath: source, workDir, outputDir: resultDir });
      fs.writeFileSync(path.join(jobDir, "report.json"), JSON.stringify({ ...result, outputFile: path.basename(result.outputFile) }, null, 2));
      return sendJson(res, 200, { ok: true, job, summary: result.summary, reviewItems: result.reviewItems, downloadUrl: `/api/dirty-exam/download?job=${job}` });
    }

    // --- 由現有 23 欄 CSV 直接產乾淨 Word（不花 token，給「已經跑過、只是要 Word」用）---
    if (req.method === "POST" && url.pathname === "/api/csv-to-word") {
      const { csvText, filename } = JSON.parse(await readBody(req, 16 * 1024 * 1024) || "{}");
      if (!csvText || !csvText.trim()) return sendJson(res, 400, { ok: false, error: "缺少 CSV 內容" });
      const buf = await renderer().buildCleanExamBuffer(csvText);
      const name = safeName((filename || "乾淨考卷").replace(/\.(csv|xlsx?|txt)$/i, "")) + "-乾淨考卷.docx";
      const outPath = path.join(OUTPUT, name);
      fs.writeFileSync(outPath, buf);
      res.writeHead(200, {
        "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "content-disposition": `attachment; filename="doc.docx"; filename*=UTF-8''${encodeURIComponent(name)}`,
        "x-saved-path": encodeURIComponent(outPath),
      });
      return res.end(buf);
    }

    // --- 一鍵包：掃描 PDF/圖片 → 一次 Claude → 乾淨 Word ＋ 題庫 23 欄 CSV ---
    if (req.method === "POST" && url.pathname === "/api/exam-package") {
      const { filename, dataBase64 } = JSON.parse(await readBody(req, 48 * 1024 * 1024) || "{}");
      if (!dataBase64) return sendJson(res, 400, { ok: false, error: "缺少考卷檔案" });
      const ext = path.extname(filename || "").toLowerCase();
      if (![".pdf", ".png", ".jpg", ".jpeg"].includes(ext)) return sendJson(res, 400, { ok: false, error: "只接受 PDF、PNG 或 JPG 檔案" });
      const job = require("crypto").randomBytes(12).toString("hex");
      const jobDir = path.join(PKG_OUTPUT, job);
      const workDir = path.join(jobDir, "work");
      const resultDir = path.join(jobDir, "result");
      fs.mkdirSync(workDir, { recursive: true });
      fs.mkdirSync(resultDir, { recursive: true });
      const source = path.join(jobDir, safeSourceName(path.basename(filename || "考卷", ext)) + ext);
      fs.writeFileSync(source, Buffer.from(dataBase64, "base64"));
      const result = await generateExamPackage({ sourcePath: source, workDir, outputDir: resultDir });
      const report = { summary: result.summary, reviewItems: result.reviewItems,
        csv: result.csvPath ? path.basename(result.csvPath) : null,
        docx: result.docxPath ? path.basename(result.docxPath) : null };
      fs.writeFileSync(path.join(jobDir, "report.json"), JSON.stringify(report, null, 2));
      return sendJson(res, 200, { ok: true, job, summary: result.summary, reviewItems: result.reviewItems,
        csvUrl: report.csv ? `/api/exam-package/download?job=${job}&kind=csv` : null,
        docxUrl: report.docx ? `/api/exam-package/download?job=${job}&kind=docx` : null });
    }

    if (req.method === "GET" && url.pathname === "/api/exam-package/download") {
      const job = safeJobId(url.searchParams.get("job"));
      const kind = url.searchParams.get("kind") === "csv" ? "csv" : "docx";
      if (!job) return send(res, 400, "text/plain; charset=utf-8", "Invalid job");
      const report = JSON.parse(fs.readFileSync(path.join(PKG_OUTPUT, job, "report.json"), "utf8"));
      const fname = kind === "csv" ? report.csv : report.docx;
      if (!fname) return send(res, 404, "text/plain; charset=utf-8", "Not found");
      const file = path.join(PKG_OUTPUT, job, "result", path.basename(fname));
      if (!fs.existsSync(file)) return send(res, 404, "text/plain; charset=utf-8", "Not found");
      const ct = kind === "csv" ? "text/csv; charset=utf-8" : "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
      res.writeHead(200, { "content-type": ct, "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent(path.basename(file))}` });
      return fs.createReadStream(file).pipe(res);
    }

    // --- 即時掃描卷：立即建立工作，後續由 progress 取得新的畫面與步驟。---
    if (req.method === "POST" && url.pathname === "/api/py-ocr/start") {
      const { filename, dataBase64 } = JSON.parse(await readBody(req, 48 * 1024 * 1024) || "{}");
      if (!dataBase64) return sendJson(res, 400, { ok: false, error: "缺少考卷檔案" });
      const ext = path.extname(filename || "").toLowerCase();
      if (![".pdf", ".png", ".jpg", ".jpeg"].includes(ext)) return sendJson(res, 400, { ok: false, error: "只接受 PDF、PNG 或 JPG 檔案" });
      const job = require("crypto").randomBytes(12).toString("hex");
      const { jobDir, resultDir } = pyOcrJobPaths(job);
      fs.mkdirSync(resultDir, { recursive: true });
      const source = path.join(jobDir, safeSourceName(path.basename(filename || "考卷", ext)) + ext);
      fs.writeFileSync(source, Buffer.from(dataBase64, "base64"));
      fs.writeFileSync(path.join(jobDir, "state.json"), JSON.stringify({ status: "running" }));
      runPyOcr({ sourcePath: source, outDir: resultDir, progress: true }).then((result) => {
        const report = { txt: result.txtFile, docx: result.docxFile, review: result.reviewFile, figures: result.figures, stats: result.stats };
        fs.writeFileSync(path.join(jobDir, "report.json"), JSON.stringify(report, null, 2));
        fs.writeFileSync(path.join(jobDir, "state.json"), JSON.stringify({ status: "done" }));
      }).catch((error) => {
        fs.writeFileSync(path.join(jobDir, "state.json"), JSON.stringify({ status: "error", error: error.message }));
      });
      return sendJson(res, 200, { ok: true, jobId: job });
    }

    if (req.method === "GET" && url.pathname === "/api/py-ocr/progress") {
      const job = safeJobId(url.searchParams.get("job"));
      const since = Math.max(0, Number(url.searchParams.get("since")) || 0);
      if (!job) return sendJson(res, 400, { ok: false, error: "無效的工作編號" });
      const { jobDir, resultDir } = pyOcrJobPaths(job);
      const events = readProgress(resultDir).slice(since);
      let state = {}; try { state = JSON.parse(fs.readFileSync(path.join(jobDir, "state.json"), "utf8")); } catch {}
      if (state.status === "error") return sendJson(res, 200, { ok: false, events, next: since + events.length, done: true, error: state.error || "處理失敗" });
      if (state.status !== "done") return sendJson(res, 200, { ok: true, events, next: since + events.length, done: false });
      let report; try { report = JSON.parse(fs.readFileSync(path.join(jobDir, "report.json"), "utf8")); } catch { report = {}; }
      const files = fs.readdirSync(resultDir);
      const txtFile = report.txt || files.find((f) => f.toLowerCase().endsWith(".txt"));
      let text = ""; if (txtFile) { try { text = fs.readFileSync(path.join(resultDir, txtFile), "utf8"); } catch {} }
      return sendJson(res, 200, { ...pyOcrResponse(job, { text, log: "", txtFile, docxFile: report.docx, reviewFile: report.review, figures: report.figures || [], stats: report.stats || {} }), events, next: since + events.length, done: true });
    }

    if (req.method === "GET" && url.pathname === "/api/py-ocr/preview") {
      const job = safeJobId(url.searchParams.get("job"));
      const page = Number(url.searchParams.get("page"));
      const kind = url.searchParams.get("kind");
      if (!job || !Number.isInteger(page) || page < 1 || !["orig", "clean"].includes(kind)) return send(res, 400, "text/plain; charset=utf-8", "Invalid request");
      const base = path.join(PYOCR_OUTPUT, job, "result", "previews");
      const file = path.resolve(base, `p${page}_${kind}.jpg`);
      if (!file.startsWith(base + path.sep) || !fs.existsSync(file)) return send(res, 404, "text/plain; charset=utf-8", "Not found");
      return send(res, 200, "image/jpeg", fs.readFileSync(file));
    }

    // --- 掃描卷轉茲茲 Word：保留同步流程，供桌面版與舊前端相容。---
    if (req.method === "POST" && url.pathname === "/api/py-ocr") {
      const { filename, dataBase64 } = JSON.parse(await readBody(req, 48 * 1024 * 1024) || "{}");
      if (!dataBase64) return sendJson(res, 400, { ok: false, error: "缺少考卷檔案" });
      const ext = path.extname(filename || "").toLowerCase();
      if (![".pdf", ".png", ".jpg", ".jpeg"].includes(ext)) return sendJson(res, 400, { ok: false, error: "純 Python OCR 只接受 PDF、PNG 或 JPG 檔案" });
      const job = require("crypto").randomBytes(12).toString("hex");
      const jobDir = path.join(PYOCR_OUTPUT, job);
      const resultDir = path.join(jobDir, "result");
      fs.mkdirSync(resultDir, { recursive: true });
      const source = path.join(jobDir, safeSourceName(path.basename(filename || "考卷", ext)) + ext);
      fs.writeFileSync(source, Buffer.from(dataBase64, "base64"));
      const result = await runPyOcr({ sourcePath: source, outDir: resultDir });
      const report = { txt: result.txtFile, docx: result.docxFile, review: result.reviewFile, figures: result.figures, stats: result.stats };
      fs.writeFileSync(path.join(jobDir, "report.json"), JSON.stringify(report, null, 2));
      return sendJson(res, 200, pyOcrResponse(job, result));
    }

    if (req.method === "POST" && url.pathname === "/api/py-ocr/job-page") {
      const { job, page, view } = JSON.parse(await readBody(req) || "{}");
      const safeJob = safeJobId(job);
      if (!safeJob || !page) return sendJson(res, 400, { ok: false, error: "Invalid job or page" });
      const args = ["job-page", "--job", safeJob, "--page", String(page)];
      if (view === "dehand") args.push("--view", "dehand");
      return sendJson(res, 200, await runLabel(args));
    }

    if (req.method === "POST" && url.pathname === "/api/py-ocr/job") {
      const { job } = JSON.parse(await readBody(req) || "{}");
      const safeJob = safeJobId(job);
      if (!safeJob) return sendJson(res, 400, { ok: false, error: "Invalid job" });
      const txt = pyOcrResultFile(safeJob, "txt");
      let text = "尚無整份文字";
      if (txt) { try { text = fs.readFileSync(txt, "utf8"); } catch {} }
      return sendJson(res, 200, { ok: true, job: safeJob, text,
        docxUrl: pyOcrDownloadUrl(safeJob, "docx"),
        reviewUrl: pyOcrDownloadUrl(safeJob, "review"),
        txtUrl: pyOcrDownloadUrl(safeJob, "txt") });
    }

    if (req.method === "GET" && url.pathname === "/api/py-ocr/download") {
      const job = safeJobId(url.searchParams.get("job"));
      const kind = ["txt", "review"].includes(url.searchParams.get("kind")) ? url.searchParams.get("kind") : "docx";
      if (!job) return send(res, 400, "text/plain; charset=utf-8", "Invalid job");
      const file = pyOcrResultFile(job, kind);
      if (!file) return send(res, 404, "text/plain; charset=utf-8", "Not found");
      const ct = kind === "docx" ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document" : "text/plain; charset=utf-8";
      res.writeHead(200, { "content-type": ct, "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent(path.basename(file))}` });
      return fs.createReadStream(file).pipe(res);
    }

    if (req.method === "GET" && url.pathname === "/api/py-ocr/figure") {
      const job = safeJobId(url.searchParams.get("job"));
      const name = path.basename(url.searchParams.get("name") || "");
      if (!job || !/_fig\d+\.png$/i.test(name)) return send(res, 400, "text/plain; charset=utf-8", "Invalid request");
      const file = path.join(PYOCR_OUTPUT, job, "result", name);
      if (!fs.existsSync(file)) return send(res, 404, "text/plain; charset=utf-8", "Not found");
      res.writeHead(200, { "content-type": "image/png", "cache-control": "no-store" });
      return fs.createReadStream(file).pipe(res);
    }

    // --- 快速乾淨考卷（純文字）：掃描 PDF / 圖片 → Claude Code 只重打文字與表格、圖用〔含圖〕佔位 ---
    if (req.method === "POST" && url.pathname === "/api/clean-exam") {
      const { filename, dataBase64 } = JSON.parse(await readBody(req, 48 * 1024 * 1024) || "{}");
      if (!dataBase64) return sendJson(res, 400, { ok: false, error: "缺少考卷檔案" });
      const ext = path.extname(filename || "").toLowerCase();
      if (![".pdf", ".png", ".jpg", ".jpeg"].includes(ext)) return sendJson(res, 400, { ok: false, error: "快速乾淨考卷只接受 PDF、PNG 或 JPG 檔案" });
      const job = require("crypto").randomBytes(12).toString("hex");
      const jobDir = path.join(CLEAN_OUTPUT, job);
      const workDir = path.join(jobDir, "work");
      const resultDir = path.join(jobDir, "result");
      fs.mkdirSync(workDir, { recursive: true });
      fs.mkdirSync(resultDir, { recursive: true });
      const source = path.join(jobDir, safeSourceName(path.basename(filename || "考卷", ext)) + ext);
      fs.writeFileSync(source, Buffer.from(dataBase64, "base64"));
      const result = await generateCleanExam({ sourcePath: source, workDir, outputDir: resultDir });
      fs.writeFileSync(path.join(jobDir, "report.json"), JSON.stringify({ ...result, outputFile: path.basename(result.outputFile) }, null, 2));
      return sendJson(res, 200, { ok: true, job, summary: result.summary, reviewItems: result.reviewItems, downloadUrl: `/api/clean-exam/download?job=${job}` });
    }

    if (req.method === "GET" && url.pathname === "/api/clean-exam/download") {
      const job = safeJobId(url.searchParams.get("job"));
      if (!job) return send(res, 400, "text/plain; charset=utf-8", "Invalid job");
      const jobDir = path.join(CLEAN_OUTPUT, job);
      const report = JSON.parse(fs.readFileSync(path.join(jobDir, "report.json"), "utf8"));
      const file = path.join(jobDir, "result", path.basename(report.outputFile || ""));
      if (!file.endsWith(".docx") || !fs.existsSync(file)) return send(res, 404, "text/plain; charset=utf-8", "Not found");
      res.writeHead(200, { "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent(path.basename(file))}` });
      return fs.createReadStream(file).pipe(res);
    }

    if (req.method === "GET" && url.pathname === "/api/dirty-exam/download") {
      const job = safeJobId(url.searchParams.get("job"));
      if (!job) return send(res, 400, "text/plain; charset=utf-8", "Invalid job");
      const jobDir = path.join(DIRTY_OUTPUT, job);
      const report = JSON.parse(fs.readFileSync(path.join(jobDir, "report.json"), "utf8"));
      const file = path.join(jobDir, "result", path.basename(report.outputFile || ""));
      if (!file.endsWith(".docx") || !fs.existsSync(file)) return send(res, 404, "text/plain; charset=utf-8", "Not found");
      res.writeHead(200, { "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent(path.basename(file))}` });
      return fs.createReadStream(file).pipe(res);
    }

    // --- 產生詳解：examText（文字）或 images（拍照/圖片，讓 Claude 讀圖）→ JSON ---
    if (req.method === "POST" && url.pathname === "/api/generate") {
      const { examText, images } = JSON.parse(await readBody(req, 48 * 1024 * 1024) || "{}");
      const data = (images && images.length) ? await generateFromImages(images) : await generate(examText);
      const mismatches = renderer().checkAnswers(data.questions);
      return sendJson(res, 200, { ok: true, data, mismatches });
    }

    // --- 產出 Word 並存到本機 output/，同時回傳檔案供下載 ---
    if (req.method === "POST" && url.pathname === "/api/render") {
      const { data, filename } = JSON.parse(await readBody(req) || "{}");
      if (!data || !Array.isArray(data.questions)) return sendJson(res, 400, { ok: false, error: "缺少 questions" });
      const buf = await renderer().buildDocxBuffer(data);
      const name = safeName(filename || data.summaryLabel) + ".docx";
      const outPath = path.join(OUTPUT, name);
      fs.writeFileSync(outPath, buf);
      res.writeHead(200, {
        "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "content-disposition": `attachment; filename="doc.docx"; filename*=UTF-8''${encodeURIComponent(name)}`,
        "x-saved-path": encodeURIComponent(outPath),
      });
      return res.end(buf);
    }

    send(res, 404, "text/plain; charset=utf-8", "Not found");
  } catch (e) {
    sendJson(res, 500, { ok: false, error: e.message });
  }
});

if (require.main === module) server.listen(PORT, HOST, () => {
  require("./tailnet-https").ensurePrivateHttps(PORT);
  console.log(`\n  詳解本機應用已啟動 →  http://localhost:${PORT}`);
  if (HOST) console.log(`  網路監聽位址：${HOST}:${PORT}`);
  console.log(`  引擎：${process.env.ENGINE || "claude-code"}（可用 ENGINE=codex 或 ENGINE=api 切換）`);
  console.log(`  產出的 Word 會存到：${OUTPUT}\n`);
});

module.exports = server;
