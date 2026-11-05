#!/usr/bin/env python3
"""兩台 Claude 透過 Claude Bridge 互傳訊息（只用標準函式庫，Mac／Windows 都能跑）。

連線資訊不進 git（repo 是公開的），用環境變數：
  BRIDGE_URL         中繼網址，向使用者要
  BRIDGE_TOKEN_FILE  存 token 的檔案，預設 ~/.config/claude-bridge/token

python ocr/figcrop/bridge.py send worker "【實作回報】..."     # 第一個參數是自己的名字
python ocr/figcrop/bridge.py watch worker                      # 有別人的新訊息就印一行；放進 Monitor 工具，閒置不耗額度
python ocr/figcrop/bridge.py history 20                        # 看最近 20 則
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CHANNEL = "figcrop"
URL = os.environ.get("BRIDGE_URL", "").rstrip("/")
TOKEN_FILE = Path(os.environ.get("BRIDGE_TOKEN_FILE", Path.home() / ".config/claude-bridge/token"))


def call(path, body=None, timeout=70):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(URL + path, data=data, headers={
        "Authorization": "Bearer " + TOKEN_FILE.read_text().strip(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.load(res)


def text_of(m):
    """訊息列表只有 preview（會截斷），全文要逐則再取一次。"""
    content = call("/api/messages/" + m["id"], timeout=15).get("content")
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def messages(limit):
    data = call("/api/messages?" + urllib.parse.urlencode({"channel": CHANNEL, "limit": limit}), timeout=15)
    rows = data.get("messages", data) if isinstance(data, dict) else data
    return sorted(rows, key=lambda m: m.get("seq", 0))


def watch(me):
    """每次重開都從記錄的位置接著讀，Monitor 到期重開不會漏訊息也不會重複。"""
    state = TOKEN_FILE.parent / f"last_seq_{me}"
    seen = int(state.read_text()) if state.exists() else max([m.get("seq", 0) for m in messages(1)] or [0])
    failing = False
    while True:
        try:
            rows = [m for m in messages(50) if m.get("seq", 0) > seen]
            if failing:
                print("中繼恢復連線", flush=True); failing = False
        except (urllib.error.URLError, OSError, ValueError) as e:
            if not failing:     # 只報一次，斷線時也不會安靜到讓人以為沒訊息
                print(f"中繼連不上：{e}（改看 PR 留言）", flush=True); failing = True
            time.sleep(30)
            continue
        for m in rows:
            seen = max(seen, m.get("seq", 0))
            if m.get("sender") != me:
                print(f"[{m.get('sender')}] " + " ⏎ ".join(text_of(m).splitlines()), flush=True)
        state.write_text(str(seen))
        time.sleep(3)


def main():
    if not URL:
        sys.exit("沒有設定 BRIDGE_URL")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "send" and len(sys.argv) == 4:
        print(call("/api/send", {"channel": CHANNEL, "sender": sys.argv[2], "content": sys.argv[3]}, timeout=15).get("seq"))
    elif cmd == "watch" and len(sys.argv) == 3:
        watch(sys.argv[2])
    elif cmd == "history":
        for m in messages(int(sys.argv[2]) if len(sys.argv) > 2 else 20):
            print(f"{m.get('seq')} {m.get('ts', '')[:19]} [{m.get('sender')}] {text_of(m)}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
