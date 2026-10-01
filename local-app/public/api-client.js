// Static portal: requests go directly to the selected private backend.
(function (root) {
  function normalizeBase(value, protocol) {
    var url;
    try { url = new URL(value); } catch (e) { throw new Error("請填寫完整伺服器網址。"); }
    if (!/^https?:$/.test(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== "/") throw new Error("伺服器網址只接受 HTTP(S) 網域與連接埠，不可包含帳密或路徑。");
    if (protocol === "https:" && url.protocol !== "https:") throw new Error("Vercel 頁面必須連接 HTTPS 主機；請啟用 Tailscale Serve，不能直接使用 http://100.x.x.x:8787。");
    return url.origin;
  }
  function apiUrl(path, target, env) {
    env = env || root;
    if (!/^\/api\/[a-z0-9/-]+(?:\?[^#]*)?$/i.test(path) || path.split("?")[0].includes("..")) throw new Error("無效的 API 路徑");
    return normalizeBase(target.baseUrl, env.location.protocol) + path;
  }

  function fileMessage() {
    return "目前是本機 HTML 預覽。請改開 Vercel 操作頁面，或先啟動 start.command 再開 http://localhost:8787/。";
  }

  async function request(path, options, target, env) {
    env = env || root;
    if (env.location.protocol === "file:") throw new Error(fileMessage());
    var url = apiUrl(path, target, env), response;
    try {
      response = await env.fetch(url, Object.assign({ credentials: "omit", cache: "no-store" }, options));
    } catch (error) {
      if (error.name === "TimeoutError" || error.name === "AbortError") {
        throw new Error(target.label + "回應逾時；可能正在處理工作或已離線。請確認原工作狀態，避免重複送出。");
      }
      throw new Error("無法連線至" + target.label + "（" + target.baseUrl + "）。請確認 Tailscale、主機服務及 HTTPS 入口；若瀏覽器詢問區域網路權限，請允許。也可能是主機尚未允許此頁面的跨來源連線。");
    }
    var type = response.headers.get("content-type") || "";
    if (!response.ok) {
      var detail = "";
      if (type.indexOf("application/json") !== -1) {
        var data = await response.json().catch(function () { return {}; });
        detail = data.error || "";
      }
      if (!detail && response.status === 404) detail = "所選主機尚未提供此功能，請確認主機程式版本與 API 路徑。";
      throw new Error("HTTP " + response.status + "：" + (detail || "伺服器未能完成請求，請查看主機終端機紀錄。"));
    }
    if (type.indexOf("text/html") !== -1) {
      throw new Error("伺服器回傳網頁而非處理結果，請確認目前網址是 OCR 網站服務。");
    }
    return response;
  }

  var client = { apiUrl: apiUrl, request: request, fileMessage: fileMessage, normalizeBase: normalizeBase };
  if (typeof module !== "undefined" && module.exports) module.exports = client;
  else root.OcrApi = client;
})(typeof window !== "undefined" ? window : globalThis);
