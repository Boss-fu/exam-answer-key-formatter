// 後端橋接：掃描考卷 → 茲茲格式 Word（ocr/exam_to_word.py）。
// Python 做 OCR、分欄、裁圖、表格、組 Word（0 token）；AI 只判斷一次題目邊界與錯字（預設 Claude → Codex 接力，約 6k token），
// AI 都不能用時自動改用規則，照樣出 Word。主機需先準備 Python 環境：bash ocr/setup_env.sh（見 docs/OCR交接_Codex.md）。
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFile } = require("child_process");

const SCRIPT = path.join(__dirname, "..", "ocr", "exam_to_word.py");
const REPO = path.join(__dirname, "..");

// Python：PY_BIN 優先；再找 ocr/setup_env.sh 建的虛擬環境；最後才用系統 python3（多半沒裝 OCR 套件）
function pyBin() {
  if (process.env.PY_BIN) return process.env.PY_BIN;
  const venv = path.join(os.homedir(), ".venvs", "zizi-exam");
  for (const p of [path.join(venv, "bin", "python"), path.join(venv, "Scripts", "python.exe")]) {
    if (fs.existsSync(p)) return p;
  }
  return process.platform === "win32" ? "python" : "python3";
}

// 回傳 { text, txtFile, docxFile, reviewFile, figures[], stats, log }（檔名為 outDir 內相對路徑）。
function runPyOcr({ sourcePath, outDir, dpi = 350, timeout = 900000, progress = false, skipClean = false }) {
  return new Promise((resolve, reject) => {
    fs.mkdirSync(outDir, { recursive: true });
    const args = [SCRIPT, sourcePath, "-o", outDir, "--dpi", String(dpi)];
    const env = { ...process.env, OCR_SKIP_CLEAN: skipClean ? "1" : "0", ...(progress ? { OCR_PROGRESS: "1" } : {}) };
    execFile(pyBin(), args, { cwd: REPO, env, timeout, maxBuffer: 16 * 1024 * 1024 }, (err, stdout, stderr) => {
      if (err) {
        if (err.code === "ENOENT") {
          return reject(new Error("主機找不到 Python（或 PY_BIN 指定的直譯器）。請在主機執行 bash ocr/setup_env.sh 準備環境。"));
        }
        if (err.killed) return reject(new Error("轉檔逾時（檔案過大或頁數過多）。"));
        return reject(new Error("轉檔失敗：" + String(stderr || err.message).slice(-500)));
      }
      const out = String(stdout || "").trim().split("\n");
      let stats = {};
      try { stats = JSON.parse(out[out.length - 1]); } catch {}
      let files;
      try { files = fs.readdirSync(outDir); } catch { files = []; }
      const txtFile = files.find((f) => f.toLowerCase().endsWith(".txt")) || null;
      const figures = files.filter((f) => /_fig\d+\.png$/i.test(f)).sort();
      const rel = (p) => (p ? path.relative(outDir, p) : null);
      let text = "";
      if (txtFile) { try { text = fs.readFileSync(path.join(outDir, txtFile), "utf8"); } catch {} }
      resolve({ text, txtFile, docxFile: rel(stats.docx), reviewFile: rel(stats.review), figures, stats,
        log: out.slice(0, -1).join("\n") });
    });
  });
}

module.exports = { runPyOcr, pyBin };
