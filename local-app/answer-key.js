const fs = require('fs');
const path = require('path');
const { promisify } = require('util');
const execFile = promisify(require('child_process').execFile);

function inputError(message) {
  return Object.assign(new Error(message), { statusCode: 400 });
}

// No I/O: PDF text is measured after removing all whitespace.
function routeAnswerKey(filename, text = '', pages = 0) {
  const ext = path.extname(String(filename || '')).toLowerCase();
  if (['.png', '.jpg', '.jpeg'].includes(ext)) return 'images';
  if (['.docx', '.txt'].includes(ext)) return 'text';
  if (ext !== '.pdf') throw inputError('只接受 PDF、Word（.docx）、TXT、PNG 或 JPG 檔案。');
  if (pages > 12 && String(text).replace(/\s/g, '').length >= 200) return 'text';
  if (pages > 12) throw inputError('掃描 PDF 超過 12 頁，請分批上傳，每份最多 12 頁。');
  return 'images';
}

async function createAnswerKey({ filename, dataBase64, officialAnswers }, output, deps) {
  routeAnswerKey(filename);
  if (typeof dataBase64 !== 'string' || !dataBase64 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(dataBase64)) {
    throw inputError('缺少考卷檔案或檔案資料不完整，請重新上傳。');
  }
  const buffer = Buffer.from(dataBase64, 'base64');
  const ext = path.extname(filename).toLowerCase();
  let text = '', pages = 1;
  if (['.pdf', '.docx', '.txt'].includes(ext)) {
    try { text = await deps.extractText(filename, buffer); }
    catch (error) {
      // extractText deliberately rejects empty scanned PDFs; keep its other failures.
      if (ext !== '.pdf' || !String(error.message).startsWith('這個 PDF 讀不到文字')) throw new Error('讀取考卷失敗，請確認檔案未損壞，且公司電腦已安裝 PDF 文字擷取工具。');
    }
  }
  let source = routeAnswerKey(filename, text);
  let data, ensemble;
  async function solve(source, value) {
    if (!deps.runEnsemble) return source === 'text' ? deps.generate(value) : deps.generateFromImages(value);
    const result = await deps.runEnsemble({ source, ...(source === 'text' ? { text: value } : { images: value }), officialAnswers });
    ensemble = result.ensemble;
    return result.data;
  }
  if (source === 'text') {
    if (!text.trim()) throw inputError('考卷沒有可讀取的文字，請確認檔案內容。');
    pages = null; // Text extraction does not expose pagination.
    data = await solve('text', text);
  } else if (ext !== '.pdf') {
    data = await solve('images', [{ filename, dataBase64 }]);
  } else {
    const dir = fs.mkdtempSync(path.join(output, 'answer-key-'));
    try {
      const input = path.join(dir, 'source.pdf');
      fs.writeFileSync(input, buffer);
      try {
        // Render at most 13 pages: the extra page detects oversized uploads.
        await (deps.execFile || execFile)(process.env.PDFTOPPM_BIN || 'pdftoppm', ['-r', '200', '-png', '-f', '1', '-l', '13', input, path.join(dir, 'page')], { timeout: 180000, maxBuffer: 4 * 1024 * 1024 });
      } catch { throw new Error('PDF 轉圖片失敗，請確認檔案可開啟，且公司電腦已安裝 PDF 轉圖工具。'); }
      const files = fs.readdirSync(dir).filter(name => /^page-\d+\.png$/.test(name)).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
      pages = files.length;
      source = routeAnswerKey(filename, text, pages);
      if (!pages) throw inputError('PDF 沒有可讀取的頁面，請重新選擇考卷。');
      if (source === 'text') data = await solve('text', text);
      else data = await solve('images', files.map(name => ({ filename: name, dataBase64: fs.readFileSync(path.join(dir, name)).toString('base64') })));
    } finally { fs.rmSync(dir, { recursive: true, force: true }); }
  }
  return { ok: true, source, pages, data, ...(ensemble ? { ensemble } : {}), mismatches: deps.checkAnswers(data.questions) };
}

module.exports = { routeAnswerKey, createAnswerKey };
