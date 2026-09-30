// 解題引擎抽象層：examText（考卷文字）→ 詳解 JSON（build_from_json / render 使用的格式）。
//
// 支援兩種引擎，用環境變數 ENGINE 選擇：
//   ENGINE=claude-code （預設）— 呼叫本機已安裝的 Claude Code：`claude -p <prompt>`。
//                        沿用你現有的 Claude Code 訂閱，不需另外的 API 金鑰。
//   ENGINE=api          — 呼叫 Anthropic Claude API，需環境變數 ANTHROPIC_API_KEY。
//
// 兩者都把 repo 的規範/SKILL 當系統規則塞進 prompt，要求「只輸出 JSON」。
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const REPO = path.resolve(__dirname, "..");

// 讀規範與 SKILL 當作系統規則（找不到就跳過，不讓它擋住流程）。
function loadRules() {
  const files = [
    path.join(REPO, "理化數學詳解與簡答產出規範.md"),
    path.join(REPO, "exam-answer-key-formatter", "SKILL.md"),
  ];
  return files.map((f) => {
    try { return fs.readFileSync(f, "utf8"); } catch (e) { return ""; }
  }).filter(Boolean).join("\n\n---\n\n");
}

const OUTPUT_SPEC = `
你是國中理化/數學詳解產生器。依上面的規範產出詳解，然後【只輸出一段 JSON】，不要有任何解釋文字、不要用 markdown code fence 包起來。JSON 結構如下：
{
  "summaryLabel": "【單元/作業名稱】頁碼範圍，若不確定填 需人工確認",
  "questions": [
    {
      "no": 1,
      "answer": "A",
      "detail": "共用說明（沒有就留空字串）",
      "options": [
        { "label": "A", "text": "正確：… 或 錯誤：…" },
        { "label": "B", "text": "…" },
        { "label": "C", "text": "…" },
        { "label": "D", "text": "…" }
      ]
    }
  ]
}
規則：每題「以正確開頭」的選項其 label 必須等於 answer；公式用單一錢號 $...$；資訊不足處在該欄位文字內標「需人工確認」，不要自己捏造。`;

function buildPrompt(examText) {
  return `${loadRules()}\n\n${OUTPUT_SPEC}\n\n========== 以下是考卷內容 ==========\n${examText}`;
}

// 從模型輸出的字串裡把 JSON 抽出來（容忍前後雜訊或 code fence）。
function extractJson(text) {
  if (!text) throw new Error("引擎沒有回傳內容");
  let t = text.trim();
  const fence = t.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fence) t = fence[1].trim();
  const start = t.indexOf("{");
  const end = t.lastIndexOf("}");
  if (start === -1 || end === -1) throw new Error("輸出中找不到 JSON");
  return JSON.parse(t.slice(start, end + 1));
}

// --- 引擎一：Claude Code headless ---
function generateWithClaudeCode(examText) {
  return new Promise((resolve, reject) => {
    const bin = process.env.CLAUDE_BIN || "claude";
    const child = spawn(bin, ["-p", buildPrompt(examText)], {
      cwd: REPO,
      env: process.env,
    });
    let out = "", err = "";
    child.stdout.on("data", (d) => { out += d; });
    child.stderr.on("data", (d) => { err += d; });
    child.on("error", (e) => reject(new Error(
      `無法啟動 Claude Code（${bin}）：${e.message}。請確認本機已安裝 claude CLI，或設 CLAUDE_BIN 指向它。`)));
    child.on("close", (code) => {
      if (code !== 0 && !out) return reject(new Error(`Claude Code 結束碼 ${code}：${err.slice(0, 500)}`));
      try { resolve(extractJson(out)); }
      catch (e) { reject(new Error(`${e.message}\n引擎原始輸出前 500 字：${out.slice(0, 500)}`)); }
    });
  });
}

// --- 引擎二：Anthropic API ---
async function generateWithApi(examText) {
  const key = process.env.ANTHROPIC_API_KEY;
  if (!key) throw new Error("ENGINE=api 需要環境變數 ANTHROPIC_API_KEY");
  const model = process.env.ANTHROPIC_MODEL || "claude-sonnet-4-5";
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": key,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model,
      max_tokens: 8000,
      messages: [{ role: "user", content: buildPrompt(examText) }],
    }),
  });
  if (!res.ok) throw new Error(`API 錯誤 ${res.status}：${(await res.text()).slice(0, 500)}`);
  const body = await res.json();
  const text = (body.content || []).map((b) => b.text || "").join("");
  return extractJson(text);
}

async function generate(examText) {
  if (!examText || !examText.trim()) throw new Error("考卷內容是空的");
  const engine = (process.env.ENGINE || "claude-code").toLowerCase();
  if (engine === "api") return generateWithApi(examText);
  return generateWithClaudeCode(examText);
}

function generateDirtyExam({ sourcePath, workDir, outputDir }) {
  const engine = (process.env.ENGINE || "claude-code").toLowerCase();
  if (engine === "api") throw new Error("髒考卷整理需要可讀取檔案並產出 Word 的 Claude Code 引擎；請以預設 claude-code 模式啟動。");
  const bin = process.env.CLAUDE_BIN || "claude";
  const skill = path.join(REPO, "exam-to-word");
  const resultPath = path.join(workDir, "result.json");
  const prompt = `你是茲茲文教的考卷清稿助理。請完整閱讀 ${path.join(skill, "SKILL.md")}、${path.join(skill, "references", "example_build.py")} 與 ${path.join(skill, "references", "lessons.md")} 後執行工作。

來源檔案：${sourcePath}
工作資料夾：${workDir}
輸出資料夾：${outputDir}
範本：${path.join(skill, "assets", "template.docx")}

請把 scripts 複製到工作資料夾，依 SKILL 的流程將來源考卷重建成可編輯、套用茲茲格式的 Word。來源看不清、筆跡無法完全移除、或原卷疑似錯誤時，不可捏造，請列入人工確認。

完成後必須在 ${resultPath} 寫入 UTF-8 JSON（不要只在回答文字中說明）：
{
  "outputFile": "絕對路徑，且必須位於 ${outputDir} 內的 .docx 檔",
  "reviewItems": ["需人工確認事項"],
  "summary": "完成摘要"
}
最後只回覆一句完成狀態。`;
  return new Promise((resolve, reject) => {
    const child = spawn(bin, ["-p", prompt], { cwd: REPO, env: process.env });
    let err = "";
    child.stderr.on("data", (d) => { err += d; });
    child.on("error", (e) => reject(new Error(`無法啟動 Claude Code（${bin}）：${e.message}`)));
    child.on("close", (code) => {
      if (code !== 0) return reject(new Error(`髒考卷處理失敗（結束碼 ${code}）：${err.slice(0, 500)}`));
      try {
        const result = JSON.parse(fs.readFileSync(resultPath, "utf8"));
        if (!result.outputFile || !path.resolve(result.outputFile).startsWith(path.resolve(outputDir) + path.sep) || !/\.docx$/i.test(result.outputFile)) {
          throw new Error("引擎沒有產出有效的 Word 檔案路徑");
        }
        if (!fs.existsSync(result.outputFile)) throw new Error("引擎回報的 Word 檔案不存在");
        resolve({ summary: String(result.summary || "已完成考卷整理"), reviewItems: Array.isArray(result.reviewItems) ? result.reviewItems.map(String) : [], outputFile: result.outputFile });
      } catch (e) { reject(new Error(`無法讀取髒考卷處理結果：${e.message}`)); }
    });
  });
}

// ===== 快速純文字乾淨考卷：不裁圖，圖用〔含圖：…〕佔位，速度優先 =====
function generateCleanExam({ sourcePath, workDir, outputDir }) {
  const engine = (process.env.ENGINE || "claude-code").toLowerCase();
  if (engine === "api") throw new Error("快速乾淨考卷需要可讀檔並產出 Word 的 Claude Code 引擎；請以預設 claude-code 模式啟動。");
  const bin = process.env.CLAUDE_BIN || "claude";
  const skill = path.join(REPO, "exam-to-word");
  const resultPath = path.join(workDir, "result.json");
  const prompt = `你是茲茲文教的考卷清稿助理，現在跑「純文字快速模式」。請先讀 ${path.join(skill, "TEXT-MODE.md")}（規則以它為準），需要時再參考 ${path.join(skill, "SKILL.md")} 的卷頭/橫幅與 ${path.join(skill, "references", "example_build.py")} 的腳本形式、以及 ${path.join(skill, "scripts", "buildlib.py")} 可用的函式（含 note()）。

來源檔案：${sourcePath}
工作資料夾：${workDir}
輸出資料夾：${outputDir}
範本：${path.join(skill, "assets", "template.docx")}

重點（務必遵守，這是速度關鍵）：
- 只重打「文字」與「表格」；**完全不要裁圖、不要找座標、不要做圖片預覽**。
- 每一處原本有圖的位置改用 note("<簡短說明>") 佔位（讓使用者之後自己框選截圖貼上）。
- **不要輸出答案，也不要標正解。**
- 原卷看不清或疑似錯字：保留原文並列入人工確認，不可捏造。

請把 scripts 複製到工作資料夾，寫一支 buildlib 內容腳本並 finish() 組出 Word，存到輸出資料夾。
完成後必須在 ${resultPath} 寫入 UTF-8 JSON（不要只在回答文字中說明）：
{
  "outputFile": "絕對路徑，且必須位於 ${outputDir} 內的 .docx 檔",
  "reviewItems": ["需人工確認事項（推定的橫幅/冊別、疑似錯字、看不清的文字；圖片不用列）"],
  "summary": "完成摘要（含題數）"
}
最後只回覆一句完成狀態。`;
  return new Promise((resolve, reject) => {
    const child = spawn(bin, ["-p", prompt, "--allowedTools", "Read,Write,Edit,Bash"], { cwd: REPO, env: process.env });
    let err = "";
    child.stderr.on("data", (d) => { err += d; });
    child.on("error", (e) => reject(new Error(`無法啟動 Claude Code（${bin}）：${e.message}`)));
    child.on("close", (code) => {
      if (code !== 0) return reject(new Error(`快速乾淨考卷處理失敗（結束碼 ${code}）：${err.slice(0, 500)}`));
      try {
        const result = JSON.parse(fs.readFileSync(resultPath, "utf8"));
        if (!result.outputFile || !path.resolve(result.outputFile).startsWith(path.resolve(outputDir) + path.sep) || !/\.docx$/i.test(result.outputFile)) {
          throw new Error("引擎沒有產出有效的 Word 檔案路徑");
        }
        if (!fs.existsSync(result.outputFile)) throw new Error("引擎回報的 Word 檔案不存在");
        resolve({ summary: String(result.summary || "已完成乾淨考卷"), reviewItems: Array.isArray(result.reviewItems) ? result.reviewItems.map(String) : [], outputFile: result.outputFile });
      } catch (e) { reject(new Error(`無法讀取快速乾淨考卷結果：${e.message}`)); }
    });
  });
}

// ===== 一份考卷一次搞定：Claude 讀圖 → 23 欄 CSV，再由 CSV 直接生乾淨 Word（不二次讀圖，省時間）=====
function generateExamPackage({ sourcePath, workDir, outputDir }) {
  const engine = (process.env.ENGINE || "claude-code").toLowerCase();
  if (engine === "api") throw new Error("此功能需要可讀檔並執行的 Claude Code 引擎；請以預設 claude-code 模式啟動。");
  const bin = process.env.CLAUDE_BIN || "claude";
  const skill = path.join(REPO, "exam-to-word");
  const promptFile = path.join(REPO, "local-app", "qbank-csv-prompt.txt");
  const rules = fs.readFileSync(promptFile, "utf8");
  const csvPath = path.join(outputDir, "題庫23欄.csv");
  const base = path.basename(sourcePath).replace(/\.[^.]+$/, "");
  const docxPath = path.join(outputDir, base + "-乾淨考卷.docx");
  const resultPath = path.join(workDir, "result.json");
  const prompt = `${rules}

========== 執行指示 ==========
考卷來源檔案：${sourcePath}
這是掃描 PDF 或照片（沒有文字層）。請先把它轉成頁面圖片再逐張辨識：
可複製 ${path.join(skill, "scripts")} 的 imgtools.py 到 ${workDir} 後用 prep() 轉圖（300dpi），或用 pdftoppm／pymupdf，圖存在 ${workDir} 內。
逐題辨識（題幹、選項、公式、圖表、表格），依上面規則整理成 23 欄 CSV。
忽略學生手寫作答與老師批改筆跡；被筆跡蓋住看不清處，該題「待確認」欄填 Y，不要猜。

輸出：把完整 23 欄 CSV（含標題列、UTF-8）寫入檔案：${csvPath}
並在 ${resultPath} 寫入 UTF-8 JSON：{"csv":"${csvPath}","summary":"完成摘要（含題數）","reviewItems":["需人工確認事項"]}
最後只回覆一句完成狀態。`;
  return new Promise((resolve, reject) => {
    const child = spawn(bin, ["-p", prompt, "--allowedTools", "Read,Write,Edit,Bash"], { cwd: REPO, env: process.env });
    let err = "";
    child.stderr.on("data", (d) => { err += d; });
    child.on("error", (e) => reject(new Error(`無法啟動 Claude Code（${bin}）：${e.message}`)));
    child.on("close", (code) => {
      if (code !== 0) return reject(new Error(`考卷處理失敗（結束碼 ${code}）：${err.slice(0, 500)}`));
      try {
        let summary = "已完成", reviewItems = [];
        try { const rj = JSON.parse(fs.readFileSync(resultPath, "utf8")); summary = String(rj.summary || summary); reviewItems = Array.isArray(rj.reviewItems) ? rj.reviewItems.map(String) : []; } catch (e) {}
        if (!fs.existsSync(csvPath)) throw new Error("引擎沒有產出 CSV");
        // 補上 UTF-8 BOM，讓 Excel 直接開中文不亂碼
        try { const buf = fs.readFileSync(csvPath); if (!(buf[0] === 0xEF && buf[1] === 0xBB && buf[2] === 0xBF)) fs.writeFileSync(csvPath, Buffer.concat([Buffer.from([0xEF, 0xBB, 0xBF]), buf])); } catch (e) {}
        // 由同一份 CSV 直接生乾淨 Word（不再讓 AI 讀第二次）
        const repl = JSON.stringify([["<w:t>國七生物</w:t>", "<w:t>國中自然</w:t>"], ["Ch1(全)", ""], ["【112.百齡】", ""]]);
        const py = require("child_process").spawnSync("python3", [path.join(REPO, "local-app", "csv_to_word.py"), csvPath, docxPath, repl], { encoding: "utf8" });
        const wordOk = py.status === 0 && fs.existsSync(docxPath);
        if (!wordOk) reviewItems.push("乾淨 Word 產生失敗（CSV 仍可用）：" + String(py.stderr || "").slice(0, 200));
        resolve({ summary, reviewItems, csvPath, docxPath: wordOk ? docxPath : null });
      } catch (e) { reject(new Error(`讀取考卷處理結果失敗：${e.message}`)); }
    });
  });
}

// ===== 拍照/圖片考卷：讓 Claude「看」圖片產詳解（沒有文字層，不能抽字）=====

const IMG_EXTS = [".png", ".jpg", ".jpeg", ".webp", ".gif"];
function imgExt(name) {
  const e = path.extname(name || "").toLowerCase();
  return IMG_EXTS.includes(e) ? e : "";
}
function imgMediaType(img) {
  if (img.mediaType) return img.mediaType;
  const e = imgExt(img.filename);
  return e === ".png" ? "image/png"
    : e === ".webp" ? "image/webp"
    : e === ".gif" ? "image/gif" : "image/jpeg";
}
function imagePrompt() {
  return `${loadRules()}\n\n${OUTPUT_SPEC}\n\n========== 考卷在圖片中 ==========\n請先仔細閱讀圖片裡的每一題（題幹、選項、圖表、公式都要看），再依規範產出 JSON。同一份考卷可能有多張圖（多頁），請合併處理。`;
}
function rmDir(dir) { try { fs.rmSync(dir, { recursive: true, force: true }); } catch (e) {} }

// 引擎一（claude-code）：把圖片寫成暫存檔，prompt 附上路徑讓 Claude Code 讀圖。
function generateFromImagesClaudeCode(images) {
  return new Promise((resolve, reject) => {
    let dir;
    try {
      // 暫存檔放在工作目錄內：headless Claude Code 的檔案沙箱只讓它讀 cwd 內的檔。
      dir = fs.mkdtempSync(path.join(REPO, ".exam-tmp-"));
      const paths = images.map((img, i) => {
        const p = path.join(dir, `page${i + 1}${imgExt(img.filename) || ".png"}`);
        fs.writeFileSync(p, Buffer.from(img.dataBase64, "base64"));
        return p;
      });
      const bin = process.env.CLAUDE_BIN || "claude";
      const prompt = `${imagePrompt()}\n\n圖片檔（請用讀取工具開啟這些檔案來看考卷）：\n${paths.join("\n")}`;
      // --allowedTools Read：授權讀檔工具，否則 headless 模式會停下來要權限、產不出 JSON。
      const child = spawn(bin, ["-p", prompt, "--allowedTools", "Read"], { cwd: REPO, env: process.env });
      let out = "", err = "";
      child.stdout.on("data", (d) => { out += d; });
      child.stderr.on("data", (d) => { err += d; });
      child.on("error", (e) => { rmDir(dir); reject(new Error(
        `無法啟動 Claude Code（${bin}）：${e.message}。請確認本機已安裝 claude CLI，或設 CLAUDE_BIN。`)); });
      child.on("close", (code) => {
        rmDir(dir);
        if (code !== 0 && !out) return reject(new Error(`Claude Code 結束碼 ${code}：${err.slice(0, 500)}`));
        try { resolve(extractJson(out)); }
        catch (e) { reject(new Error(`${e.message}\n引擎原始輸出前 500 字：${out.slice(0, 500)}`)); }
      });
    } catch (e) { if (dir) rmDir(dir); reject(e); }
  });
}

// 引擎二（api）：用 image content blocks 傳給 Claude。
async function generateFromImagesApi(images) {
  const key = process.env.ANTHROPIC_API_KEY;
  if (!key) throw new Error("ENGINE=api 需要環境變數 ANTHROPIC_API_KEY");
  const model = process.env.ANTHROPIC_MODEL || "claude-sonnet-4-5";
  const content = images.map((img) => ({
    type: "image",
    source: { type: "base64", media_type: imgMediaType(img), data: img.dataBase64 },
  }));
  content.push({ type: "text", text: imagePrompt() });
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: { "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01" },
    body: JSON.stringify({ model, max_tokens: 8000, messages: [{ role: "user", content }] }),
  });
  if (!res.ok) throw new Error(`API 錯誤 ${res.status}：${(await res.text()).slice(0, 500)}`);
  const body = await res.json();
  const text = (body.content || []).map((b) => b.text || "").join("");
  return extractJson(text);
}

async function generateFromImages(images) {
  if (!images || !images.length) throw new Error("沒有圖片");
  const engine = (process.env.ENGINE || "claude-code").toLowerCase();
  if (engine === "api") return generateFromImagesApi(images);
  return generateFromImagesClaudeCode(images);
}

module.exports = { generate, generateDirtyExam, generateCleanExam, generateExamPackage, generateFromImages, buildPrompt, extractJson };
