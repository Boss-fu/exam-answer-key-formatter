// 後端橋接：呼叫純 Python 管線 ocr/pyexam.py（PyMuPDF+OpenCV+Tesseract+python-docx）。
// 完全不經 Claude、不花 token。主機需先安裝 Python 套件（見 ocr/README）。
const fs = require("fs");
const path = require("path");
const { execFile } = require("child_process");

const SCRIPT = path.join(__dirname, "..", "ocr", "pyexam.py");
const REPO = path.join(__dirname, "..");

function pyBin() { return process.env.PY_BIN || "python3"; }

// 跑 pyexam.py：source PDF/圖 → outDir 內產 .txt / .docx / *_fig*.png。
// 回傳 { text, txtFile, docxFile, figures[] }（檔名為 outDir 內相對名）。
function runPyOcr({ sourcePath, outDir, dpi = 350, timeout = 300000 }) {
  return new Promise((resolve, reject) => {
    fs.mkdirSync(outDir, { recursive: true });
    const args = [SCRIPT, sourcePath, "-o", outDir, "--dpi", String(dpi)];
    execFile(pyBin(), args, { cwd: REPO, timeout, maxBuffer: 16 * 1024 * 1024 }, (err, stdout, stderr) => {
      if (err) {
        if (err.code === "ENOENT") {
          return reject(new Error("主機找不到 python3（或 PY_BIN 指定的直譯器）。請先安裝 Python 與套件，見 ocr/README。"));
        }
        if (err.killed) return reject(new Error("純 Python OCR 逾時（檔案過大或頁數過多）。"));
        return reject(new Error("純 Python OCR 執行失敗：" + String(stderr || err.message).slice(0, 500)));
      }
      let files;
      try { files = fs.readdirSync(outDir); } catch { files = []; }
      const txtFile = files.find((f) => f.toLowerCase().endsWith(".txt")) || null;
      const docxFile = files.find((f) => f.toLowerCase().endsWith(".docx")) || null;
      const figures = files.filter((f) => /_fig\d+\.png$/i.test(f)).sort();
      let text = "";
      if (txtFile) { try { text = fs.readFileSync(path.join(outDir, txtFile), "utf8"); } catch {} }
      resolve({ text, txtFile, docxFile, figures, log: String(stdout || "").trim() });
    });
  });
}

module.exports = { runPyOcr };
