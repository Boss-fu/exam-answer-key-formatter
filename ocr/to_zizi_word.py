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
RE_Q = re.compile(r"^(?:[\(（]?\s*[^\u4e00-\u9fff\s()（）]?\s*[\)）]\s*)?(\d{1,2})\s*[.．、。](?!\d)\s*(?:[\(（]\s*[A-Ea-e]?\s*[\)）]\s*)?")
# 正常選項多在行首或空白後；但 OCR 常吃掉 inline 選項之間的空白
# （``…b(B)(2)…``），仍要接受 B～E 的括號代號。附圖代號另由
# split_opts 的明確「圖」判斷排除，不能只靠放寬此 regex。
RE_OPT = re.compile(r"[\(（]\s*([A-E])\s*[\)）]|^[\(（]?\s*([A-E])\s*[\)）]|^[\(（]\s*([A-E])(?=[\u4e00-\u9fff])"
                    r"|(?<![A-Za-z0-9(（])([B-E])[\)）](?=\S)|[\(（]([B-E])(?=[\u4e00-\u9fff])")
# 選項代號常被 OCR 讀成「（13）」「（T）」「（及」或「（E.）」。
# 只在整行剛好有四個候選代號、且已辨識出的 A～D 都落在正確位置時才修；
# 一般的 (1)、(甲) 等選項內容不會滿足這個完整序列條件。
RE_DAMAGED_OPT = re.compile(r"[\(（]\s*(?:[A-Da-d]\s*[\)）]?|13\s*[\)）]?|[Tt及]\s*[\)）]{0,2}|[Ee]\s*[.．]\s*[\)）]?)")
RE_Q_NOANS = re.compile(r"^(?:[\(（]\s*[A-Ea-e]?\s*[\)）]\s*)?(\d{1,2})\s*[.．、](?!\d)\s*")
RE_ANS_START = re.compile(r"^[^\u4e00-\u9fff]{0,4}?[\(（]\s*[A-Ea-e]\s*[\)）]\s*(?=[\u4e00-\u9fff「])")
RE_MID_Q = re.compile(r"(?<=\S)(?=[\(（]\s*[^\u4e00-\u9fff\s()（）]?\s*[\)）]\s*\d{1,2}\s*[.．、。](?!\d))")
RE_SUBQ = re.compile(r"^[(（]\s*([1-9])\s*[)）]\s*(?=\S)")     # 題組小題「(1)下列敘述何者錯誤？」
RE_GROUP_SEC = re.compile(r"綜合|題組|非選")
RE_Q_LOOSE = re.compile(r"^[^\u4e00-\u9fff\d]{0,3}?(\d{1,2})\s*[.．、。]?\s*(?=[\u4e00-\u9fff「（(])")
# 題組引言除了「◎／閱讀／題組」標記，也常以「（一）附圖／如圖」起頭。
# 後者必須同時明示「下列問題」或題號範圍，不能把一般的單題附圖題當題組。
RE_INTRO = re.compile(
    r"^\s*[◎※●■◆]|回答\s*第?\s*\d+\s*[～~至\-－—]\s*\d+\s*題|閱讀.{0,12}回答|題組|"
    r"^\s*(?:[（(]\s*[一二三四五六七八九十]\s*[)）]\s*)?(?:附|如|下|右|左)圖.{0,45}?"
    r"(?:回答\s*(?:下列|第?\s*\d+\s*[～~至\-－—]\s*\d+)|下列(?:\s*\d+\s*[～~至\-－—]\s*\d+)?\s*問題)"
)
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


# 題組成員必須以卷面明示的題號範圍為準。這個規則刻意不接受單一
# 「回答第 N 題」：那通常只是題幹的一部分，並不是題組的成員宣告。
RE_GROUP_RANGE = re.compile(r"(?:(?:回答|作答|完成|請答|請回答)[^\d]{0,8}第?\s*)?(\d{1,2})\s*[～~至\-－—]\s*(\d{1,2})\s*題")


def group_range(text):
    """回傳題組明示的 ``(起題, 迄題)``，沒有可靠範圍則回傳 ``None``。"""
    m = RE_GROUP_RANGE.search(text or "")
    if not m:
        return None
    start, end = map(int, m.groups())
    return (start, end) if start <= end else None


def group_left(text, fallback):
    """題組尚可套用的小題數；未印範圍時保守使用呼叫端的既有上限。"""
    bounds = group_range(text)
    return bounds[1] - bounds[0] + 1 if bounds else fallback


def recover_truncated_qnum(text, current):
    """復原 OCR 把兩位題號後半吃掉的極少數情形。

    例如第 10 題被讀成 ``1).承上題``、第 39 題被讀成
    ``3.承上題``。只接受預期下一題的十位數仍在行首，避免把
    題幹中的一般數字當成題號。
    """
    expected = current + 1
    if not 10 <= expected <= 99 or not text:
        return None
    tens = str(expected)[0]
    # ``1).`` 是 0 被誤讀成右括號的典型；一般 ``3.`` 則只收
    # 「承上題」這種明確題幹，避免改變正常的單位數題號。
    if re.match(rf"^{tens}\s*[)）]\s*[.．、]", text) and re.search(r"承\s*上\s*題", text):
        return expected
    if re.match(rf"^{tens}\s*[.．、]", text) and re.search(r"承\s*上\s*題", text):
        return expected
    return None


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
    # OCR 偶爾把「圖\n(B)的構造」斷行；這仍是「圖(B)」，不是 B 選項。
    # 正常選項以「(B)的…」開頭極少見，寧可保住圖題的整題結構。
    if re.match(r"^\s*[\(（]\s*[A-D]\s*[\)）]\s*(?:的|中)", line):
        return line, []
    damaged = list(RE_DAMAGED_OPT.finditer(line))
    if len(damaged) == 4:
        known = []
        for i, m in enumerate(damaged):
            letter = re.search(r"[A-Da-d]", m.group())
            if letter:
                known.append((i, letter.group().upper()))
        # 例如 A、（13）、C、D 或 A、（及）、（E.）、D：已知的位置
        # 和 A→D 一致，故中間殘字可安全依位置復原。
        if known and all(letter == "ABCD"[i] for i, letter in known):
            out, last = [], 0
            for i, m in enumerate(damaged):
                out.append(line[last:m.start()] + f"({'ABCD'[i]})")
                last = m.end()
            line = "".join(out) + line[last:]
    # 第四個分支會由 ``B)`` 起配對；明確排除前面接「(圖」的情況。
    idx = [m.start() for m in RE_OPT.finditer(line)
           if line[max(0, m.start() - 2):m.start()] not in ("(圖", "（圖")]
    if not idx:
        return line, []
    head = line[:idx[0]].strip()
    parts = [line[a:b].strip() for a, b in zip(idx, idx[1:] + [len(line)])]
    return head, [re.sub(r"^[\(（]?\s*([A-E])\s*[\)）]?\s*", r"(\1)", p, count=1) for p in parts]


def is_figure_label_block(stem, opts, line):
    """題幹已問完「如下圖／右圖」，其後、選項前的 OCR 是圖內標籤時不收入題文。"""
    return (not opts and "如下圖所示" in stem and
            bool(re.search(r"[？?。]\s*$", stem)) and bool(line.strip()))


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
        # 頁尾提示常跟下一題引言黏在同一行（「Bless第2附圖為葉的…」「共4頁Earth下頁還有試題請繼續作答」）：先就地剔除
        ln = RE_FOOT_INLINE.sub("", ln).strip()
        if not ln or RE_CAPTION_ONLY.match(ln):
            continue                          # 只有「圖(十)圖(十一)」「表(一)」的圖說行，題庫題目不收
        ln = fix_opt_labels(ln)
        # 題號框比題幹略高時，OCR 會把「( )13.」黏到上一行行尾：從那裡切成兩行
        for part in RE_MID_Q.split(ln):
            if part.strip():
                lines.append((page, part.strip()))
    return base, _page_order(lines)


# 題組引言的口吻：「…試回答下列問題」「附圖為…」「根據下列資料…」「閱讀下文」「請根據此圖回答第34~35題」
RE_GROUP_INTRO = re.compile(r"回答|下列問題|下列資料|閱讀|根據(?:下|此|上|附)|^(?:附|下|上|右|左)圖|如下(?:圖|表)|^(?:附|下)表")
# 選項 (D) 後面同一行黏著題組引言：在句號後切開
# 考卷結尾、答案欄說明、頁尾：出現在 (D) 之後就不是選項內容
RE_END_TAIL = re.compile(r"試題結束|試卷到此|答案欄|作答說明|背面還有|下頁還有|請翻頁|第\s*\d+\s*頁|共\s*\d+\s*頁|國[一二三]\S{0,4}第\s*\d")


RE_ARTICLE_HEAD = re.compile(r"(?:【\s*(?:文章|閱讀)[^】]{0,4}】)?\s*(?:請)?(?:根據|閱讀)(?:下列|以下|上述)?(?:文章|短文|資料|文字)(?:內容)?[^。]{0,12}?(?:回答|作答)[^。]{0,16}?題\s*[:：。]?\s*$")


def _bigrams(t):
    t = re.sub(r"[^\u4e00-\u9fff]", "", t)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def place_articles(questions, orphans):
    """閱讀題組的文章被 PDF 排在別處（題目先、文章最後）：題組引言只剩「請根據文章內容，回答41-43題：」。
    把孤立的文章行依原順序切成連續幾段，每段配給「用字最像」的那個只有標頭的題組（看它小題的題幹與選項）。"""
    if sum(len(t) for _, t, _ in orphans) < 80:
        return
    groups = {}
    for q in questions:
        if q["gid"] is not None:
            groups.setdefault(q["gid"], []).append(q)
    heads = []
    for gid, qs in groups.items():
        m = RE_ARTICLE_HEAD.search(qs[0]["group"])
        if m:
            heads.append((gid, qs, m.group().strip()))
    if not heads:
        return
    units = [(t, src) for _, t, src in orphans if not RE_ARTICLE_HEAD.search(t)]
    lines = [t for t, _ in units]

    def own_text(q):                          # 題幹扣掉黏上來的候選文章，才不會「自己像自己」
        stem = q["stem"]
        for t, src in units:
            if src is q:
                stem = stem.replace(t, "", 1)
        return stem + "".join(q["opts"])
    words = [_bigrams("".join(own_text(q) for q in qs)) for _, qs, _ in heads]
    n, m = len(lines), len(heads)
    score = [[len(_bigrams(t) & w) for w in words] for t in lines]
    # 動態規劃：行依序切成 m 段（可為空），第 j 段給第 j 個題組，總相似度最大
    best = [[-1] * (m + 1) for _ in range(n + 1)]; back = [[0] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0
    for j in range(1, m + 1):
        for i in range(n + 1):
            for k in range(i + 1):
                if best[k][j - 1] < 0:
                    continue
                v = best[k][j - 1] + sum(score[x][j - 1] for x in range(k, i))
                if v > best[i][j]:
                    best[i][j], back[i][j] = v, k
    if best[n][m] <= 0:
        return
    cuts, i = [], n
    for j in range(m, 0, -1):
        cuts.append((back[i][j], i)); i = back[i][j]
    cuts.reverse()
    for j in range(1, len(cuts)):             # 「【文章修改自：…】」「資料來源」是上一篇的結尾
        a, b = cuts[j]
        while a < b and re.match(r"\s*[【(（]?\s*(?:文章)?(?:修改|改寫|摘錄|節錄)自|\s*[【(（]?\s*資料來源", lines[a]):
            a += 1
        cuts[j - 1] = (cuts[j - 1][0], a); cuts[j] = (a, b)
    placed = []
    for (gid, qs, head), (a, b) in zip(heads, cuts):
        body = "".join(lines[a:b])
        if len(body) < 20:
            continue
        text = join(head, body)
        for q in qs:
            q["group"] = text
        placed.extend(units[a:b])
    for t, src in placed:                     # 從題幹拿掉已經搬去題組的文章
        if src is not None:
            src["stem"] = src["stem"].replace(t, "", 1).rstrip()
    used = "".join(t for t, _ in placed)
    for q in questions:                       # 別的題組引言裡如果黏到這些文章，拿掉
        if q["gid"] not in {h[0] for h in heads} and q["group"] and used and used[:20] in q["group"]:
            q["group"] = q["group"].replace(used, "")


def tail_fits(opts, tl):
    """(D) 之後的一行是否像 (D) 的換行：不是結尾／頁尾，而且接上後 (D) 不會比其他選項長太多。
    同一題的選項長度相近；(D) 突然多出一整段，就是下一個題組的引言或閱讀文章黏上來。"""
    if RE_END_TAIL.search(tl):
        return False
    others = [len(o) for o in opts[:-1]]
    if not others:
        return True
    longest = max(others)
    return len(opts[-1]) + len(tl) <= max(longest * 1.6, longest + 14)


RE_TAIL_INTRO = re.compile(r"(?<=[。.．?？])(?=[^。]{10,}?(?:回答|下列問題|閱讀|根據|(?:附|下|上|右|左)圖|如下))")
RE_OPT_TOKEN = re.compile(r"[（(]\s*([^()（）\s]{1,2})\s*[)）]")


def fix_opt_labels(ln):
    """選項代號認錯（「(3)丙 (C)戊 (D)庚」的 (3) 其實是 (B)）：同一行至少兩個正確代號、
    錯的那個夾在順序上該有的位置，就依序補成正確字母。只動單一字元、且整行代號能排成連續字母時才改。"""
    # ``(圖B)`` 不是選項 token。排除它，否則前面的「圖」會讓後面
    # 真正的 (A)～(D) 序列整組位移，並把圖號改成選項 B。
    toks = [m for m in RE_OPT_TOKEN.finditer(ln)
            if len(m.group(1)) == 1 and not re.search(r"[\u4e00-\u9fff]", m.group(1))]
    good = [(i, "ABCDE".index(m.group(1).upper())) for i, m in enumerate(toks) if m.group(1).upper() in list("ABCDE")]
    if len(good) < 2 or len(toks) > 5:
        return ln
    i0, l0 = good[0]
    want = [l0 - i0 + k for k in range(len(toks))]          # 以第一個正確代號推回每個位置該是哪個字母
    if want[0] < 0 or want[-1] > 4 or any(want[i] != l for i, l in good):
        return ln
    out, last = [], 0
    for k, m in enumerate(toks):
        lab = m.group(1)
        if lab.upper() not in list("ABCDE") and len(lab) == 1 and not re.match(r"[\u4e00-\u9fff]", lab):
            out.append(ln[last:m.start()] + f"({'ABCDE'[want[k]]})"); last = m.end()
    return "".join(out) + ln[last:] if out else ln


RE_FOOT_INLINE = re.compile(r"[,，]?\s*(?:共\s*\d+\s*[頁贝]\s*)?(?:[A-Z][a-z]{3,5}\s*)?(?:下\s*頁\s*還\s*有\s*試?\s*題\s*[,，、]?\s*請?\s*繼?\s*續\s*作\s*答)|"
                            r"背\s*面\s*(?:還|尚)\s*有\s*(?:試\s*)?題\s*目?|試\s*題\s*已\s*結\s*束.*$|請?\s*再\s*仔\s*細\s*檢\s*查\s*一?\s*下?\s*[。!！]?|"
                            r"(?:Bless|Earth|Farth)\s*(?:第\s*\d+\s*[頁贝]?)?")
RE_CAPTION_ONLY = re.compile(r"^(?:\s*[圖表]\s*[（(]\s*[一二三四五六七八九十0-9]{1,3}\s*[）)]\s*)+[。.]?$")
RE_PAGE_NO = re.compile(r"(?:^|[^\d])第\s*([0-9]{1,2})\s*[頁贝](?:\s*[,，、]?\s*共\s*[0-9]{1,2}\s*[頁贝])?\s*$|^\s*共\s*[0-9]{1,2}\s*[頁贝]\s*[,，、]?\s*第\s*([0-9]{1,2})\s*[頁贝]")


def _page_order(lines):
    """掃描順序錯（113 碧華：PDF 第 1 頁其實是卷面「第3頁」）時，依頁尾印的頁碼把頁重排。
    至少一半的頁讀得到頁碼、而且彼此不重複才動；讀不到的頁依原順序補上剩下的頁碼。頁號（找圖用）不變。"""
    pages = sorted({p for p, _ in lines})
    found = {}
    for p, t in lines:
        m = RE_PAGE_NO.search(t)
        if m:
            found.setdefault(p, int(m.group(1) or m.group(2)))
    n = len(pages)
    if n < 2 or len(found) * 2 < n or len(set(found.values())) != len(found) or not all(1 <= v <= n for v in found.values()):
        return _order_by_qnum(lines, pages)
    rest = iter(sorted(set(range(1, n + 1)) - set(found.values())))
    printed = {p: found[p] if p in found else next(rest) for p in pages}
    if [printed[p] for p in pages] == sorted(printed.values()):
        return lines
    order = sorted(pages, key=lambda p: printed[p])
    return [ln for p in order for ln in lines if ln[0] == p]


def _order_by_qnum(lines, pages):
    """頁尾沒有頁碼時，改看每頁題號範圍排頁（113 士林：PDF 第 3 頁是 42~50、第 4 頁是 30~41）。
    每頁取題號的 20%~80% 分位當範圍；各頁範圍互不重疊、排序後比原順序整齊才重排。
    大題換段從 1 重編時範圍會重疊 → 不動，避免把第二段搬到最前面。"""
    import statistics
    rng = {}
    for p in pages:
        ns = sorted(int(m.group(1)) for q, t in lines if q == p for m in [RE_Q.match(t)] if m and int(m.group(1)) <= 80)
        if len(ns) < 3:
            return lines                     # 有頁抓不到足夠題號（圖頁、答案卷）就不冒險
        rng[p] = (ns[len(ns) // 5], ns[(len(ns) * 4) // 5], statistics.median(ns))
    order = sorted(pages, key=lambda p: rng[p][2])
    if order == pages:
        return lines
    for a, b in zip(order, order[1:]):
        if rng[a][1] >= rng[b][0]:
            return lines                     # 範圍重疊（換段重編或認錯很多）→ 不重排
    return [ln for p in order for ln in lines if ln[0] == p]


# 頁碼倒過來寫（「共4頁，第1頁」）、答案卡貼紙提示、「選擇題：(每題2分…)」配分說明、「※請用2B鉛筆…」作答說明
RE_MISC_HEAD = re.compile(r"^\s*(?:共|其|合)\s*[0-9OoIl]{1,3}\s*[頁贝]\s*[,，、]?\s*第\s*[0-9OoIl]{1,3}\s*[頁贝]\s*$|"
                          r"貼\s*上?\s*不\s*透|不\s*透\s*光|光\s*貼\s*紙|"
                          r"^\s*[單多]?\s*選\s*擇\s*題\s*[：:]?\s*[（(]\s*每\s*題|"
                          r"^\s*[※＊*]\s*請.{0,20}(?:作\s*答|鉛\s*筆|答\s*案\s*卡)|"
                          r"Docu\s*Freezer|Created\s+by\s+(?:free|trial)\s+version")     # PDF 轉檔軟體試用版浮水印


# 「說明」欄的編號條目（「1.請用2B鉛筆作答」「2.本試卷共有50題」「3.範圍：3-1~4-2」）：前面有題號也是卷頭
RE_INSTR = re.compile(r"^\s*(?:[（(]\s*[)）]\s*)?(?:[（(]?\s*\d{1,2}\s*[.．、)）]\s*)?(?:請\s*用\s*2\s*B|請\s*將\s*答\s*案|答\s*案\s*[卷卡].{0,6}請\s*用|本\s*試\s*卷\s*共|試\s*卷\s*共\s*有|"
                      r"範\s*圍\s*[:：]|作\s*答\s*時\s*間|答\s*案\s*請\s*(?:劃|畫|填|寫))")
# 有些試卷把電腦卡注意事項排成「2.」「3.」「4.」。這些行雖符合
# 題號格式，仍屬卷頭；只接受讀卡／劃記的多個明確詞，避免濾掉一般題幹。
RE_CARD_INSTR_WORDS = ("電腦卡", "答案卡", "讀卡機", "劃記", "塗記", "2B鉛筆", "扣該科", "不予計分")


def is_card_instruction(text):
    compact = re.sub(r"\s", "", text or "")
    return sum(word in compact for word in RE_CARD_INSTR_WORDS) >= 2


def is_boilerplate_line(text, in_header=False):
    """卷頭、頁碼與跨頁提示不應進題目；保留題號開頭的正常題文。"""
    return ((in_header and RE_HEADER.search(text) and not RE_Q.match(text)) or
            bool(RE_PAGE_HEAD.search(text)) or bool(RE_FOOT.match(text)) or
            (bool(RE_MISC_HEAD.search(text)) and not RE_Q.match(text)) or bool(RE_INSTR.match(text)) or
            (in_header and is_card_instruction(text)))


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


def build(pyexam_dir, out_dir, dpi=300, src_name="", ai=None, collect=None):
    """collect（dict）有給的話，順便收集每題結構化內容給題庫 Excel 用：collect["questions"]、collect["meta"]。"""
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
    figure_kinds_path = os.path.join(pyexam_dir, base + "_figure_kinds.json")
    figure_kinds = json.load(open(figure_kinds_path, encoding="utf-8")) if os.path.exists(figure_kinds_path) else {}

    def new_group(text, left):
        """新題組引言。上一段引言還沒有任何小題用到（同一段引言被拆成兩行、兩個分支）就接起來，不要蓋掉。"""
        if group and "first" not in group:
            text = join(group["text"], text)
        return {"text": text, "left": left, "kinds": set()}

    def has_group_text(text):
        """圖／表佔位不是題組引言；題組必須有可讀的引言文字。"""
        bare = re.sub(r"〔(?:含圖|表格)\s*\d*〕", "", text or "")
        return bool(re.search(r"[\u4e00-\u9fffA-Za-z0-9]", bare))

    def flush():
        nonlocal cur, n_q, group
        if not cur:
            return
        if not cur["opts"] and not cur["figs_mid"] and not cur["figs_after"] and (
                len(re.sub(r"\W", "", cur["stem"])) < 4 or not re.search(r"[\u4e00-\u9fff]", cur["stem"])):
            # 只有一兩個字、或整段沒有中文（「118 118」這類圖表刻度）、又沒選項沒圖的「題目」是雜訊
            cur = None                         # 只有一兩個字、沒選項沒圖的「題目」是手寫雜訊（例如一個 0）
            return
        n_q += 1
        spill = ""
        tails = cur.pop("tail", [])
        for k, tl in enumerate(tails):            # (D) 之後的換行：像選項尾巴才接回最後一個選項
            if not tail_fits(cur["opts"], tl):
                spill = "".join(t for t in tails[k:] if not RE_END_TAIL.search(t))
                orphans.extend((n_q, t, None) for t in tails[k:] if not RE_END_TAIL.search(t))
                break
            cur["opts"][-1] = join(cur["opts"][-1], tl)
        if cur["opts"] and opts_done() and not spill:
            parts = RE_TAIL_INTRO.split(cur["opts"][-1], maxsplit=1)
            if len(parts) == 2 and len(parts[1]) >= 12:
                cur["opts"][-1], spill = parts[0], parts[1]   # (D) 後面黏到的是下一個題組的引言
        raw = cur["stem"] + "".join(cur["opts"])
        group_active = bool(group and has_group_text(group["text"]))
        group_kinds = set(group.get("kinds", ())) if group_active else set()
        rec = {"n": n_q, "pn": cur.get("pn"), "tf": tf_sec, "stem": cur["stem"], "opts": list(cur["opts"]),
               "kinds": sorted(set(cur.get("kinds", ())) | group_kinds), "group_kinds": sorted(group_kinds),
               "review": "⚠" in raw, "group": "", "gid": None}
        if group_active and (group["left"] is None or group["left"] > 0):
            rec["group"] = group["text"]; rec["gid"] = group.setdefault("first", n_q)
            if group["left"] is not None:
                group["left"] -= 1
        questions.append(rec)
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
        if len(re.sub(r"\W", "", spill)) >= 12:   # (D) 後面多出來的段落：下一個題組的引言，放在本題之後
            C.append(B.p_intro(markup_safe(spill)))
            group = new_group(spill, group_left(spill, 3))
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

    tmp = tempfile.mkdtemp(prefix="zzword_")
    figdir = os.path.join(tmp, "figs")
    os.makedirs(figdir)
    B.start(os.path.join(tmp, "build"), figdir)
    in_header = True
    qn = 0
    num_off, sec_qn = 0, None
    group_sec, group_raw = False, 0
    tf_sec = False                   # 是非題大題：題庫 Excel 每題補「(A)正確 (B)錯誤」
    group = None                     # 目前生效的題組引言 {"text", "left"(剩幾題，None＝到下個題組為止), "first"}
    orphans = []                     # (D) 之後切出來的整段文字（逐行）：可能是排在別處的閱讀文章
    questions = []  # 綜合題：大題號「1.」是題組引言，「(1)(2)…」小題才各算一題（題庫 Excel 也這樣算）     # 大題換段後題號從 1 重編（「二、題組題」1~15）：接在上一段後面編（36~50）

    def qnum(t):
        """題號（容許手寫符號黏在前面、少了點號）；號碼必須接在上一題後面 +1～+3，避免 10m/s、2.0 這類被當題號"""
        m = RE_Q.match(t) or RE_Q_LOOSE.match(t)
        if not m:
            return None, None
        if RE_Q.match(t) and re.search(r"[\(（]\s*[A-Ea-e]\s*[\)）]\s*$", t[:m.end()]) and re.search(r"[\(（]\s*B\s*[\)）]", t[m.end():]):
            m = RE_Q_NOANS.match(t)           # 「12.(A)甲 (B)乙」：括號裡的 A 是選項，不是手寫答案
        n = int(m.group(1))
        repaired = recover_truncated_qnum(t, qn)
        if repaired is not None:
            n = repaired
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
            para = fig_para(pg, int(mf.group(1))) if RE_FIG.match(t) else None
            if para is None:
                continue
            kind = figure_kinds.get(f"p{pg}_fig{int(mf.group(1))}", "表格" if RE_TAB.match(t) else "圖片")
            if cur is not None:
                cur.setdefault("kinds", set()).add(kind)
            if cur is None:
                if group is not None and has_group_text(group["text"]):
                    group.setdefault("kinds", set()).add(kind)
                C.append(para)
            elif cur["opts"]:
                cur["figs_after"].append(para)
            else:
                cur["figs_mid"].append(para)
            continue
        if len(t) <= 80 and re.match(r"^\s*[一二三四五六七八九十]{1,3}\s*[、.．，,]?\s*[^\s]{0,4}?題", t):
            if ("是非" in t) != tf_sec:
                flush()                      # 先存好上一大題最後一題（是非題最後一題才會補到 (A)正確 (B)錯誤）
            tf_sec = "是非" in t          # 大題標題：是非題開始／別的大題開始
        if is_boilerplate_line(t, in_header):
            continue                          # 卷頭（每頁都可能重印）、頁碼：由茲茲橫幅取代
        if RE_SECTION.match(t) and len(t) <= 60:
            sec_qn = qn
            group_sec, group_raw = bool(RE_GROUP_SEC.search(t)), 0
            group = None
            in_header = False
            flush()
            sec = t.replace("（", "【", 1).replace("(", "【", 1) if "【" not in t else t
            sec = re.sub(r"[)）]\s*$", "】", sec) if "【" in sec and "】" not in sec else sec
            C.append(B.p_section(markup_safe(sec)) if "【" in sec else B.p_intro("^" + markup_safe(sec) + "^"))
            if "閱讀" in t:
                # 「三、科普閱讀題」後通常先連續排文章、再排小題；標題仍是
                # Word 的大題標題，文章本身才是題組文章，故從空白引言開始累積。
                group = new_group("", None)
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
        if group_sec:
            mg = RE_Q.match(t) or RE_Q_LOOSE.match(t)
            if mg and int(mg.group(1)) == group_raw + 1 and len(t) - mg.end() >= 4 and \
                    any(RE_SUBQ.match(t2) for _, t2 in lines[i + 1:i + 14]):
                group_raw += 1                      # 下一個題組（大題號照順序 1、2、3…）
                sec_qn = None
                flush(); group = None
                start_q(qn + 1, t[mg.end():])
                cur["group"] = True
                continue
            ms = RE_SUBQ.match(t)
            if ms and cur is not None and len(t) - ms.end() >= 4:
                if cur.get("group") and int(ms.group(1)) == 1:
                    # 第 (1) 小題出現：剛才的「題」其實是題組引言 → 改成引言段落，不佔題號
                    C.append(B.p_intro(markup_safe(cur["stem"])))
                    C.extend(cur["figs_mid"] + cur["figs_after"])
                    group = new_group(cur["stem"], None)
                    cur = None
                    qn -= 1
                start_q(qn + 1, t[ms.end():])
                continue
        m0 = RE_Q.match(t)
        if m0 and cur is not None and not cur.get("pn") and not cur["opts"] and not cur.get("group") and \
                int(m0.group(1)) + num_off == qn and len(t) - m0.end() >= 4:
            # 前一段沒印題號、沒選項，佔掉的號碼正好是這行印的題號 → 前一段是題組引言（「小張將某植物的葉片…」），
            # 這行才是那一題。不這樣接，後面每一題都會因「題號沒有變大」被拒，整串黏在一起
            C.append(B.p_intro(markup_safe(cur["stem"])))
            C.extend(cur["figs_mid"] + cur["figs_after"])
            group = new_group(cur["stem"], group_left(cur["stem"], 3))
            cur = None
            qn -= 1
            start_q(qn + 1, t[m0.end():])
            cur["pn"] = qn
            continue
        mc = re.match(r"^[^\u4e00-\u9fff]{0,6}?(?=承\s*上\s*題)", t)
        if mc is not None and cur is not None and (cur["opts"] or cur.get("pn")) and not RE_Q.match(t):
            # 「承上題」開頭一定是新題（題號被手寫蓋掉或認成「1).」也一樣）
            start_q(qn + 1, t[mc.end():])
            continue
        n, mq = qnum(t)
        if n is not None and sec_qn is not None:
            if n != int(mq.group(1)):
                num_off = sec_qn             # 換段後第一題用了換算 → 這一段後面的題號都加同樣的位移
            sec_qn = None
        # 行首是空白作答括號「( )10.」本身就是新題的證據：上一題選項字母被擦掉、認不出 (A) 時也要接得上
        # 中間漏一題（題號被擦／蓋掉）：嚴格題號＋後面一長串中文，跳號 +2 也接受
        skip_ok = n == qn + 2 and RE_Q.match(t) is not None and len(re.findall(r"[\u4e00-\u9fff]", t[mq.end():])) >= 8 if n is not None else False
        if n is not None and (cur is None or cur["opts"] or n == qn + 1 or skip_ok or re.match(r"[\(（]\s*[\)）]", t)):
            start_q(n, t[mq.end():])
            cur["pn"] = n                    # 卷面印的題號（換段重編已換算）；題庫 Excel 的題號以它為準
            continue
        # 題號被蓋掉、只剩作答括號裡的手寫答案：「(A)量筒內原有18.0mL的水…」「B (B)孟純以…」
        # 括號後面是一長句（或有問號）、同一行沒有別的選項，而且後面接著從 (A) 開始的一組選項 → 是新題，不是選項 (A)
        ma = RE_ANS_START.match(t)
        if ma and cur and cur["opts"]:
            last = re.search(r"[A-Ea-e]", cur["opts"][-1][:4])
            got = re.search(r"[A-Ea-e]", t[:ma.end()])
            if last and got and ord(got.group().upper()) == ord(last.group().upper()) + 1:
                ma = None                    # 上一個選項是 (C)、這行是 (D)…：就是下一個選項，不是被蓋掉題號的新題
        if ma:
            rest = t[ma.end():]
            if not RE_OPT.search(rest) and (len(rest) >= 15 or re.search(r"[？?]", rest)) and opens_question(i):
                start_q(qn + 1, rest)
                continue
        head, opts = split_opts(t)
        if cur is not None and is_figure_label_block(cur["stem"], cur["opts"], t) and not opts:
            continue
        g = geo[i]
        if g and g["margin_ink"] >= MARGIN_INK and g["indent"] >= 1.0 and not (opts and not head) and opens_question(i):
            # 這行左邊的題號位置有墨跡（印刷題號／作答括號被手寫蓋住、OCR 沒認出字），後面又接著 (A) 開頭的選項 → 新題
            start_q(qn + 1, t)
            continue
        if RE_INTRO.search(t) and not opts:   # 題組引言：「請回答第 7～8 題」「◎ 閱讀下文…」
            flush()
            in_header = False
            C.append(B.p_intro(markup_safe(t)))
            group = new_group(t, group_left(t, 2))
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
            if group and "first" not in group:
                group["text"] = join(group["text"], t)   # 題組引言後、第一小題前的文章內容
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
        elif cur["opts"] and opts_done() and len(t) >= 12 and RE_GROUP_INTRO.search(t) and not cur.get("tail"):
            # (D) 之後、沒有題號的一行引言口吻 → 下一個題組的引言，不是 (D) 的尾巴
            flush()
            C.append(B.p_intro(markup_safe(t)))
            group = new_group(t, group_left(t, 3))
        elif cur["opts"]:
            if opts_done() or len(cur["opts"]) >= 3:
                cur.setdefault("tail", []).append(t)    # 先記著：可能是選項尾巴，也可能是下一題被蓋掉題號的題幹
            else:
                cur["opts"][-1] = join(cur["opts"][-1], t)   # 長選項換行
        else:
            cur["stem"] = join(cur["stem"], t)
    flush()
    for q in questions:                       # 沒選項的題目：問句後面又接一大段，也可能是排在別處的文章
        if not q["opts"]:
            mm = re.search(r"[？?。]\s*(?:[(（][^()（）]{0,12}[)）])?", q["stem"])
            if mm and len(q["stem"]) - mm.end() >= 80:
                orphans.extend((q["n"], u, q) for u in re.findall(r"[^。】]+[。】]?", q["stem"][mm.end():]))
    place_articles(questions, sorted(orphans, key=lambda o: o[0]))

    if collect is not None:
        collect["questions"] = questions; collect["meta"] = dict(meta)
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
    review.append(f"共 {n_q} 題、插入 {used_figs} 張圖（表格已以圖片保留）。附圖是自動裁切，請對照原卷檢查有沒有切到字或漏掉標籤。")
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
