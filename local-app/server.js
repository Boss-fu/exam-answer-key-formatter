// 本機小網頁應用：在你自己的電腦跑 `npm start`，瀏覽器開 http://localhost:8787
// 丟考卷文字 → 產生詳解 JSON → 產出 Word(.docx) 並存到 local-app/output/。
// 這是「本機」伺服器，才有權限讀寫你的檔案；沙箱 artifact 做不到這件事。
const http = require("http");
const fs = require("fs");
const path = require("path");
const { generate } = require("./engine");
const { buildDocxBuffer, checkAnswers } = require("../code/render");

const PORT = process.env.PORT || 8787;
const PUBLIC = path.join(__dirname, "public");
const OUTPUT = path.join(__dirname, "output");
fs.mkdirSync(OUTPUT, { recursive: true });

function send(res, code, type, body) {
  res.writeHead(code, { "content-type": type });
  res.end(body);
}
function sendJson(res, code, obj) { send(res, code, "application/json; charset=utf-8", JSON.stringify(obj)); }

function readBody(req, limit = 8 * 1024 * 1024) {
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

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);

    // --- 靜態首頁 ---
    if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/index.html")) {
      return send(res, 200, "text/html; charset=utf-8", fs.readFileSync(path.join(PUBLIC, "index.html")));
    }

    // --- 產生詳解：examText → JSON ---
    if (req.method === "POST" && url.pathname === "/api/generate") {
      const { examText } = JSON.parse(await readBody(req) || "{}");
      const data = await generate(examText);
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
