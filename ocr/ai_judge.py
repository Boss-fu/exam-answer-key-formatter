#!/usr/bin/env python3
"""AI 當「大腦」：Python 做完 OCR 之後，一份考卷只呼叫一次 AI，請它做 Python 最不擅長的判斷，結果存成 ai_<後端>.json，
to_zizi_word.build(..., ai=<後端>) 會照它切題。粗重的事（轉正、分欄、OCR、裁圖、表格、組 Word）全部還是 Python。

AI 只看「行號＋文字」的精簡草稿（圖、表只留佔位），不看整頁圖片；只回傳改動，不重打全文 → 一份考卷約 5~8k token。
回傳 JSON：
    {"q": [題目開頭的行號…], "noise": [手寫雜訊行號…], "fix": [{"l": 行號, "from": "原字", "to": "改字"}…]}

後端（預設 auto：Claude → Codex 接力，全部失敗就退回 Python 規則）：
    claude  本機 Claude Code（claude -p，用 Claude 訂閱額度；考卷文字會送到 Anthropic）。一份約 10 秒
    codex   ChatGPT App 內附的 Codex（codex exec，用 ChatGPT 訂閱額度；考卷文字會送到 OpenAI）。一份約 35 秒
    ollama  本機 Ollama（完全不外傳、不花錢）；目前優先使用 qwen3-vl:8b，作為遠端引擎失敗時的備援

用法：
    python3 ocr/ai_judge.py <pyexam 輸出資料夾> --backend claude
    python3 ocr/ai_judge.py <pyexam 輸出資料夾> --backend ollama
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import to_zizi_word as Z  # noqa: E402

PROMPT = """你是國中自然科考卷的排版助理。下面是 OCR 辨識出來的考卷草稿，每行開頭是行號（L12|）。
OCR 有幾個毛病：學生用筆在題號、作答括號上寫答案或打勾，題號常被認錯、變形或整個不見；學生的手寫（答案、計算、勾勾）也會混成一行一行的雜訊；
另外有形近字錯誤（例：示意墨→示意圖、附圓→附圖、良尊體→良導體、酚酥→酚酞）。[圖]、[表] 是附圖與表格的位置。

請判斷三件事，只輸出一個 JSON 物件，不要任何說明文字：
1. "q"：每一題「題幹第一行」的行號（依題目順序）。單選題每題通常有一組 (A)(B)(C)(D) 選項；題號不見時，用語意判斷新題從哪一行開始。
   題組的共同說明（例如「請回答第 7～8 題」）不算題目。卷頭、作答說明、大題標題都不算題目。
2. "noise"：明顯是學生手寫雜訊、與考題無關的行號（例如只有「28-18=10」「√」「50=1×50」這類計算或符號的行）。拿不準就不要列。
3. "fix"：你非常確定的 OCR 錯字，格式 {"l": 行號, "from": "原文片段", "to": "改正"}；片段要短（1~6 字），只改錯字，不要潤飾、不要改寫句子、不要補題目沒有的內容。

輸出範例：{"q": [5, 12, 20], "noise": [3, 17], "fix": [{"l": 6, "from": "示意墨", "to": "示意圖"}]}

考卷草稿：
"""


def draft(pyexam_dir):
    """行號＋文字的精簡草稿；行號＝to_zizi_word.read_lines 的索引（兩邊共用）"""
    _, lines = Z.read_lines(pyexam_dir)
    out, page = [], None
    for i, (pg, t) in enumerate(lines):
        if pg != page:
            out.append(f"--- 第 {pg} 頁 ---")
            page = pg
        if Z.RE_FIG.match(t):
            t = "[圖]"
        elif Z.RE_TAB.match(t):
            t = "[表]"
        out.append(f"L{i}|{t.replace(Z.WARN, '')}")
    return "\n".join(out), len(lines)


def ask_claude(prompt):
    model = os.environ.get("CLAUDE_MODEL", "sonnet")
    claude = os.environ.get("CLAUDE_BIN", "claude")      # Windows 主機要用 claude.cmd（local-app 已設 CLAUDE_BIN）
    p = subprocess.run([claude, "-p", "--model", model, "--output-format", "text"],
                       input=prompt, capture_output=True, text=True, timeout=900)
    if p.returncode != 0:
        raise RuntimeError(f"claude -p 失敗：{p.stderr[:300]}")
    return p.stdout


def _codex_bin():
    import shutil
    for c in (os.environ.get("CODEX_BIN"), shutil.which("codex"),
              "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"):
        if c and os.path.exists(c):
            return c
    raise RuntimeError("找不到 codex（裝 ChatGPT App 或設 CODEX_BIN）")


def ask_codex(prompt):
    """Codex（GPT，用 ChatGPT 訂閱額度）。唯讀沙箱、不讀 stdin（否則會卡住等輸入）"""
    import tempfile
    out = tempfile.mktemp(suffix=".txt")
    p = subprocess.run([_codex_bin(), "exec", "-s", "read-only", "--skip-git-repo-check", "-o", out, prompt],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900)
    if p.returncode != 0 or not os.path.exists(out):
        raise RuntimeError(f"codex exec 失敗：{(p.stderr or p.stdout)[-300:]}")
    text = open(out, encoding="utf-8").read()
    os.remove(out)
    return text


def ask_ollama(prompt):
    model = os.environ.get("OLLAMA_MODEL", "qwen3-vl:8b")
    body = {"model": model, "stream": False, "format": "json", "think": False,
            "options": {"temperature": 0, "num_ctx": 16384},
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=1800))["message"]["content"]


def parse(text, n_lines):
    i = text.find("{")
    if i < 0:
        raise ValueError("AI 沒有回傳 JSON")
    d, _ = json.JSONDecoder().raw_decode(text[i:])       # 只取第一個完整的 JSON 物件（AI 偶爾會多回一段）
    ok = lambda x: str(x).lstrip("-").isdigit() and 0 <= int(x) < n_lines
    return {"q": sorted({int(x) for x in d.get("q", []) if ok(x)}),
            "noise": sorted({int(x) for x in d.get("noise", []) if ok(x)}),
            "fix": [f for f in d.get("fix", []) if isinstance(f, dict) and ok(f.get("l"))]}


BACKENDS = {"claude": ask_claude, "codex": ask_codex, "ollama": ask_ollama}
CHAIN = [b.strip() for b in os.environ.get("AI_CHAIN", "claude,codex,ollama").split(",") if b.strip()]


def judge(pyexam_dir, backend="claude", force=False):
    """backend="auto"：照 CHAIN 順序接力（預設 Claude → Codex），第一個成功的結果另存 ai_auto.json；
    全部失敗就丟 RuntimeError，呼叫端退回 Python 規則（照樣出 Word）。"""
    human = os.path.join(pyexam_dir, "ai_human.json")
    if os.path.exists(human):
        return json.load(open(human, encoding="utf-8"))
    if backend == "auto":
        out = os.path.join(pyexam_dir, "ai_auto.json")
        if os.path.exists(out) and not force:
            return json.load(open(out, encoding="utf-8"))
        errs = []
        for b in CHAIN:
            try:
                res = judge(pyexam_dir, b, force)
            except Exception as e:                       # 額度用完、沒網路、回傳格式錯…換下一個
                errs.append(f"{b}: {str(e)[:120]}")
                continue
            json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            return res
        raise RuntimeError("所有 AI 都失敗：" + "；".join(errs))
    out = os.path.join(pyexam_dir, f"ai_{backend}.json")
    if os.path.exists(out) and not force:
        return json.load(open(out, encoding="utf-8"))
    text, n = draft(pyexam_dir)
    t0 = time.time()
    raw = BACKENDS[backend](PROMPT + text)
    res = parse(raw, n)
    res["_meta"] = {"backend": backend, "sec": round(time.time() - t0, 1), "chars_in": len(PROMPT) + len(text),
                    "chars_out": len(raw)}
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pyexam_dir")
    ap.add_argument("--backend", choices=["auto", "claude", "codex", "ollama"], default="auto",
                    help="auto＝Claude → Codex → Ollama 接力（環境變數 AI_CHAIN 可改順序）")
    ap.add_argument("--force", action="store_true", help="不用快取，重問一次")
    a = ap.parse_args()
    r = judge(a.pyexam_dir, a.backend, a.force)
    print(f"題目開頭 {len(r['q'])} 個、雜訊 {len(r['noise'])} 行、改字 {len(r['fix'])} 處；{r['_meta']}")


if __name__ == "__main__":
    main()
