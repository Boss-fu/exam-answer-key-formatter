// 直接以 server handler 開一個暫時埠；不啟動 Python 或完整轉檔。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { EventEmitter } = require("node:events");
const path = require("node:path");
const server = require("../server");

const id = "abcdef012345abcdef012345";
const root = path.join(__dirname, "..", "output", "py-ocr", id, "result");
function get(route) {
  return new Promise((resolve) => {
    const req = new EventEmitter(); req.method = "GET"; req.url = route; req.headers = { host: "localhost" };
    const chunks = []; const res = { headers: {}, writeHead(status, headers) { this.status = status; this.headers = headers || {}; }, end(body) { if (body) chunks.push(Buffer.from(body)); resolve({status:this.status,body:Buffer.concat(chunks),type:this.headers["content-type"]}); } };
    server.emit("request", req, res);
  });
}
(async () => {
  fs.mkdirSync(path.join(root, "previews"), { recursive: true });
  fs.writeFileSync(path.join(root, "progress.jsonl"), JSON.stringify({t:1,stage:"render",page:1,pages:1,msg:"ok",img:"previews/p1_orig.jpg"}) + "\n");
  fs.writeFileSync(path.join(root, "previews", "p1_orig.jpg"), Buffer.from([0xff,0xd8,0xff,0xd9]));
  fs.writeFileSync(path.join(root, "..", "state.json"), JSON.stringify({status:"running"}));
  try {
    const progress = await get(`/api/py-ocr/progress?job=${id}&since=0`);
    assert.equal(progress.status, 200); assert.equal(JSON.parse(progress.body).events.length, 1);
    const preview = await get(`/api/py-ocr/preview?job=${id}&page=1&kind=orig`);
    assert.equal(preview.status, 200); assert.match(preview.type, /image\/jpeg/);
    assert.equal((await get("/api/py-ocr/preview?job=../bad&page=1&kind=orig")).status, 400);
    console.log("PASS: 即時工作路由與預覽路徑限制");
  } finally { fs.rmSync(path.join(__dirname, "..", "output", "py-ocr", id), {recursive:true,force:true}); }
})().catch((err) => { console.error(err); process.exitCode = 1; });
