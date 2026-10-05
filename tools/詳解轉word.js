#!/usr/bin/env node
/**
 * 將其他 AI 回覆中的 JSON 擷取出來，交給既有 code/build_from_json.js 排版。
 * Usage: node tools/詳解轉word.js <AI回覆.txt或.json> <輸出.docx>
 */
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const [, , inputPath, outputPath] = process.argv;
if (!inputPath || !outputPath) {
  console.error("用法：node tools/詳解轉word.js <AI回覆.txt或.json> <輸出.docx>");
  process.exit(1);
}

function jsonObjectsIn(text) {
  const objects = [];
  for (let start = 0; start < text.length; start += 1) {
    if (text[start] !== "{") continue;
    let quote = false;
    let escaped = false;
    let depth = 0;
    for (let end = start; end < text.length; end += 1) {
      const char = text[end];
      if (quote) {
        if (escaped) escaped = false;
        else if (char === "\\") escaped = true;
        else if (char === '"') quote = false;
        continue;
      }
      if (char === '"') quote = true;
      else if (char === "{") depth += 1;
      else if (char === "}") {
        depth -= 1;
        if (depth === 0) {
          try { objects.push(JSON.parse(text.slice(start, end + 1))); } catch (_) { /* 繼續找下一段 */ }
          break;
        }
      }
    }
  }
  return objects;
}

function validate(data) {
  if (!data || !Array.isArray(data.questions) || data.questions.length === 0) {
    throw new Error("找不到含 questions 陣列的 JSON。請確認 AI 回覆有完整輸出。");
  }
  data.questions.forEach((question, index) => {
    const where = `第${index + 1}筆`;
    if (!Number.isInteger(question.no) || question.no < 1) throw new Error(`${where} 的 no 必須是正整數。`);
    if (typeof question.answer !== "string" || !question.answer.trim()) throw new Error(`${where} 缺少 answer。`);
    if (typeof question.detail !== "string" || !question.detail.trim()) throw new Error(`${where} 缺少 detail。`);
    if (!Array.isArray(question.options) || question.options.length === 0) throw new Error(`${where} 缺少 options。`);
    const correct = question.options.filter((option) => option && /^正確/.test(option.text || ""));
    if (correct.length !== 1 || correct[0].label !== question.answer) {
      throw new Error(`${where} 的 answer 與以「正確」開頭的選項不一致。`);
    }
  });
}

try {
  const source = fs.readFileSync(inputPath, "utf8").replace(/^\uFEFF/, "");
  const candidates = jsonObjectsIn(source).filter((item) => Array.isArray(item.questions));
  if (!candidates.length) throw new Error("找不到 JSON 物件。請把 AI 回覆完整存成 UTF-8 文字檔。");
  const data = candidates[0];
  validate(data);

  const tempPath = path.join(path.dirname(path.resolve(outputPath)), `.詳解轉word-${process.pid}.json`);
  fs.writeFileSync(tempPath, `${JSON.stringify(data, null, 2)}\n`, "utf8");
  const builder = path.resolve(__dirname, "../code/build_from_json.js");
  const result = spawnSync(process.execPath, [builder, tempPath, outputPath], { stdio: "inherit" });
  fs.unlinkSync(tempPath);
  if (result.status !== 0) process.exit(result.status || 1);
} catch (error) {
  console.error(`轉換失敗：${error.message}`);
  process.exit(2);
}
