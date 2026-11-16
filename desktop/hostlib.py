"""茲茲 OCR 主機（桌面版）的共用零件：設定檔、Tailscale、網站登錄、AI 橋接、並行佇列。
純標準庫，沒有任何 GUI 依賴，所以在 Linux 上也能單元測試。"""
import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
APPDIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share"), "ZiziOCR")
CONFIG_FILE = os.path.join(APPDIR, "config.json")
LOG_FILE = os.path.join(APPDIR, "ziziocr.log")
STATE_FILE = os.path.join(APPDIR, "registry-state.json")

SUPABASE_URL = os.environ.get("HOST_REGISTRY_URL", "https://esfepufqyeplafzbeeag.supabase.co")
SUPABASE_KEY = os.environ.get("HOST_REGISTRY_KEY", "sb_publishable_P_OpGwKYuWxpHMHCZdQRPA_1lCCLfNf")   # 公開的 anon key，與網站前端相同
ID_BASE = 100000
ALLOWED_ORIGINS = {"https://science-qbank.vercel.app"} | {o.strip() for o in os.environ.get("OCR_ALLOWED_ORIGINS", "").split(",") if o.strip()}


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + str(msg) + "\n"
    try:
        os.makedirs(APPDIR, exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 2_000_000:
            os.replace(LOG_FILE, LOG_FILE + ".old")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    if sys.stdout is not None and getattr(sys.stdout, "isatty", lambda: False)():
        sys.stdout.write(line)


# ---------------- 設定 ----------------
DEFAULTS = {"displayName": "", "ocrConcurrency": 1, "aiConcurrency": 1, "register": True, "share": True, "autostart": True}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    for k in ("ocrConcurrency", "aiConcurrency"):
        try:
            cfg[k] = max(1, min(8, int(cfg[k])))
        except (TypeError, ValueError):
            cfg[k] = 1
    return cfg


def save_config(cfg):
    os.makedirs(APPDIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


def display_name(cfg=None):
    cfg = cfg or load_config()
    name = str(cfg.get("displayName") or "").strip()
    return (name or socket.gethostname())[:20]


# ---------------- 並行佇列 ----------------
class Slots:
    """同時最多 n 份在跑，其餘排隊（先進先出）。"""

    def __init__(self, n):
        self.n = max(1, int(n))
        self._sem = threading.Semaphore(self.n)
        self._lock = threading.Lock()
        self.active = 0
        self.waiting = 0

    def __enter__(self):
        with self._lock:
            self.waiting += 1
        self._sem.acquire()
        with self._lock:
            self.waiting -= 1
            self.active += 1
        return self

    def __exit__(self, *a):
        with self._lock:
            self.active -= 1
        self._sem.release()

    def is_queued(self):
        return self.active >= self.n


# ---------------- Tailscale ----------------
def tailscale_bin():
    p = os.environ.get("TAILSCALE_BIN")
    if p:
        return p
    for c in (os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Tailscale", "tailscale.exe"),
              "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        if os.path.exists(c):
            return c
    return shutil.which("tailscale") or "tailscale"


def ts(args, timeout=15):
    env = dict(os.environ, TAILSCALE_BE_CLI="1")
    r = subprocess.run([tailscale_bin()] + args, capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW, env=env)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).decode("utf-8", "replace")[:200])
    return r.stdout.decode("utf-8", "replace")


def tailscale_dns():
    try:
        st = json.loads(ts(["status", "--json"]))
        if st.get("BackendState") == "Running":
            return (st.get("Self", {}).get("DNSName") or "").rstrip(".")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        pass
    return ""


def ensure_https(dns, https_port, target_port):
    """把 https://<dns>:<https_port> 轉到本機 target_port（只在 Tailscale 私有網路內；絕不用 Funnel，不動別人的設定）。"""
    cfg = json.loads(ts(["serve", "status", "--json"]) or "{}")
    if cfg.get("AllowFunnel") and any(cfg["AllowFunnel"].values()):
        raise RuntimeError("偵測到 Funnel 設定，為安全起見不更動")
    want = f"http://127.0.0.1:{target_port}"
    web, tcp = cfg.get("Web") or {}, cfg.get("TCP") or {}
    used = str(https_port) in tcp or any(k.endswith(f":{https_port}") for k in web)
    handlers = (web.get(f"{dns}:{https_port}") or {}).get("Handlers") or {}
    if used and (handlers.get("/") or {}).get("Proxy") != want:
        raise RuntimeError(f"{https_port} 埠已被其他 Tailscale 服務使用")
    if not used:
        ts(["serve", "--bg", f"--https={https_port}", "--yes", want])
    return True


# ---------------- 網站登錄 ----------------
def host_id(dns):
    h = 0x811C9DC5
    for b in dns.lower().encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return ID_BASE + (h % 1_000_000_000)


def build_row(dns, name, ocr_port, ai_port, version, online=True, name_at=0):
    obj = {"kind": "ocr-host", "v": 1, "name": name, "nameAt": int(name_at or 0), "url": f"https://{dns}:{ocr_port}", "aiUrl": f"https://{dns}:{ai_port}",
           "platform": sys.platform, "revision": version, "seen": int(time.time() * 1000), "online": online}
    return {"id": host_id(dns), "content": json.dumps(obj, ensure_ascii=False)}


def fetch_row(dns, opener=None):
    """讀回網站上這台的登錄資料（網頁改名會寫在這裡）；失敗回 None。"""
    try:
        req = urllib.request.Request(f"{SUPABASE_URL}/rest/v1/syllabus_doc?select=content&id=eq.{host_id(dns)}",
                                     headers={"apikey": SUPABASE_KEY, "Authorization": "Bearer " + SUPABASE_KEY})
        with (opener or urllib.request.urlopen)(req, timeout=15) as r:
            rows = json.loads(r.read().decode("utf-8"))
        return json.loads(rows[0]["content"]) if rows else None
    except Exception:
        return None


def sync_name(dns, cfg, opener=None):
    """名稱以「最後修改者」為準：網頁（nameAt 較新）→ 採用網頁名稱並存回本機設定；否則以本機為準。"""
    remote = fetch_row(dns, opener)
    try:
        rat = int((remote or {}).get("nameAt") or 0)
    except (TypeError, ValueError):
        rat = 0
    rname = str((remote or {}).get("name") or "").strip()[:20]
    if remote and rname and rat > int(cfg.get("nameAt") or 0):
        cfg["displayName"], cfg["nameAt"] = rname, rat
        save_config(cfg)
    return display_name(cfg), int(cfg.get("nameAt") or 0)


def upsert(row, opener=None):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/syllabus_doc?on_conflict=id", data=json.dumps([row]).encode("utf-8"), method="POST",
        headers={"apikey": SUPABASE_KEY, "Authorization": "Bearer " + SUPABASE_KEY, "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"})
    with (opener or urllib.request.urlopen)(req, timeout=15) as r:
        if r.status >= 300:
            raise RuntimeError(f"登錄失敗 HTTP {r.status}")
    return True


def write_state(obj):
    try:
        os.makedirs(APPDIR, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    except OSError:
        pass


def read_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ---------------- AI 橋接 ----------------
ENGINES = {
    "agy": {"label": "Antigravity（agy）", "bin": os.environ.get("AGY_BIN", "agy"),
            "fallback": os.path.join(os.path.expanduser("~"), ".local", "bin", "agy")},
    "claude": {"label": "Claude Code", "bin": os.environ.get("CLAUDE_BIN", "claude")},
    "codex": {"label": "Codex CLI", "bin": os.environ.get("CODEX_BIN", "codex")},
}


def engine_args(name, prompt, add_dir=None, out_file=None):
    if name == "agy":
        a = ["-p", prompt, "--effort", os.environ.get("AGY_EFFORT", "low")]
        if add_dir:
            a += ["--add-dir", add_dir]
        return a
    if name == "claude":
        return ["-p", prompt, "--model", os.environ.get("CLAUDE_MODEL", "haiku"), "--disable-slash-commands"]
    return ["exec", "--skip-git-repo-check", "--sandbox", "read-only", "-m", os.environ.get("CODEX_MODEL", "gpt-6-luna"),
            "-c", "model_reasoning_effort=" + os.environ.get("CODEX_EFFORT", "low"), "-o", out_file, prompt]


def detect_engines():
    out = {}
    for k, e in ENGINES.items():
        p = shutil.which(e["bin"]) or (e.get("fallback") if e.get("fallback") and os.path.exists(e["fallback"]) else "")
        out[k] = {"label": e["label"], "path": p or "", "ok": bool(p)}
    return out


def pick_engine(name=None):
    allv = detect_engines()
    if name and name != "auto":
        if not allv.get(name, {}).get("ok"):
            raise RuntimeError(f"這台電腦找不到 {name} 命令列工具")
        return name, allv[name]["path"]
    for k in ("agy", "claude", "codex"):
        if allv[k]["ok"]:
            return k, allv[k]["path"]
    raise RuntimeError("這台電腦沒有可用的 AI 命令列工具（agy／claude／codex）")


def save_imgs(srcs):
    if not srcs:
        return "", []
    d = tempfile.mkdtemp(prefix="agy-img-")
    files = []
    for i, src in enumerate(srcs[:6], 1):
        try:
            m = re.match(r"^data:image/(\w+);base64,(.*)$", src, re.S)
            if m:
                ext = "jpg" if m.group(1) == "jpeg" else m.group(1)
                buf = base64.b64decode(m.group(2))
            elif re.match(r"^https?://", src):
                with urllib.request.urlopen(src, timeout=20) as r:
                    buf = r.read()
                    ct = r.headers.get("content-type", "")
                ext = "jpg" if re.search("jpe?g", ct) else "gif" if "gif" in ct else "webp" if "webp" in ct else "png"
            else:
                continue
            if len(buf) > 8_000_000:
                continue
            p = os.path.join(d, f"img{i}.{ext}")
            with open(p, "wb") as f:
                f.write(buf)
            files.append(p)
        except Exception:
            continue
    return d, files


def run_ai(prompt, imgs=None, engine=None, timeout=240):
    prompt = str(prompt or "")[:60000]
    if not prompt:
        raise ValueError("缺 prompt")
    d, files = save_imgs([x for x in (imgs or []) if isinstance(x, str)])
    work = tempfile.mkdtemp(prefix="agy-bridge-")   # 空目錄：AI 工具就算想讀檔也讀不到專案
    try:
        full = prompt + (("\n\n題目附圖檔案（請用讀檔方式實際查看這些圖片再判斷）：\n" + "\n".join(files)) if files else "") + \
            "\n\n（只根據上面內容回答。只可讀取上面列出的圖片檔，不要讀其他檔案、不要寫檔或執行任何指令。）"
        name, binp = pick_engine(engine)
        out_file = os.path.join(work, "out.txt") if name == "codex" else None
        cmd = [binp] + engine_args(name, full, d or None, out_file)
        if sys.platform == "win32" and binp.lower().endswith((".cmd", ".bat")):
            cmd = ["cmd", "/c"] + cmd
        try:
            p = subprocess.run(cmd, cwd=work, capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise RuntimeError(name + " 逾時")
        text = p.stdout.decode("utf-8", "replace").strip()
        if out_file and os.path.exists(out_file):
            with open(out_file, encoding="utf-8", errors="replace") as f:
                text = f.read().strip()
        if re.search(r"Failed to authenticate|OAuth session expired|Please run /login", text, re.I):
            raise RuntimeError(name + " 尚未登入或登入已過期，請先在終端機登入該工具")
        if p.returncode == 0 or text:
            return text
        err = p.stderr.decode("utf-8", "replace")
        lines = [x for x in err.splitlines() if re.search(r"error|quota|unauthor|401|429|denied", x, re.I)]
        raise RuntimeError((name + " 失敗：" + (" ｜ ".join(lines[-2:]) if lines else err[-300:]))[:300])
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if d:
            shutil.rmtree(d, ignore_errors=True)
