const { test } = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const { apiUrl, request, normalizeBase } = require("../public/api-client");
const config = require("../public/server-config");
const { plan } = require("../tailnet-https");
const browser = { location: { protocol: "https:" } };

test("all API, image and download URLs point directly to the selected backend", () => {
  for (const target of Object.values(config.servers)) {
    for (const path of ["/api/ocr-preflight", "/api/ocr-review", "/api/ocr-page?job=abc&image=page-1.png", "/api/exam-package/download?job=abc&type=docx"]) {
      assert.equal(apiUrl(path, target, browser), target.baseUrl + path);
    }
  }
  assert.throws(() => apiUrl("https://example.com/api/ocr-preflight", config.servers.mac, browser));
  assert.throws(() => apiUrl("/api/../private", config.servers.mac, browser));
  assert.throws(() => normalizeBase("http://100.75.52.69:8787", "https:"), /HTTPS/);
  assert.throws(() => normalizeBase("https://user:pass@host/", "https:"));
  assert.throws(() => normalizeBase("https://host/secret", "https:"));
});

test("file previews never submit jobs", async () => {
  await assert.rejects(request("/api/ocr-preflight", {}, config.servers.mac, {
    location: { protocol: "file:" }, fetch: () => assert.fail("must not fetch"),
  }), /HTML 預覽.*start.command.*localhost:8787/);
});

test("network failure names selected host and does not claim missing dependencies", async () => {
  await assert.rejects(request("/api/ocr-preflight", {}, config.servers.company, {
    ...browser, fetch: async () => { throw new TypeError("Load failed"); },
  }), (error) => /無法連線至公司電腦/.test(error.message) && !/請確認主機已安裝/.test(error.message));
});

test("dependency errors, old server 404s and unexpected HTML remain distinct", async () => {
  const env = (body, status, type) => ({ ...browser, fetch: async () => new Response(body, { status, headers: { "content-type": type } }) });
  await assert.rejects(request("/api/ocr-preflight", {}, config.servers.mac, env(JSON.stringify({ error: "Tesseract 無法啟動：ENOENT" }), 500, "application/json")), /HTTP 500.*Tesseract 無法啟動/);
  await assert.rejects(request("/api/ocr-preflight", {}, config.servers.company, env("Not found", 404, "text/plain")), /HTTP 404.*版本/);
  await assert.rejects(request("/api/ocr-preflight", {}, config.servers.mac, env("<html></html>", 200, "text/html")), /回傳網頁/);
});

function listen(server) {
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve("http://127.0.0.1:" + server.address().port)));
}
function close(server) {
  server.closeAllConnections();
  return new Promise((resolve) => server.close(resolve));
}

test("two independent backend requests never need a central proxy", async (t) => {
  const seen = [];
  const make = (name) => http.createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    seen.push({ name, path: req.url, method: req.method, body: Buffer.concat(chunks).toString() });
    res.writeHead(200, { "content-type": "application/octet-stream", "content-disposition": "attachment; filename=test.bin" });
    res.end(Buffer.from([0, 127, 255]));
  });
  const mac = make("mac"), company = make("company");
  const targets = { mac: { label: "Mac", baseUrl: await listen(mac) }, company: { label: "公司", baseUrl: await listen(company) } };
  t.after(() => close(mac)); t.after(() => close(company));
  const env = { location: { protocol: "http:" }, fetch };
  const jobTarget = { ...targets.mac };
  for (const name of ["mac", "company"]) {
    for (const path of ["/api/ocr-preflight", "/api/ocr-review", "/api/ocr-page?job=abc&image=page-1.png", "/api/exam-package/download?job=abc&type=docx"]) {
      const options = path.includes("?") ? {} : { method: "POST", headers: { "content-type": "application/json" }, body: '{"test":true}' };
      const response = await request(path, options, targets[name], env);
      assert.deepEqual(Buffer.from(await response.arrayBuffer()), Buffer.from([0, 127, 255]));
      assert.equal(response.headers.get("content-disposition"), "attachment; filename=test.bin");
      assert.deepEqual(seen.at(-1), { name, path, method: options.method || "GET", body: options.body || "" });
    }
  }
  await request("/api/ocr-review", { method: "POST", body: "{}" }, jobTarget, env);
  assert.equal(seen.at(-1).name, "mac");
});

test("actual server permits only approved portal origins, includes CORS on errors and exposes download names", async (t) => {
  const app = require("../server"), origin = await listen(app);
  t.after(() => close(app));
  const portal = config.portalOrigins[0];
  let response = await fetch(origin + "/api/health", { headers: { origin: portal } });
  assert.equal(response.headers.get("access-control-allow-origin"), portal);
  assert.match(response.headers.get("access-control-expose-headers"), /content-disposition/);
  const health = await response.json();
  assert.equal(health.service, config.service); assert.equal(health.apiVersion, config.apiVersion);
  response = await fetch(origin + "/api/ocr-preflight", { method: "OPTIONS", headers: { origin: portal, "access-control-request-method": "POST", "access-control-request-headers": "content-type", "access-control-request-private-network": "true" } });
  assert.equal(response.status, 204); assert.equal(response.headers.get("access-control-allow-private-network"), "true");
  response = await fetch(origin + "/api/ocr-preflight", { method: "POST", headers: { origin: portal, "content-type": "application/json" }, body: "{}" });
  assert.equal(response.status, 400); assert.equal(response.headers.get("access-control-allow-origin"), portal);
  for (const evil of ["https://evil.example", "https://tzutzu-ocr.vercel.app.evil.example", "null"]) {
    response = await fetch(origin + "/api/ocr-preflight", { method: "POST", headers: { origin: evil, "content-type": "application/json" }, body: "{}" });
    assert.equal(response.status, 403); assert.equal(response.headers.get("access-control-allow-origin"), null);
  }
  assert.equal((await fetch(origin + "/api/ocr-preflight", { method: "POST", body: "{}" })).status, 415);
  assert.equal((await fetch(origin + "/server-config.js")).status, 200);
  assert.equal((await fetch(origin + "/api-client.js")).status, 200);
});

test("HTTPS setup does not replace occupied ports or expose a Funnel", () => {
  const target = "http://127.0.0.1:8787";
  assert.equal(plan({}, "mac.ts.net", target), "create");
  assert.equal(plan({ TCP: { 8443: { HTTPS: true } }, Web: { "mac.ts.net:8443": { Handlers: { "/": { Proxy: target } } } } }, "mac.ts.net", target), "ready");
  assert.equal(plan({ TCP: { 8443: { HTTPS: true } } }, "mac.ts.net", target), "conflict");
  assert.equal(plan({ AllowFunnel: { "mac.ts.net:443": true } }, "mac.ts.net", target), "conflict");
});
