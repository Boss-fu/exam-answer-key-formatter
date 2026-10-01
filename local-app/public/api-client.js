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
        throw new Error(target.label + "太久沒回應，可能還在處理上一份工作或已離線。請稍等再按「重新連線」，不要重複送出。");
      }
      throw new Error("無法連線至" + target.label + "。請確認那台電腦已開機並啟動 OCR 服務、你的電腦已登入 Tailscale；若瀏覽器詢問「區域網路存取」，請按允許。");
    }
    var type = response.headers.get("content-type") || "";
    if (!response.ok) {
      var detail = "";
      if (type.indexOf("application/json") !== -1) {
        var data = await response.json().catch(function () { return {}; });
        detail = data.error || "";
      }
      if (!detail && response.status === 404) detail = "這台電腦的程式版本太舊，還沒有這個功能；請重新啟動它的 OCR 服務以更新。";
      throw new Error("HTTP " + response.status + "：" + (detail || "處理失敗，請稍後再試；一直失敗請聯絡管理員。"));
    }
    if (type.indexOf("text/html") !== -1) {
      throw new Error("主機回傳網頁而不是處理結果，請確認選的是正確的 OCR 主機。");
    }
    return response;
  }

  var client = { apiUrl: apiUrl, request: request, fileMessage: fileMessage, normalizeBase: normalizeBase };
  if (typeof module !== "undefined" && module.exports) module.exports = client;
  else root.OcrApi = client;
})(typeof window !== "undefined" ? window : globalThis);
