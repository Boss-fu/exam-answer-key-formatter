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

module.exports = { generate, generateDirtyExam, buildPrompt, extractJson };
