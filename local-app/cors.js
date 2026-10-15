const config = require("./public/server-config");

function allowedOrigins() {
  return new Set([
    ...config.portalOrigins,
    ...Object.values(config.servers).map((server) => server.baseUrl),
    "http://localhost:8787", "http://127.0.0.1:8787", "http://[::1]:8787",
    "http://100.98.1.41:8787", "http://100.75.52.69:8787",
    "http://chias-macbook-pro.tail3d3c18.ts.net:8787", "http://ljm2222.tail3d3c18.ts.net:8787",
    ...(process.env.OCR_ALLOWED_ORIGINS || "").split(",").map((s) => s.trim()).filter(Boolean),
  ]);
}

// Tailnet access remains required. Exact origins also prevent arbitrary public
// websites from issuing requests through a user's private-network connection.
function applyCors(req, res) {
  const origin = req.headers.origin;
  if (origin && !allowedOrigins().has(origin)) {
    res.writeHead(403, { "content-type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ ok: false, error: "此網頁來源尚未獲准連線。請將正式頁面網址加入 OCR_ALLOWED_ORIGINS。" }));
    return false;
  }
  if (origin) {
    res.setHeader("access-control-allow-origin", origin);
    res.setHeader("vary", "Origin");
    res.setHeader("access-control-expose-headers", "content-disposition, x-saved-path");
  }
  if (req.method === "OPTIONS") {
    if (!origin) { res.writeHead(400); res.end(); return false; }
    const headers = (req.headers["access-control-request-headers"] || "").toLowerCase().split(",").map((s) => s.trim()).filter(Boolean);
    const method = req.headers["access-control-request-method"] || "GET";
    if (!["GET", "POST"].includes(method) || headers.some((h) => !["content-type", "authorization"].includes(h))) {
      res.writeHead(403); res.end(); return false;
    }
    res.setHeader("access-control-allow-methods", "GET,POST,OPTIONS");
    res.setHeader("access-control-allow-headers", "content-type, authorization");
    res.setHeader("access-control-max-age", "600");
    if (req.headers["access-control-request-private-network"] === "true") res.setHeader("access-control-allow-private-network", "true");
    res.writeHead(204); res.end(); return false;
  }
  return true;
}
module.exports = { applyCors };
