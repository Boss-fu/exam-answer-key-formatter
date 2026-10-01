(function (root) {
  var config = {
    service: "tzutzu-ocr",
    apiVersion: 2,
    portalOrigins: ["https://tzutzu-ocr.vercel.app"],
    servers: {
      mac: { label: "我的 Mac", baseUrl: "https://chias-macbook-pro.tail3d3c18.ts.net:8443" },
      company: { label: "公司電腦", baseUrl: "https://ljm2222.tail3d3c18.ts.net:8443" }
    }
  };
  if (typeof module !== "undefined" && module.exports) module.exports = config;
  else root.OcrServerConfig = config;
})(typeof window !== "undefined" ? window : globalThis);
