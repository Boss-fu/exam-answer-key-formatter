"""茲茲 OCR 主機（桌面版 2.0）：一個程式，雙擊就在背景執行（沒有黑視窗），右下角有小圖示。

- 在這台電腦跑 OCR、自動切圖與題庫 Excel（API 與 local-app 的 /api/py-ocr/* 相同）。
- 本機只監聽 127.0.0.1；透過 Tailscale 私有 HTTPS（8443／8790）讓其他老師一起連線，並自動登錄到網站的主機選單。
- 內建 AI 橋接（8790）：用這台電腦上已登入的 agy／claude／codex 做 AI 分類，老師免輸入連線碼。
- 處理預設一次一份、其餘排隊；可在設定檔調高並行數。
"""
import json
import os
import queue
import secrets
import shutil
import sys
import tempfile
import threading
import time
import traceback
from base64 import b64decode
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VERSION = "2.0.0"
PORT = int(os.environ.get("ZIZI_OCR_PORT", "18787"))   # 不用 8787：那是 OCR 主機的埠，有的電腦把它對外開放
HOST = "127.0.0.1"
MAX_BODY = 100 * 1024 * 1024
JOB_TTL = 24 * 3600   # 考卷含學生資料，不留超過一天
import hostlib
from hostlib import ALLOWED_ORIGINS, log

CFG = hostlib.load_config()
BRIDGE_PORT = int(os.environ.get("ZIZI_BRIDGE_PORT", "8790"))   # 網頁用「所選主機的 :8790」找 AI 橋接

BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("OCR_MODELS_DIR", os.path.join(BASE, "models"))
os.environ.setdefault("EXAM_TEMPLATE", os.path.join(BASE, "assets", "template.docx"))
os.environ["OCR_PROGRESS"] = "1"
os.environ["OCR_SKIP_CLEAN"] = "1"   # 清稿在瀏覽器做完了；這裡只做 OCR

JOBS_DIR = os.path.join(hostlib.APPDIR, "jobs")
jobs = {}
work = queue.Queue()
heavy = hostlib.Slots(CFG["ocrConcurrency"])   # OCR 與自動裁切預設一次只跑一個（避免記憶體爆掉）；設定檔可調高
crop_jobs = {}


def run_figcrop(pdf_bytes, job=None, state=None):
    """自動裁切：在子程序跑 crop.py（同一支 exe 的 --figcrop 模式），版面模型偶爾在結束時才當掉也不拖垮本機程式。"""
    import subprocess
    tmp = tempfile.mkdtemp(prefix="figcrop-", dir=JOBS_DIR)
    try:
        pdf = os.path.join(tmp, "source.pdf")
        with open(pdf, "wb") as f:
            f.write(pdf_bytes)
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--figcrop"]
        else:
            cmd = [sys.executable, os.path.abspath(__file__), "--figcrop"]
        cmd += [pdf, "-o", os.path.join(tmp, "out"), "--json"]
        ocr_dir = result_dir(job) if job in jobs else None
        if ocr_dir and os.path.isdir(ocr_dir) and any(n.lower().endswith("_ocr_boxes.json") for n in os.listdir(ocr_dir)):
            cmd += ["--ocr-dir", ocr_dir]
        with heavy:
            if state is not None:
                state["status"] = "running"
            p = subprocess.run(cmd, capture_output=True, timeout=10 * 60,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = p.stdout.decode("utf-8", "replace")
        try:
            data = json.loads(out[out.index("{"):])
        except ValueError:
            data = None
        if data and data.get("ok"):   # 結果已完整輸出，結束時才當掉就照樣採用
            return data
        err = p.stderr.decode("utf-8", "replace") or f"結束碼 {p.returncode}"
        return {"ok": False, "error": "自動截圖失敗：" + err[-400:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "自動截圖失敗：超過 10 分鐘"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def figcrop_cli(argv):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    import crop
    sys.argv = ["crop.py"] + argv
    crop.main()
    sys.stdout.flush()


def job_dir(job):
    return os.path.join(JOBS_DIR, job)


def result_dir(job):
    return os.path.join(job_dir(job), "result")


def worker():
    try:
        import pyexam
    except ImportError as e:
        log(f"載入 OCR 引擎失敗：{e}")
        return
    while True:
        job, source = work.get()
        state = jobs[job]
        try:
            with heavy:
                state["status"] = "running"
                r = pyexam.process(source, result_dir(job), dpi=350)
            state["txt"] = os.path.basename(r["txt"])
            state["status"] = "done"
        except Exception:
            log(traceback.format_exc())
            state["error"] = traceback.format_exc()[-500:]
            state["status"] = "error"
        finally:
            try:
                os.remove(source)
            except OSError:
                pass


def clean_old_jobs():
    if not os.path.isdir(JOBS_DIR):
        return
    for name in os.listdir(JOBS_DIR):
        path = os.path.join(JOBS_DIR, name)
        if time.time() - os.path.getmtime(path) > JOB_TTL:
            shutil.rmtree(path, ignore_errors=True)


def read_events(job):
    try:
        with open(os.path.join(result_dir(job), "progress.jsonl"), encoding="utf-8") as f:
            return [{"message": e.get("msg", ""), "stage": e.get("stage"), "page": e.get("page"), "pages": e.get("pages")}
                    for e in map(json.loads, filter(None, f.read().splitlines()))]
    except OSError:
        return []


def job_result(job, state):
    txt = state.get("txt")
    text = ""
    if txt:
        with open(os.path.join(result_dir(job), txt), encoding="utf-8") as f:
            text = f.read()
    return {"ok": True, "job": job, "text": text, "log": "", "stats": {}, "reviewText": "", "reviewUrl": None,
            "figures": [], "docxUrl": None, "txtUrl": f"/api/py-ocr/download?job={job}&kind=txt",
            "qbankUrl": f"/api/py-ocr/qbank-xlsx?job={job}"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def cors(self):
        origin = self.headers.get("Origin")
        if origin and origin not in ALLOWED_ORIGINS:
            self.send_json(403, {"ok": False, "error": "此網頁來源未獲准連線本機程式"}, cors=False)
            return False
        return True

    def send_common(self):
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Expose-Headers", "content-disposition")

    def send_json(self, code, obj, cors=True):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cors:
            self.send_common()
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type, filename):
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_common()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        origin = self.headers.get("Origin")
        if origin not in ALLOWED_ORIGINS:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(204)
        self.send_common()
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Max-Age", "600")
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def known_job(self, query):
        job = (query.get("job") or [""])[0]
        return job if job in jobs else None

    def do_GET(self):
        if not self.cors():
            return
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/api/health":
            return self.send_json(200, {"ok": True, "service": "tzutzu-ocr", "host": hostlib.display_name(CFG), "revision": VERSION,
                                        "ocrEngine": "PP-OCRv5（桌面版）", "capabilities": ["ocr", "qbank-xlsx", "figcrop", "ai-bridge"],
                                        "slots": {"ocr": heavy.n, "active": heavy.active, "waiting": heavy.waiting}})
        if url.path == "/api/py-ocr/figcrop-status":
            state = crop_jobs.get((query.get("id") or [""])[0])
            if not state:
                return self.send_json(404, {"ok": False, "done": True, "error": "找不到這筆切圖工作（本機程式可能重開過），請再按一次自動裁切"})
            if state["status"] == "done":
                return self.send_json(200, {"done": True, **state["result"]})
            return self.send_json(200, {"ok": True, "done": False, "queued": state["status"] == "queued"})
        job = self.known_job(query)
        if not job:
            return self.send_json(404, {"ok": False, "error": "找不到這筆工作"})
        state = jobs[job]
        if url.path == "/api/py-ocr/progress":
            since = max(0, int((query.get("since") or ["0"])[0] or 0))
            events = read_events(job)[since:]
            next_i = since + len(events)
            if state["status"] == "error":
                return self.send_json(200, {"ok": False, "events": events, "next": next_i, "done": True, "error": state["error"]})
            if state["status"] != "done":
                if state["status"] == "queued" and not events:
                    events = [{"message": "排隊中，前一份考卷跑完就開始"}]
                    return self.send_json(200, {"ok": True, "events": events, "next": since, "done": False})
                return self.send_json(200, {"ok": True, "events": events, "next": next_i, "done": False})
            return self.send_json(200, {**job_result(job, state), "events": events, "next": next_i, "done": True})
        if url.path == "/api/py-ocr/download" and state.get("txt"):
            return self.send_file(os.path.join(result_dir(job), state["txt"]), "text/plain; charset=utf-8", state["txt"])
        if url.path == "/api/py-ocr/qbank-xlsx" and state.get("txt"):
            name = os.path.splitext(state["txt"])[0]
            out = os.path.join(job_dir(job), "qbank.xlsx")
            try:   # import 也放進 try：缺模組時要回 500＋CORS，不然瀏覽器只看到 Failed to fetch
                import to_qbank_xlsx
                to_qbank_xlsx.write(to_qbank_xlsx.rows(result_dir(job), name), out)
            except Exception:
                log(traceback.format_exc())
                return self.send_json(500, {"ok": False, "error": "題庫 Excel 產生失敗：" + traceback.format_exc()[-400:]})
            return self.send_file(out, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", name + "_題庫.xlsx")
        self.send_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        if not self.cors():
            return
        path = urlparse(self.path).path
        if path not in ("/api/py-ocr/start", "/api/py-ocr/figcrop"):
            return self.send_json(404, {"ok": False, "error": "Not found"})
        size = int(self.headers.get("Content-Length") or 0)
        if size > MAX_BODY:
            return self.send_json(413, {"ok": False, "error": "檔案太大"})
        body = json.loads(self.rfile.read(size) or b"{}")
        if path == "/api/py-ocr/figcrop":
            if not body.get("dataBase64"):
                return self.send_json(400, {"ok": False, "error": "缺少 PDF"})
            pdf_bytes = b64decode(body["dataBase64"])
            job = str(body.get("job") or "")
            if not body.get("async"):
                data = run_figcrop(pdf_bytes, job)
                return self.send_json(200 if data.get("ok") else 500, data)
            crop_job = secrets.token_hex(12)
            state = crop_jobs[crop_job] = {"status": "queued"}

            def bg():
                state["result"] = run_figcrop(pdf_bytes, job, state)
                state["status"] = "done"
            threading.Thread(target=bg, daemon=True).start()
            return self.send_json(200, {"ok": True, "cropJob": crop_job})
        if not body.get("dataBase64"):
            return self.send_json(400, {"ok": False, "error": "缺少考卷檔案"})
        filename = os.path.basename(str(body.get("filename") or "考卷.pdf"))
        if os.path.splitext(filename)[1].lower() not in (".pdf", ".png", ".jpg", ".jpeg"):
            return self.send_json(400, {"ok": False, "error": "只接受 PDF、PNG 或 JPG 檔案"})
        job = secrets.token_hex(12)
        os.makedirs(result_dir(job))
        source = os.path.join(job_dir(job), filename)
        with open(source, "wb") as f:
            f.write(b64decode(body["dataBase64"]))
        jobs[job] = {"status": "queued"}
        work.put((job, source))
        self.send_json(200, {"ok": True, "jobId": job})


class BridgeHandler(BaseHTTPRequestHandler):
    """AI 橋接（開放模式）：只接受題庫網頁來源，老師免輸入連線碼；網址與 Node 版 ai-bridge 相同（/engines、/ai、/ping）。"""
    protocol_version = "HTTP/1.1"
    slots = None

    def log_message(self, *args):
        pass

    def _cors(self):
        origin = self.headers.get("Origin") or ""
        return {"Access-Control-Allow-Origin": origin if origin in ALLOWED_ORIGINS else "null",
                "Access-Control-Allow-Headers": "content-type,x-bridge-token", "Access-Control-Allow-Methods": "POST,GET,OPTIONS",
                "Access-Control-Allow-Private-Network": "true", "Vary": "Origin"}

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in self._cors().items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _allowed(self):
        return (self.headers.get("Origin") or "") in ALLOWED_ORIGINS

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in self._cors().items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._allowed():
            return self._send(403, {"error": "只接受題庫網頁來源"})
        if self.path == "/ping":
            return self._send(200, {"ok": True})
        if self.path == "/engines":
            return self._send(200, {"ok": True, "engines": hostlib.detect_engines()})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._allowed():
            return self._send(403, {"error": "只接受題庫網頁來源"})
        if self.path != "/ai":
            return self._send(404, {"error": "not found"})
        size = int(self.headers.get("Content-Length") or 0)
        if size > 30_000_000:
            return self._send(413, {"error": "內容太大"})
        try:
            j = json.loads(self.rfile.read(size) or b"{}")
        except ValueError:
            return self._send(400, {"error": "JSON 格式錯誤"})
        if not str(j.get("prompt") or "").strip():
            return self._send(400, {"error": "缺 prompt"})
        try:
            with BridgeHandler.slots:   # 預設一次一題，其餘排隊
                text = hostlib.run_ai(j.get("prompt"), j.get("imgs"), j.get("engine"))
            self._send(200, {"text": text})
        except Exception as e:
            self._send(500, {"error": str(e)})


def make_servers():
    """建立 OCR 與 AI 橋接兩個本機伺服器（只聽 127.0.0.1）。回傳 (ocr, bridge)；埠被占用代表已經有一份在跑。"""
    ocr = ThreadingHTTPServer((HOST, PORT), Handler)
    BridgeHandler.slots = hostlib.Slots(CFG["aiConcurrency"])
    bridge = ThreadingHTTPServer((HOST, BRIDGE_PORT), BridgeHandler)
    return ocr, bridge


def share_loop(stop, https_ocr=8443, https_ai=8790):
    """每 5 分鐘：確認 Tailscale HTTPS 入口、把名稱與網址登錄到網站（主機選單自動出現）。失敗只記錄。"""
    last = None
    while not stop.is_set():
        try:
            cfg = hostlib.load_config()
            dns = hostlib.tailscale_dns() if (cfg.get("share") or cfg.get("register")) else ""
            if not dns:
                hostlib.write_state({"ok": False, "at": int(time.time() * 1000), "error": "Tailscale 尚未連線或沒安裝"})
            else:
                if cfg.get("share"):
                    hostlib.ensure_https(dns, https_ocr, PORT)
                    hostlib.ensure_https(dns, https_ai, BRIDGE_PORT)
                name = hostlib.display_name(cfg)
                if cfg.get("register"):
                    hostlib.upsert(hostlib.build_row(dns, name, https_ocr, https_ai, VERSION))
                    if last != (name, dns):
                        log(f"[登錄] 已登錄到網站：{name}（https://{dns}:{https_ocr}）")
                    last = (name, dns)
                hostlib.write_state({"ok": True, "at": int(time.time() * 1000), "name": name, "dns": dns,
                                     "url": f"https://{dns}:{https_ocr}", "aiUrl": f"https://{dns}:{https_ai}"})
        except Exception as e:
            hostlib.write_state({"ok": False, "at": int(time.time() * 1000), "error": str(e)[:160]})
            log(f"[登錄/HTTPS] {e}")
        stop.wait(300)


def deregister():
    """正常結束時標成離線（盡力而為）。"""
    try:
        cfg = hostlib.load_config()
        dns = hostlib.tailscale_dns()
        if dns and cfg.get("register"):
            row = hostlib.build_row(dns, hostlib.display_name(cfg), 8443, 8790, VERSION, online=False)
            hostlib.upsert(row)
    except Exception:
        pass


def start_background(stop):
    """啟動 OCR 工作執行緒、兩個本機伺服器與登錄迴圈。回傳 (ocr, bridge)；埠被占用時回傳 None。"""
    os.makedirs(JOBS_DIR, exist_ok=True)
    clean_old_jobs()
    try:
        ocr, bridge = make_servers()
    except OSError:
        return None
    for _ in range(heavy.n):
        threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=ocr.serve_forever, daemon=True).start()
    threading.Thread(target=bridge.serve_forever, daemon=True).start()
    threading.Thread(target=share_loop, args=(stop,), daemon=True).start()
    log(f"茲茲 OCR 主機 {VERSION} 已啟動（OCR 埠 {PORT}、AI 橋接埠 {BRIDGE_PORT}、並行 OCR {heavy.n}／AI {CFG['aiConcurrency']}）")
    return ocr, bridge


def main():
    import multiprocessing
    multiprocessing.freeze_support()   # pyexam 多頁並行用 spawn 子程序；打包成 exe 沒這行，子程序會重跑整支程式而卡住
    argv = sys.argv[1:]
    if argv and argv[0] == "--figcrop":
        return figcrop_cli(argv[1:])
    for stream in (sys.stdout, sys.stderr):   # 無視窗版（--windowed）沒有 stdout／stderr
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if "--selftest" in argv:
        import selftest
        return selftest.run(sys.modules[__name__], light="--light" in argv)
    stop = threading.Event()
    started = start_background(stop)
    if started is None:
        log("埠已被占用：茲茲 OCR 主機可能已經開著了，這一份直接結束。")
        return
    if "--no-tray" in argv or sys.platform != "win32":
        log("以無圖示模式執行（Ctrl+C 結束）")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return
    import tray
    tray.run(sys.modules[__name__], stop)


if __name__ == "__main__":
    sys.exit(main() or 0)
