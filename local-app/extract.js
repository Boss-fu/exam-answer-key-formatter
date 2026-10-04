// 從上傳的檔案取出純文字：PDF（文字型）、Word(.docx)、純文字(.txt/.md)。
// 「乾淨考卷」= 已打字排版的檔案，取字準確；掃描成圖片的 PDF 沒有文字層，取不到字（會提示改用 exam-to-word）。
const path = require("path");
const fs = require("fs");
const os = require("os");
const { spawnSync } = require("child_process");

async function extractText(filename, buffer) {
  const ext = path.extname(filename || "").toLowerCase();

  if (ext === ".docx") {
    // Mammoth 在部分新版 Node 上載入較慢；只在使用者真的上傳 DOCX 時載入。
    const mammoth = require("mammoth");
    const { value } = await mammoth.extractRawText({ buffer });
    return normalize(value);
  }
  if (ext === ".doc") {
    throw new Error("舊版 .doc 不支援，請在 Word 另存成 .docx 再上傳。");
  }
  if (ext === ".pdf") {
    const text = extractPdfText(buffer);
    if (!text) {
      throw new Error("這個 PDF 讀不到文字（可能是掃描成圖片的）。請改用文字型 PDF，或先用 exam-to-word 技能轉成文字。");
    }
    return text;
  }
  if (ext === ".txt" || ext === ".md" || ext === "") {
    return normalize(buffer.toString("utf8"));
  }
  throw new Error(`不支援的檔案類型：${ext}。支援 .pdf / .docx / .txt`);
}

// 以 Poppler pdftotext 取代已停止相容新版 Node 的 pdf-parse。
// OCR 流程同樣需要 Poppler，使用者只需維護一套 PDF 工具鏈。
function extractPdfText(buffer) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "tzutzu-pdf-"));
  const input = path.join(dir, "input.pdf");
  try {
    fs.writeFileSync(input, buffer);
    const bin = process.env.PDFTOTEXT_BIN || "pdftotext";
    const result = spawnSync(bin, ["-layout", input, "-"], { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
    if (result.error) throw new Error(`無法啟動 pdftotext：${result.error.message}`);
    if (result.status !== 0) throw new Error(String(result.stderr || "PDF 文字擷取失敗").trim());
    return normalize(result.stdout);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

// 收斂空白：去掉多餘空行、行尾空白，統一換行。
function normalize(s) {
  return String(s || "")
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

module.exports = { extractText };
