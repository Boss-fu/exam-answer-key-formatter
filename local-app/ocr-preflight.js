// 本機 OCR 試跑：不呼叫 AI。先把掃描頁轉圖、用 Tesseract 讀印刷文字，
// 產出每題候選框與信心分數，讓使用者在匯出前人工覆核。
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

function run(bin, args, label) {
  const child = spawnSync(bin, args, { encoding: "utf8", maxBuffer: 32 * 1024 * 1024 });
  if (child.error) throw new Error(`${label} 無法啟動：${child.error.message}`);
  if (child.status !== 0) throw new Error(`${label} 失敗：${String(child.stderr || "").slice(0, 400)}`);
  return child.stdout || "";
}

function parseTsv(tsv) {
  const rows = String(tsv).trim().split(/\r?\n/).slice(1).map((line) => line.split("\t"));
  return rows.map((p) => ({
    level: Number(p[0]), page: Number(p[1]), left: Number(p[6]), top: Number(p[7]),
    width: Number(p[8]), height: Number(p[9]), confidence: Number(p[10]), text: (p[11] || "").trim(),
  })).filter((r) => Number.isFinite(r.left) && Number.isFinite(r.top));
}

function questionAnchors(words, pageWidth, pageHeight) {
  return words.filter((w) => {
    const t = w.text.replace(/[（(]/g, "");
    const m = t.match(/^0?([1-9]\d?)[\.、\)]?$/);
    // 題號通常在欄的左緣；避開算式裡的數字。
    return Boolean(m) && w.confidence >= 25 && w.top > pageHeight * 0.1
      && (w.left < pageWidth * 0.23 || Math.abs(w.left - pageWidth * 0.5) < pageWidth * 0.12);
  }).map((w) => ({ ...w, no: Number(w.text.replace(/\D/g, "")) }));
}

function buildQuestions(words, width, height) {
  const anchors = questionAnchors(words, width, height).sort((a, b) => a.top - b.top || a.left - b.left);
  const unique = []; const seen = new Set();
  for (const a of anchors) {
    // 同一題號可能被 OCR 重複辨識，保留最左、最高信心的一個。
    const key = `${a.no}:${Math.round(a.top / 80)}`;
    if (!seen.has(key)) { unique.push(a); seen.add(key); }
  }
  return unique.map((a, index) => {
    const columnStart = a.left < width * 0.42 ? 0 : Math.round(width * 0.44);
    const columnEnd = a.left < width * 0.42 ? Math.round(width * 0.56) : width;
    const next = unique.slice(index + 1).find((n) => (n.left < width * 0.42) === (a.left < width * 0.42) && n.top > a.top);
    const bottom = next ? Math.max(a.top + 120, next.top - 12) : height - 40;
    const inBox = words.filter((w) => w.left >= columnStart && w.left < columnEnd && w.top >= a.top - 8 && w.top < bottom);
    const text = inBox.sort((x, y) => x.top - y.top || x.left - y.left).map((w) => w.text).filter(Boolean).join(" ");
    const scores = inBox.map((w) => w.confidence).filter((c) => c >= 0);
    const confidence = scores.length ? Math.round(scores.reduce((s, c) => s + c, 0) / scores.length) : 0;
    return {
      id: `q${String(a.no).padStart(2, "0")}-${index + 1}`, no: a.no,
      rect: { left: columnStart, top: Math.max(0, a.top - 15), width: columnEnd - columnStart, height: Math.max(120, bottom - a.top + 15) },
      // 自動 OCR 永遠不直接視為已驗收；綠色只能由使用者按「確認此題」取得。
      text, confidence, status: confidence >= 50 ? "review" : "attention",
    };
  });
}

function checkTools() {
  const tesseract = process.env.TESSERACT_BIN || "tesseract";
  run(tesseract, ["--version"], "Tesseract");
  return tesseract;
}

function preflight({ sourcePath, jobDir }) {
  const tesseract = checkTools();
  const pagesDir = path.join(jobDir, "pages");
  fs.mkdirSync(pagesDir, { recursive: true });
  const ext = path.extname(sourcePath).toLowerCase();
  if (ext === ".pdf") {
    run(process.env.PDFTOPPM_BIN || "pdftoppm", ["-r", "250", "-png", sourcePath, path.join(pagesDir, "page")], "PDF 轉圖工具 pdftoppm");
  } else {
    fs.copyFileSync(sourcePath, path.join(pagesDir, "page-1" + ext));
  }
  const images = fs.readdirSync(pagesDir).filter((n) => /\.(png|jpe?g)$/i.test(n)).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  if (!images.length) throw new Error("PDF 沒有成功轉成頁面圖片");
  const pages = images.map((name, index) => {
    const image = path.join(pagesDir, name);
    // macOS 的 /tmp 是指向 /private/tmp 的連結；Leptonica 在部分版本無法以連結路徑開圖。
    const tsv = run(tesseract, [fs.realpathSync(image), "stdout", "-l", "chi_tra+eng", "--psm", "3", "tsv"], `第 ${index + 1} 頁 OCR`);
    const rows = parseTsv(tsv); const page = rows.find((r) => r.level === 1) || {};
    const words = rows.filter((r) => r.level === 5 && r.text);
    const width = page.width || 1, height = page.height || 1;
    const confidences = words.map((w) => w.confidence).filter((c) => c >= 0);
    const confidence = confidences.length ? Math.round(confidences.reduce((s, c) => s + c, 0) / confidences.length) : 0;
    const questions = buildQuestions(words, width, height);
    return { page: index + 1, image: `pages/${name}`, width, height, wordCount: words.length, confidence, questions,
      status: !words.length ? "attention" : confidence >= 70 && questions.length ? "ok" : "review" };
  });
  const questions = pages.flatMap((p) => p.questions.map((q) => ({ ...q, page: p.page })));
  return {
    version: 1, mode: "local-tesseract", createdAt: new Date().toISOString(),
    summary: { pageCount: pages.length, questionCandidates: questions.length, reviewCount: questions.filter((q) => q.status !== "ok").length },
    pages, questions, corrections: {},
  };
}

module.exports = { preflight };

if (require.main === module) {
  const [sourcePath, jobDir] = process.argv.slice(2);
  if (!sourcePath || !jobDir) {
    console.error("用法：node local-app/ocr-preflight.js <考卷.pdf> <輸出資料夾>");
    process.exitCode = 2;
  } else {
    try {
      const report = preflight({ sourcePath: path.resolve(sourcePath), jobDir: path.resolve(jobDir) });
      const reportPath = path.join(path.resolve(jobDir), "report.json");
      fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
      console.log(JSON.stringify({ reportPath, summary: report.summary }, null, 2));
    } catch (error) {
      console.error(error.message);
      process.exitCode = 1;
    }
  }
}
