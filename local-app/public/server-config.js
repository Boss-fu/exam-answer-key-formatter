(function (root) {
  var config = {
    service: "tzutzu-ocr",
    apiVersion: 2,
    // 題庫 raw.html 直接沿用同一套茲茲 OCR 後端；OCR 首頁仍保留給其他科目老師使用。
    portalOrigins: ["https://tzutzu-ocr.vercel.app", "https://science-qbank.vercel.app"],
    servers: {
      mac: { label: "我的 Mac", baseUrl: "https://chias-macbook-pro.tail3d3c18.ts.net:8443" },
      company: { label: "公司電腦", baseUrl: "https://ljm2222.tail3d3c18.ts.net:8443" }
    }
  };
  if (typeof module !== "undefined" && module.exports) module.exports = config;
  else root.OcrServerConfig = config;
})(typeof window !== "undefined" ? window : globalThis);
