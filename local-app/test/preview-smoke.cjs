// 本機 OCR 舊工作預覽 smoke test：不建立 OCR 工作，不跑 eval_gt。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { PassThrough } = require("node:stream");
const vm = require("node:vm");

const app = path.resolve(__dirname, "..");
function networkRequest(port, method, pathname, body) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port, method, path: pathname, headers: body ? { "content-type": "application/json", "content-length": Buffer.byteLength(body) } : {} }, (res) => {
      const chunks=[];res.on("data", (chunk) => chunks.push(chunk));res.on("end", () => resolve({ status: res.statusCode, body: Buffer.concat(chunks) }));
    });req.on("error", reject);if(body)req.write(body);req.end();
  });
}
function directRequest(server, method, pathname, body) {
  return new Promise((resolve, reject) => {
    const req=new PassThrough(),res=new PassThrough(),chunks=[];
    req.method=method;req.url=pathname;req.headers=body?{"content-type":"application/json"}:{};
    res.statusCode=200;res.headers={};res.setHeader=(name,value)=>{res.headers[name.toLowerCase()]=value;};
    res.writeHead=(code,headers={})=>{res.statusCode=code;Object.assign(res.headers,headers);};
    res.on("data",(chunk)=>chunks.push(chunk));res.on("end",()=>resolve({status:res.statusCode,body:Buffer.concat(chunks)}));res.on("error",reject);
    server.emit("request",req,res);req.end(body);
  });
}
function listen(server) {
  return new Promise((resolve,reject) => {
    server.once("error",reject);server.listen(0,"127.0.0.1",()=>{server.off("error",reject);resolve(server.address().port);});
  });
}
async function main() {
  const script=fs.readFileSync(path.join(app,"public/index.html"),"utf8").match(/<script>([\s\S]*)<\/script>/i)[1];
  new vm.Script(script, { filename: "index.html inline script" });
  const server=require("../server");
  let request,usedDirectHandler=false;
  try {
    try { const port=await listen(server);request=(method,url,body)=>networkRequest(port,method,url,body); }
    catch (error) { if (error.code !== "EPERM") throw error; usedDirectHandler=true;request=(method,url,body)=>directRequest(server,method,url,body); }
    const listed=await request("POST","/api/label/list","{}");assert.equal(listed.status,200);const jobs=JSON.parse(listed.body);assert.ok(jobs.ok&&jobs.jobs.length,"no py-ocr jobs");
    const job=jobs.jobs[0].id;const page=await request("POST","/api/py-ocr/job-page",JSON.stringify({job,page:1,view:"original"}));assert.equal(page.status,200);assert.ok(JSON.parse(page.body).image,"job page has no image");
    const meta=await request("POST","/api/py-ocr/job",JSON.stringify({job}));assert.equal(meta.status,200);const urls=JSON.parse(meta.body);const url=[urls.docxUrl,urls.txtUrl,urls.reviewUrl].find(Boolean);assert.ok(url,"no downloadable artifact");
    const download=await request("GET",url);assert.equal(download.status,200);assert.ok(download.body.length>0,"empty download");
    const invalid=await request("GET","/api/py-ocr/download?job=../bad&kind=docx");assert.equal(invalid.status,400);
    console.log(`PASS: ${usedDirectHandler?"direct handler":"HTTP"}, job=${job}, page image, download ${url}`);
  } finally { if (server.listening) server.close(); }
}
main().catch((error)=>{console.error(error);process.exitCode=1;});
