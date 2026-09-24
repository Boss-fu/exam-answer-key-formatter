// 本機小網頁應用：在你自己的電腦跑 `npm start`，瀏覽器開 http://localhost:8787
// 丟考卷文字 → 產生詳解 JSON → 產出 Word(.docx) 並存到 local-app/output/。
// 這是「本機」伺服器，才有權限讀寫你的檔案；沙箱 artifact 做不到這件事。
const http = require("http");
const fs = require("fs");
const path = require("path");
const { generate, generateDirtyExam, generateFromImages } = require("./engine");
const { extractText } = require("./extract");
const { buildDocxBuffer, checkAnswers } = require("../code/render");

const PORT = process.env.PORT || 8787;
const PUBLIC = path.join(__dirname, "public");
const OUTPUT = path.join(__dirname, "output");
const DIRTY_OUTPUT = path.join(OUTPUT, "dirty-exams");
fs.mkdirSync(OUTPUT, { recursive: true });
fs.mkdirSync(DIRTY_OUTPUT, { recursive: true });

function send(res, code, type, body) {
  res.writeHead(code, { "content-type": type });
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

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);

    // --- 靜態首頁 ---
    if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/index.html")) {
      return send(res, 200, "text/html; charset=utf-8", fs.readFileSync(path.join(PUBLIC, "index.html")));
    }

    // --- 上傳 PDF/Word/文字檔 → 取出純文字（給前端放進考卷欄位確認後再產詳解）---
    if (req.method === "POST" && url.pathname === "/api/extract") {
      const { filename, dataBase64 } = JSON.parse(await readBody(req) || "{}");
      if (!dataBase64) return sendJson(res, 400, { ok: false, error: "缺少檔案內容" });
      const buffer = Buffer.from(dataBase64, "base64");
      const text = await extractText(filename || "", buffer);
      return sendJson(res, 200, { ok: true, filename, chars: text.length, text });
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
      const mismatches = checkAnswers(data.questions);
      return sendJson(res, 200, { ok: true, data, mismatches });
    }

    // --- 產出 Word 並存到本機 output/，同時回傳檔案供下載 ---
    if (req.method === "POST" && url.pathname === "/api/render") {
      const { data, filename } = JSON.parse(await readBody(req) || "{}");
      if (!data || !Array.isArray(data.questions)) return sendJson(res, 400, { ok: false, error: "缺少 questions" });
      const buf = await buildDocxBuffer(data);
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

server.listen(PORT, () => {
  console.log(`\n  詳解本機應用已啟動 →  http://localhost:${PORT}`);
  console.log(`  引擎：${process.env.ENGINE || "claude-code"}（用 ENGINE=api 可切換到 Anthropic API）`);
  console.log(`  產出的 Word 會存到：${OUTPUT}\n`);
});
