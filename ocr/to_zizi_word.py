#!/usr/bin/env python3
"""pyexam 的 OCR 結果 → 茲茲範本 Word（題號自動編號 1.(　　)、選項定位點、茲茲橫幅、附圖插回原位）。
完全本地、不經 Claude、不花 token。排版沿用 exam-to-word 技能的 buildlib 與 template.docx。

用法：
    python3 ocr/to_zizi_word.py 考卷.pdf -o 輸出資料夾            # 先跑 pyexam，再組 Word
    python3 ocr/to_zizi_word.py --from-dir pyexam輸出資料夾 -o 輸出資料夾   # 已經跑過 pyexam
產出：<學年學期>-<國X科目>-<段次>-<學校>.docx ＋ 同名 _確認清單.txt
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "exam-to-word", "scripts"))
os.environ.setdefault("EXAM_TEMPLATE", os.path.join(REPO, "exam-to-word", "assets", "template.docx"))

RE_PAGE = re.compile(r"^【第 (\d+) 頁")
RE_SEP = re.compile(r"^───")
RE_FIG = re.compile(r"^〔含圖 (\d+)〕$")
RE_TAB = re.compile(r"^〔表格 (\d+)〕$")
# 題號，含後面的作答括號：「( )1.」「1.( B )」「1. (　)」——括號裡的手寫答案一起吃掉，不能被當成選項 (B)
RE_Q = re.compile(r"^(?:[\(（]?\s*[A-Ea-e]?\s*[\)）]\s*)?(\d{1,2})\s*[.．、。](?!\d)\s*(?:[\(（]\s*[A-Ea-e]?\s*[\)）]\s*)?")
RE_OPT = re.compile(r"[\(（]\s*([A-E])\s*[\)）]|^[\(（]?\s*([A-E])\s*[\)）]|^[\(（]\s*([A-E])(?=[\u4e00-\u9fff])"
                    r"|(?<![A-Za-z0-9(（])([B-E])[\)）](?=\S)|[\(（]([B-E])(?=[\u4e00-\u9fff])")
RE_Q_NOANS = re.compile(r"^(?:[\(（]\s*[A-Ea-e]?\s*[\)）]\s*)?(\d{1,2})\s*[.．、](?!\d)\s*")
RE_ANS_START = re.compile(r"^[^\u4e00-\u9fff]{0,4}?[\(（]\s*[A-Ea-e]\s*[\)）]\s*(?=[\u4e00-\u9fff「])")
RE_MID_Q = re.compile(r"(?<=\S)(?=[\(（]\s*[\)）]\s*\d{1,2}\s*[.．、。])")
RE_Q_LOOSE = re.compile(r"^[^\u4e00-\u9fff\d]{0,3}?(\d{1,2})\s*[.．、。]?\s*(?=[\u4e00-\u9fff「（(])")
RE_INTRO = re.compile(r"^\s*[◎※●■◆]|回答\s*第?\s*\d+\s*[～~至\-－—]\s*\d+\s*題|閱讀.{0,12}回答|題組")
RE_SECTION = re.compile(r"^[一二三四五六七八九十]{1,3}\s*[、.．]\s*\S")
RE_HEADER = re.compile(r"學\s*年\s*度|學\s*期|段\s*考|定期\s*評量|定\s*考|試\s*[卷題]|"
                       r"[七八九789]\s*年\s*級|年\s*級|科\s*目\s*(?:名\s*稱|代\s*(?:碼)?|名稱)?|"
                       r"命\s*題\s*(?:教\s*師)?|班\s*級|座\s*號|姓\s*名")
# 每一頁頂端重印的卷頭（校名＋學年度＋考試名稱，或班級座號姓名欄），出現在任何位置都不算題目內容
RE_PAGE_HEAD = re.compile(r"(?:學\s*年\s*度.{0,30}(?:段\s*考|評量|定\s*考|考試|試\s*[卷題]))|"
                          r"(?:班\s*級\s*[:：]?.{0,18}(?:座\s*號|姓\s*名))|"
                          r"(?:年\s*級|科\s*目\s*(?:名\s*稱|代\s*碼|代)?|命\s*題\s*(?:教\s*師)?)")
RE_FOOT = re.compile(r"^(?:第?\s*[0-9OoIl]{1,3}\s*[頁贝](?:\s*[,，/／]?(?:共|其|合)\s*[0-9OoIl]{1,3}\s*[頁贝])?|"
                     r"[0-9OoIl]{1,2}|[-－]\s*[0-9OoIl]{1,2}\s*[-－]|[頁贝]\s*[0-9OoIl]{1,3}|"
                     r"[0-9OoIl]{1,2}\s*[（(]\s*(?:共|其|合)\s*[0-9OoIl]{1,3}\s*[頁贝]\s*[)）]|"
                     r"(?:請\s*接\s*第?\s*[0-9OoIl]{1,3}\s*[頁贝]\s*(?:繼|续|續)\s*續?\s*作\s*答|"
                     r"背\s*面\s*(?:尚|還)?\s*有\s*試\s*[題题]))$")
CN = "一二三四五六七八九十"
BOOK = {("七", "上"): "第一冊", ("七", "下"): "第二冊", ("八", "上"): "第三冊",
        ("八", "下"): "第四冊", ("九", "上"): "第五冊", ("九", "下"): "第六冊"}
LABEL = {"七": "國七生物", "八": "國八理化", "九": "國九自然"}
WARN = "　⚠需確認"
MARGIN_INK = 9.9          # 停用：v7 實測學生在左邊空白寫字會大量誤判成新題（士林 73/40）；位置資料保留供之後改良


def markup_safe(t):
    """OCR 原文裡的 ~ * _ ^ 是 buildlib 的格式符號，先換成全形；pyexam 的 x^{2}／v_{1} 改成上下標。"""
    t = re.sub(r"_\{([^{}]{1,6})\}", lambda m: "\x01" + m.group(1) + "\x01", t)
    t = re.sub(r"\^\{([^{}]{1,6})\}", lambda m: "\x02" + m.group(1) + "\x02", t)
    t = t.replace("~", "～").replace("*", "＊").replace("_", "＿").replace("^", "＾").replace("§", "")
    t = re.sub("\x01([^\x01]*)\x01", r"~\1~", t)
    t = re.sub("\x02([^\x02]*)\x02", r"\1", t)       # buildlib 沒有上標標記：先照原字輸出
    return t.replace(WARN, "").replace("⚠", "")       # 不確定的地方列在確認清單，Word 內文保持乾淨


# 題幹的否定關鍵字照茲茲格式加粗底線：「下列何者*錯誤*」「哪一項*不包含*」
RE_EMPH = re.compile(r"(何者|何項|哪一?[項個些者種組]?|下列)(錯誤|不正確|不包含|不可能|不屬於|不是|無關|不同|不適當|不合理|不會)")


def emphasize(stem):
    return RE_EMPH.sub(lambda m: m.group(1) + "*" + m.group(2) + "*", stem, count=1)


def _w(t):
    return sum(2 if ord(c) > 0x2000 else 1 for c in re.sub(r"[*_^~]", "", t))


def layout_opts(opts):
    """茲茲選項排法：都很短 → 一行四個用定位點對齊；中等 → 一行兩個（(A)…　(B)…／(C)…　(D)…）；長 → 一行一個"""
    if not opts:
        return ""
    wmax = max(_w(o) for o in opts)
    if len(opts) <= 4 and wmax <= 13:
        return "\t".join(opts)
    if wmax <= 30:
        return "\n".join("　".join(opts[i:i + 2]) for i in range(0, len(opts), 2))
    return "\n".join(opts)


def join(a, b):
    if not a:
        return b
    if re.match(r"[A-Za-z0-9]", b[:1]) and re.match(r"[A-Za-z0-9.,)]", a[-1:]):
        return a + " " + b
    return a + b


def split_opts(line):
    """一行裡的 (A)…(B)… 拆成多個選項；回傳 (選項前的文字, [選項…])"""
    idx = [m.start() for m in RE_OPT.finditer(line)]
    if not idx:
        return line, []
    head = line[:idx[0]].strip()
    parts = [line[a:b].strip() for a, b in zip(idx, idx[1:] + [len(line)])]
    return head, [re.sub(r"^[\(（]?\s*([A-E])\s*[\)）]?\s*", r"(\1)", p, count=1) for p in parts]


def parse_meta(lines, src_name):
    head = " ".join(lines[:6]) + " " + src_name
    m = {}
    y = re.search(r"(1\d\d)\s*學年度", head) or re.search(r"(?<!\d)(1\d\d)(?!\d)", head)
    m["year"] = y.group(1) if y else ""
    if re.search(r"第\s*[二2]\s*學期|下學期|\d{3}\s*下|-2-", head):
        m["term"] = "下"
    elif re.search(r"第\s*[一1]\s*學期|上學期|\d{3}\s*上|-1-", head):
        m["term"] = "上"
    else:
        m["term"] = ""
    g = re.search(r"([七八九])\s*年級", head) or re.search(r"國([七八九])", head)
    gd = {"7": "七", "8": "八", "9": "九"}
    if not g:
        g2 = re.search(r"G([789])|([789])\s*年級", head)
        m["grade"] = gd.get((g2.group(1) or g2.group(2)) if g2 else "", "")
    else:
        m["grade"] = g.group(1)
    e = re.search(r"第\s*([一二三123])\s*次(?:段考|定期評量|定考|評量)", head) or re.search(r"([一二三])段", head)
    m["exam"] = {"1": "一", "2": "二", "3": "三"}.get(e.group(1), e.group(1)) if e else ""
    s = re.search(r"市立\s*(\S{2,4}?)(?:高級中學|國民中學|國中|高中|中學)", head) or re.search(r"(\S{2,4}?)國中", head)
    m["school"] = re.sub(r"(高級中學|國民中學|高中|國中|中學)$", "", s.group(1)) if s else ""
    if re.search(r"評量|段考|考試|試卷|學年|學期", m["school"]):     # 抓到考試名稱而不是校名（例：「…定期評量國中」）
        m["school"] = ""
        fs = re.search(r"([^\s_\-]{2,4}?)(?:國中|國民中學|高中|高級中學|附中)", src_name)
        if fs:
            m["school"] = fs.group(1)
    # 檔名常是「113-2-1_G9_新興附中」：學年-學期-段次_年級_學校，卷頭沒認到的欄位用檔名補
    f = re.search(r"(1\d\d)-([12])-([123])_G([789])[^_]*_([^_.]+?)(?:國中|國民中學|高中|附中)?(?:\.pdf)?$", src_name)
    if f:
        m["year"] = m["year"] or f.group(1)
        m["term"] = m["term"] or ("上" if f.group(2) == "1" else "下")
        m["exam"] = m["exam"] or "一二三"[int(f.group(3)) - 1]
        m["grade"] = m["grade"] or gd[f.group(4)]
        m["school"] = m["school"] or f.group(5)
    return m


def read_lines(pyexam_dir):
    """pyexam 的 .txt → [(頁, 行文字)]（去掉頁標頭、欄分隔、空行）。ai_judge 給 AI 的行號就是這個清單的索引，兩邊一定要用同一個函式"""
    txts = [f for f in os.listdir(pyexam_dir) if f.endswith(".txt")]
    if not txts:
        raise SystemExit(f"{pyexam_dir} 沒有 pyexam 的 .txt")
    base = txts[0][:-4]
    page, lines = 1, []
    for ln in open(os.path.join(pyexam_dir, txts[0]), encoding="utf-8").read().splitlines():
        ln = ln.strip()
        mp = RE_PAGE.match(ln)
        if mp:
            page = int(mp.group(1))
            continue
        if not ln or RE_SEP.match(ln):
            continue
        # 題號框比題幹略高時，OCR 會把「( )13.」黏到上一行行尾：從那裡切成兩行
        for part in RE_MID_Q.split(ln):
            if part.strip():
                lines.append((page, part.strip()))
    return base, lines


def is_boilerplate_line(text, in_header=False):
    """卷頭、頁碼與跨頁提示不應進題目；保留題號開頭的正常題文。"""
    return ((in_header and RE_HEADER.search(text) and not RE_Q.match(text)) or
            bool(RE_PAGE_HEAD.search(text)) or bool(RE_FOOT.match(text)))


AI_Q = "\x00Q"            # AI 判定「這行是題目開頭」的內部記號


def apply_ai(lines, ai):
    """套用 AI 的判斷：改錯字（只改 AI 指定的片段）、丟手寫雜訊行、在題目開頭行加記號"""
    fixes = {}
    for f in ai.get("fix", []):
        try:
            fixes.setdefault(int(f["l"]), []).append((str(f["from"]), str(f["to"])))
        except (KeyError, ValueError, TypeError):
            continue
    noise = {int(x) for x in ai.get("noise", []) if str(x).lstrip("-").isdigit()}
    starts = {int(x) for x in ai.get("q", []) if str(x).lstrip("-").isdigit()}
    out = []
    for i, (pg, t) in enumerate(lines):
        if i in noise and not (RE_FIG.match(t) or RE_TAB.match(t)):
            continue
        for a, b in fixes.get(i, []):
            if a and a in t and len(b) <= len(a) + 6:      # 只接受小幅修正，避免 AI 改寫題目
                t = t.replace(a, b, 1)
        out.append((pg, AI_Q + t if i in starts else t))
    return out


def build(pyexam_dir, out_dir, dpi=300, src_name="", ai=None):
    import buildlib as B
    base, lines = read_lines(pyexam_dir)
    ai_mode = False
    if ai:
        aj = os.path.join(pyexam_dir, f"ai_{ai}.json")
        if os.path.exists(aj):
            lines, ai_mode = apply_ai(lines, json.load(open(aj, encoding="utf-8"))), True

    meta = parse_meta([t for _, t in lines], src_name or base)

    # 每行的版面位置（pyexam 的 _lines.json）：依序比對文字，對到 lines 的每一行
    geo = [None] * len(lines)
    gj = os.path.join(pyexam_dir, base + "_lines.json")
    if os.path.exists(gj):
        gl = json.load(open(gj, encoding="utf-8"))
        key = lambda x: re.sub(r"\s|⚠需確認", "", x)[:10]
        p = 0
        for li, (_, t) in enumerate(lines):
            k = key(t)
            for q in range(p, min(len(gl), p + 30)):
                if k and key(gl[q]["text"]) == k:
                    geo[li], p = gl[q], q + 1
                    break
    review = []
    C = []
    cur = None                                # {"stem","opts","figs_mid","figs_after"}
    used_figs = used_tabs = n_q = 0

    def flush():
        nonlocal cur, n_q
        if not cur:
            return
        if not cur["opts"] and not cur["figs_mid"] and not cur["figs_after"] and len(re.sub(r"\W", "", cur["stem"])) < 4:
            cur = None                         # 只有一兩個字、沒選項沒圖的「題目」是手寫雜訊（例如一個 0）
            return
        n_q += 1
        for tl in cur.pop("tail", []):            # (D) 之後的換行：確定是選項的尾巴，接回最後一個選項
            cur["opts"][-1] = join(cur["opts"][-1], tl)
        raw = cur["stem"] + "".join(cur["opts"])
        stem = emphasize(markup_safe(cur["stem"])) or "（題幹未辨識，請對照原卷補上）"
        opts = [markup_safe(o) for o in cur["opts"]]
        body = layout_opts(opts)
        if cur["figs_mid"]:
            C.append(B.p_question(stem))
            C.extend(cur["figs_mid"])
            if body:
                C.append(B.opts_para(body))
        else:
            C.append(B.p_question(stem + ("\n" + body if body else "")))
        C.extend(cur["figs_after"])
        if not opts:
            review.append(f"第 {n_q} 題：沒抓到 (A)(B)(C)(D) 選項（可能是非選題，或選項在圖上）")
        if "⚠" in raw:
            review.append(f"第 {n_q} 題：有辨識信心低的字，請對照原卷")
        cur = None

    def fig_para(pg, k):
        nonlocal used_figs
        path = os.path.join(pyexam_dir, f"{base}_p{pg}_fig{k}.png")
        if not os.path.exists(path):
            return None
        # Word 內部的圖檔一律用英數檔名：中文檔名經 zip 打包後編碼會跑掉，Word 會找不到圖
        name = f"p{pg}_fig{k}"
        shutil.copy(path, os.path.join(figdir, name + ".png"))
        from PIL import Image
        w = Image.open(path).width / float(dpi)
        used_figs += 1
        return B.p_image(name, round(min(4.5, max(1.5, w * 0.85)), 2))   # 茲茲慣例：圖 2～5 吋寬

    tj = os.path.join(pyexam_dir, base + "_tables.json")
    tables = json.load(open(tj, encoding="utf-8")) if os.path.exists(tj) else {}

    def tab_para(pg, k):
        t = tables.get(f"p{pg}_t{k}")
        if not t:
            return None
        dxa = [w / float(dpi) * 1440 for w in t["widths"]]
        s = min(1.0, 10000.0 / sum(dxa))               # 版心約 10772 dxa，留一點邊
        widths = [max(400, int(w * s)) for w in dxa]
        rows = [[(markup_safe(c[0]), c[1]) if c[1] > 1 else markup_safe(c[0]) for c in r] for r in t["rows"]]
        nonlocal used_tabs
        used_tabs += 1
        if any(not c[0] for r in t["rows"] for c in r):
            review.append(f"第 {pg} 頁表格 {k}：有空白儲存格，請對照原卷補字")
        return B.tbl(rows, widths)

    tmp = tempfile.mkdtemp(prefix="zzword_")
    figdir = os.path.join(tmp, "figs")
    os.makedirs(figdir)
    B.start(os.path.join(tmp, "build"), figdir)
    in_header = True
    qn = 0
    num_off, sec_qn = 0, None     # 大題換段後題號從 1 重編（「二、題組題」1~15）：接在上一段後面編（36~50）

    def qnum(t):
        """題號（容許手寫符號黏在前面、少了點號）；號碼必須接在上一題後面 +1～+3，避免 10m/s、2.0 這類被當題號"""
        m = RE_Q.match(t) or RE_Q_LOOSE.match(t)
        if not m:
            return None, None
        if RE_Q.match(t) and re.search(r"[\(（]\s*[A-Ea-e]\s*[\)）]\s*$", t[:m.end()]) and re.search(r"[\(（]\s*B\s*[\)）]", t[m.end():]):
            m = RE_Q_NOANS.match(t)           # 「12.(A)甲 (B)乙」：括號裡的 A 是選項，不是手寫答案
        n = int(m.group(1))
        if sec_qn is not None and n <= 3 and n < qn and sec_qn + n <= qn + 3:
            n += sec_qn                      # 換段後題號重編：只在大題標題之後、號碼很小時才換算
        elif num_off and n + num_off > qn:
            n += num_off
        strict = RE_Q.match(t) is not None
        if strict and qn < n <= qn + 3:
            return n, m
        if not strict and n == qn + 1 and len(t) - m.end() >= 6:
            return n, m
        return None, None

    def opens_question(i):
        """第 i 行之後幾行內，先出現以 (A) 開頭的選項行，中間沒有別的題號 → 第 i 行是新題的題幹開頭"""
        for _, t2 in lines[i + 1:i + 16]:
            if RE_FIG.match(t2) or RE_TAB.match(t2) or len(t2) <= 4:   # 圖、表、圖上刻度雜訊
                continue
            if qnum(t2)[0] is not None or RE_SECTION.match(t2):
                return False
            _, o = split_opts(t2)
            if o:
                return o[0].startswith("(A)")
        return False

    def opts_done():
        return bool(cur and cur["opts"] and (cur["opts"][-1][:3] in ("(D)", "(E)") or len(cur["opts"]) >= 4))

    def start_q(n, body):
        nonlocal cur, qn, in_header
        in_header = False
        flush()
        qn = n
        head, opts = split_opts(body)
        cur = {"n": qn, "stem": head, "opts": opts, "figs_mid": [], "figs_after": []}

    for i, (pg, t) in enumerate(lines):
        mf = RE_FIG.match(t) or RE_TAB.match(t)
        if mf:
            para = (fig_para if RE_FIG.match(t) else tab_para)(pg, int(mf.group(1)))
            if para is None:
                continue
            if cur is None:
                C.append(para)
            elif cur["opts"]:
                cur["figs_after"].append(para)
            else:
                cur["figs_mid"].append(para)
            continue
        if is_boilerplate_line(t, in_header):
            continue                          # 卷頭（每頁都可能重印）、頁碼：由茲茲橫幅取代
        if RE_SECTION.match(t) and len(t) <= 60:
            sec_qn = qn
            in_header = False
            flush()
            sec = t.replace("（", "【", 1).replace("(", "【", 1) if "【" not in t else t
            sec = re.sub(r"[)）]\s*$", "】", sec) if "【" in sec and "】" not in sec else sec
            C.append(B.p_section(markup_safe(sec)) if "【" in sec else B.p_intro("^" + markup_safe(sec) + "^"))
            continue
        if ai_mode:
            # AI 模式：題目邊界以 AI 為準（AI 讀得懂「這句是新題開頭」，不會被手寫蓋掉的題號騙）
            if t.startswith(AI_Q):
                t = t[len(AI_Q):]
                m = RE_Q.match(t) or RE_ANS_START.match(t) or RE_Q_LOOSE.match(t)
                start_q(qn + 1, t[m.end():] if m else t)
                continue
            head, opts = split_opts(t)
            if cur is None:
                C.append(B.p_intro(markup_safe(t)))
                continue
            if opts:
                if head:
                    if cur["opts"]:
                        cur["opts"][-1] = join(cur["opts"][-1], head)
                    else:
                        cur["stem"] = join(cur["stem"], head)
                cur["opts"].extend(opts)
            elif cur["opts"]:
                cur["opts"][-1] = join(cur["opts"][-1], t)
            else:
                cur["stem"] = join(cur["stem"], t)
            continue
        n, mq = qnum(t)
        if n is not None and sec_qn is not None:
            if n != int(mq.group(1)):
                num_off = sec_qn             # 換段後第一題用了換算 → 這一段後面的題號都加同樣的位移
            sec_qn = None
        # 行首是空白作答括號「( )10.」本身就是新題的證據：上一題選項字母被擦掉、認不出 (A) 時也要接得上
        if n is not None and (cur is None or cur["opts"] or n == qn + 1 or re.match(r"[\(（]\s*[\)）]", t)):
            start_q(n, t[mq.end():])
            continue
        # 題號被蓋掉、只剩作答括號裡的手寫答案：「(A)量筒內原有18.0mL的水…」「B (B)孟純以…」
        # 括號後面是一長句（或有問號）、同一行沒有別的選項，而且後面接著從 (A) 開始的一組選項 → 是新題，不是選項 (A)
        ma = RE_ANS_START.match(t)
        if ma:
            rest = t[ma.end():]
            if not RE_OPT.search(rest) and (len(rest) >= 15 or re.search(r"[？?]", rest)) and opens_question(i):
                start_q(qn + 1, rest)
                continue
        head, opts = split_opts(t)
        g = geo[i]
        if g and g["margin_ink"] >= MARGIN_INK and g["indent"] >= 1.0 and not (opts and not head) and opens_question(i):
            # 這行左邊的題號位置有墨跡（印刷題號／作答括號被手寫蓋住、OCR 沒認出字），後面又接著 (A) 開頭的選項 → 新題
            start_q(qn + 1, t)
            continue
        if RE_INTRO.search(t) and not opts:   # 題組引言：「請回答第 7～8 題」「◎ 閱讀下文…」
            flush()
            in_header = False
            C.append(B.p_intro(markup_safe(t)))
            continue
        # 題號被手寫蓋掉、認不出來：用結構判斷新題——上一題選項已經完整（或還沒有任何題目），
        # 這一行不是選項、不是上一個選項的換行尾巴，且後面接著 (A) 開頭的選項
        if not opts and (cur is None or opts_done()) and opens_question(i):
            tail = cur is not None and len(t) <= 30 and re.search(r"[。．.）)]$", t) and not re.search(r"[？?]", t)
            if not tail:
                start_q(qn + 1, t)
                continue
        if cur is None:
            C.append(B.p_intro(markup_safe(t)))   # 例如「請注意，1~20題為地科」
            continue
        if opts:
            if opts[0].startswith("(A)") and any(o.startswith("(A)") for o in cur["opts"]):
                # 同一題裡又出現第二個 (A) → 一定是兩題黏在一起（題號被手寫蓋掉；一題不會有兩個 (A)）。
                # (D) 之後累積的行：第一行若像選項尾巴（短、句號收尾）留給上一題，其餘＋這行 (A) 前的字是新題題幹
                tails = cur.pop("tail", [])
                keep = 1 if tails and len(tails[0]) <= 30 and re.search(r"[。．.）)]$", tails[0]) else 0
                cur["tail"] = tails[:keep]
                stem = "".join(tails[keep:])
                start_q(qn + 1, join(stem, head) if head else stem)
                cur["opts"].extend(opts)
                continue
            if head:
                if cur["opts"]:
                    cur["opts"][-1] = join(cur["opts"][-1], head)
                else:
                    cur["stem"] = join(cur["stem"], head)
            cur["opts"].extend(opts)
        elif cur["opts"]:
            if opts_done() or len(cur["opts"]) >= 3:
                cur.setdefault("tail", []).append(t)    # 先記著：可能是選項尾巴，也可能是下一題被蓋掉題號的題幹
            else:
                cur["opts"][-1] = join(cur["opts"][-1], t)   # 長選項換行
        else:
            cur["stem"] = join(cur["stem"], t)
    flush()

    g, term = meta["grade"], meta["term"]
    label = LABEL.get(g, "國九自然")
    book = BOOK.get((g, term), "")
    exam = f"{meta['exam']}段" if meta["exam"] else "段考"
    school = meta["school"]
    repl = [("<w:t>國七生物</w:t>", f"<w:t>{label}</w:t>"),
            ("【112.百齡】", f"【{meta['year']}.{school}】")]
    if book:
        repl.append(("第一冊", book))
    repl.append(("<w:t>10</w:t>", f"<w:t>{exam}</w:t>"))
    repl.append(("Ch1(全)", "範圍待填"))
    name = f"{meta['year']}{term}-{label}-{exam}-{school}國中"
    name = re.sub(r"-+", "-", name).strip("-") or base
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.abspath(os.path.join(out_dir, name + ".docx"))
    B.finish(C, out, repl=repl)
    shutil.rmtree(tmp, ignore_errors=True)

    for k, v in (("學年度", meta["year"]), ("學期", term), ("年級", g), ("段次", meta["exam"]), ("學校", school)):
        if not v:
            review.insert(0, f"卷頭：{k}沒辨識到，橫幅／檔名可能不對")
    review.insert(0, f"橫幅推定：{label}／{book or '冊別?'}／{exam}／【{meta['year']}.{school}】；範圍（Ch）請手動填")
    review.append(f"共 {n_q} 題、插入 {used_figs} 張圖、{used_tabs} 個表格。附圖是自動裁切，請對照原卷檢查有沒有切到字或漏掉標籤。")
    rv = out[:-5] + "_確認清單.txt"
    with open(rv, "w", encoding="utf-8") as f:
        f.write("\n".join("・" + r for r in review) + "\n")
    return out, rv, {"questions": n_q, "figures": used_figs, "tables": used_tabs, "meta": meta}


def main():
    ap = argparse.ArgumentParser(description="pyexam OCR 結果 → 茲茲範本 Word")
    ap.add_argument("pdf", nargs="?")
    ap.add_argument("--from-dir", help="已跑過 pyexam 的輸出資料夾")
    ap.add_argument("-o", "--out", default="out")
    ap.add_argument("--dpi", type=int, default=350)
    a = ap.parse_args()
    src = ""
    if a.from_dir:
        d = a.from_dir
    else:
        if not a.pdf:
            ap.error("要給 PDF 或 --from-dir")
        import pyexam
        d = os.path.join(a.out, "_ocr")
        pyexam.process(a.pdf, d, dpi=a.dpi)
        src = os.path.basename(a.pdf)
    out, rv, _ = build(d, a.out, dpi=a.dpi, src_name=src)
    print("Word：", out)
    print("確認清單：", rv)


if __name__ == "__main__":
    main()
