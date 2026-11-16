"""--selftest：在 CI（GitHub Actions Windows）或本機驗證打包後的程式能動。
--light 只測 HTTP 層（沒有 OCR 套件時用，例如 Linux 開發環境）；完整模式會真的跑一份 OCR、題庫 Excel 與自動切圖。
結果寫到環境變數 ZIZI_SELFTEST_OUT 指定的檔案（無視窗版沒有 stdout），失敗時結束碼 1。"""
import base64
import json
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request

ORIGIN = "https://science-qbank.vercel.app"


def call(url, data=None, origin=ORIGIN, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def make_pdf():
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 90), "1. Which is a pure substance?", fontsize=14)
    page.insert_text((70, 120), "(A) air  (B) salt water  (C) distilled water  (D) sea water", fontsize=12)
    page.insert_text((60, 190), "2. Density equals mass divided by volume.", fontsize=14)
    page.insert_text((70, 220), "(A) true  (B) false", fontsize=12)
    return doc.tobytes()


def run(app, light=False):
    results, ok_all = [], True
    out = os.environ.get("ZIZI_SELFTEST_OUT")

    def check(name, fn):
        nonlocal ok_all
        t0 = time.time()
        try:
            detail = fn()
            results.append({"name": name, "ok": True, "detail": detail, "sec": round(time.time() - t0, 1)})
        except Exception as e:
            ok_all = False
            results.append({"name": name, "ok": False, "detail": f"{e}", "trace": traceback.format_exc()[-600:], "sec": round(time.time() - t0, 1)})

    stop = threading.Event()
    started = app.start_background(stop)
    if started is None:
        results.append({"name": "啟動", "ok": False, "detail": "埠被占用"})
        ok_all = False
    else:
        base = f"http://127.0.0.1:{app.PORT}"
        bridge = f"http://127.0.0.1:{app.BRIDGE_PORT}"

        def health():
            c, b = call(base + "/api/health")
            j = json.loads(b)
            assert c == 200 and j.get("service") == "tzutzu-ocr", j
            return f"{j.get('host')} v{j.get('revision')}"
        check("OCR 主機 /api/health", health)

        def engines():
            c, b = call(bridge + "/engines")
            j = json.loads(b)
            assert c == 200 and j.get("ok") and set(j["engines"]) == {"agy", "claude", "codex"}, j
            return {k: v["ok"] for k, v in j["engines"].items()}
        check("AI 橋接 /engines（網站來源）", engines)

        def reject():
            c, _ = call(bridge + "/engines", origin=None)
            c2, _ = call(base + "/api/health", origin="https://evil.example")
            assert c == 403 and c2 == 403, (c, c2)
            return "沒有來源／陌生來源皆 403"
        check("來源檢查", reject)

        def ai_no_tool():
            c, b = call(bridge + "/ai", {"prompt": "hi", "engine": "claude"})
            j = json.loads(b)
            assert c in (200, 500) and ("text" in j or "error" in j), j
            return "有 AI 工具" if c == 200 else "沒有 AI 工具（預期）：" + j["error"][:40]
        check("AI 橋接 /ai", ai_no_tool)

        if not light:
            pdf = make_pdf()

            def ocr():
                c, b = call(base + "/api/py-ocr/start", {"filename": "selftest.pdf", "dataBase64": base64.b64encode(pdf).decode()})
                j = json.loads(b)
                assert c == 200 and j.get("jobId"), j
                job = j["jobId"]
                since, t0 = 0, time.time()
                while time.time() - t0 < 900:
                    c, b = call(f"{base}/api/py-ocr/progress?job={job}&since={since}")
                    j = json.loads(b)
                    since = j.get("next", since)
                    if j.get("done"):
                        assert j.get("ok"), j.get("error")
                        text = j.get("text") or ""
                        assert len(text) > 10, "OCR 文字太短：" + text[:50]
                        c, x = call(f"{base}/api/py-ocr/qbank-xlsx?job={job}")
                        assert c == 200 and x[:2] == b"PK", f"題庫 Excel 失敗 {c}"
                        return f"OCR {len(text)} 字；題庫 Excel {len(x)} 位元組"
                    time.sleep(2)
                raise TimeoutError("OCR 超過 15 分鐘")
            check("OCR＋題庫 Excel", ocr)

            def figcrop():
                c, b = call(base + "/api/py-ocr/figcrop", {"dataBase64": base64.b64encode(pdf).decode(), "async": True})
                j = json.loads(b)
                assert c == 200 and j.get("cropJob"), j
                t0 = time.time()
                while time.time() - t0 < 900:
                    c, b = call(f"{base}/api/py-ocr/figcrop-status?id={j['cropJob']}")
                    d = json.loads(b)
                    if d.get("done"):
                        assert d.get("ok"), d.get("error")
                        return f"頁數 {len(d.get('pages', []))}"
                    time.sleep(2)
                raise TimeoutError("自動切圖超過 15 分鐘")
            check("自動切圖", figcrop)
        stop.set()
    report = {"ok": ok_all, "version": app.VERSION, "light": light, "results": results}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
    if sys.stdout is not None:
        print(text)
    return 0 if ok_all else 1
