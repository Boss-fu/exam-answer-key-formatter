#!/usr/bin/env node
// 用茲茲「課前考 Word 範本」(science-qbank/templates/pre_tpl.docx) 填出合規考卷。
// 這是「我們的格式化工具」：內容(sections/items)可來自本地 OCR 或 AI，格式一律走你們的範本，
// 與 index.html 的 downloadWordViaTemplate('pre') 同一套 docxtemplater 契約
// (paragraphLoop:true, linebreaks:true；題號與大題「一、」由範本原生清單自動編號)。
//
// 用法：node ocr/fill_prequiz.js <context.json> <pre_tpl.docx> <out.docx>
// context.json 結構：
// { subject, book, quiz_label, running_header, chapter, unit, ver,
//   sections:[ { title:"單選題　/每題 4 分，共 80 分",
//                items:[ { no:1, text:"題幹\n(A)…\n(B)…", imgs:[], passage:null } ] } ] }
const fs = require("fs");
const PizZip = require("pizzip");
const Docxtemplater = require("docxtemplater");

function main() {
  const [ctxPath, tplPath, outPath] = process.argv.slice(2);
  if (!ctxPath || !tplPath || !outPath) {
    console.error("用法: node ocr/fill_prequiz.js <context.json> <pre_tpl.docx> <out.docx>");
    process.exit(2);
  }
  const ctx = JSON.parse(fs.readFileSync(ctxPath, "utf8"));
  // 預設值（與網站一致）
  ctx.running_header = ctx.running_header || "福大自然 課前考";
  ctx.unit = ctx.unit || "";
  ctx.ver = ctx.ver || "";
  (ctx.sections || []).forEach((s) => (s.items || []).forEach((it) => {
    it.imgs = it.imgs || [];
    if (it.passage === undefined) it.passage = null;
  }));
  const zip = new PizZip(fs.readFileSync(tplPath));
  const doc = new Docxtemplater(zip, { paragraphLoop: true, linebreaks: true });
  doc.render(ctx);
  const outZip = doc.getZip();
  // 配分已填進大題標題時，移除範本手填佔位「  /每題＿＿分，共＿＿分」（與網站 downloadWordViaTemplate 一致）。
  const scoring = (ctx.sections || []).some((s) => /\/每題\s*\d/.test(s.title || ""));
  if (scoring) {
    const docXmlPath = "word/document.xml";
    const xml = outZip.file(docXmlPath).asText().split("  /每題＿＿分，共＿＿分").join("");
    outZip.file(docXmlPath, xml);
  }
  fs.writeFileSync(outPath, outZip.generate({ type: "nodebuffer", compression: "DEFLATE" }));
  const n = (ctx.sections || []).reduce((a, s) => a + (s.items || []).length, 0);
  console.log(`✅ 已輸出 ${outPath}（${(ctx.sections || []).length} 大題、${n} 題）`);
}
main();
