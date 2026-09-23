// 從上傳的檔案取出純文字：PDF（文字型）、Word(.docx)、純文字(.txt/.md)。
// 「乾淨考卷」= 已打字排版的檔案，取字準確；掃描成圖片的 PDF 沒有文字層，取不到字（會提示改用 exam-to-word）。
const path = require("path");
const mammoth = require("mammoth");
// 直接指向 lib，避開 pdf-parse 進入點在無 module.parent 時會讀測試檔的怪癖。
const pdfParse = require("pdf-parse/lib/pdf-parse.js");

async function extractText(filename, buffer) {
  const ext = path.extname(filename || "").toLowerCase();

  if (ext === ".docx") {
    const { value } = await mammoth.extractRawText({ buffer });
    return normalize(value);
  }
  if (ext === ".doc") {
    throw new Error("舊版 .doc 不支援，請在 Word 另存成 .docx 再上傳。");
  }
  if (ext === ".pdf") {
    const data = await pdfParse(buffer);
    const text = normalize(data.text);
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

// 收斂空白：去掉多餘空行、行尾空白，統一換行。
function normalize(s) {
  return String(s || "")
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

module.exports = { extractText };
