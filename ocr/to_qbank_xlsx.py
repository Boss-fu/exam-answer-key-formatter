#!/usr/bin/env python3
"""pyexam 結果 → 題庫匯入 Excel 草稿（欄位同「待匯入」的 <卷>_題目.xlsx）。

填得出來的欄位：年度、來源學校、考試名稱、題號、題目（題幹＋每個選項一行）、題組文章、題組ID、
圖表類型（無／表格；其他圖留白讓 AI 或人判斷）、待確認（辨識信心低、沒選項、有圖時標 Y）。
分類欄位（冊數、大單元、知識點、題型、配分…）與答案、詳解留白，交給題庫系統的 AI 步驟。
    python ocr/to_qbank_xlsx.py <pyexam 輸出資料夾> [-o 輸出.xlsx] [--name 112_南市復興_國七上二段_生物_題目]
"""
import argparse, os, re, sys, tempfile

import openpyxl
from openpyxl.styles import Alignment, Font

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import to_zizi_word as Z  # noqa: E402
from qbank_latex import formula_doubts, to_latex  # noqa: E402

COLS = ["年度", "來源學校", "考試名稱", "題號", "答案", "冊數", "冊名", "領域", "大單元", "子單元", "知識點", "題目摘要",
        "題型", "步驟分", "記憶分", "概念分", "圖表類型", "判斷依據", "待確認", "題目", "詳解", "題組文章", "題組ID"]
CN = "一二三四五六七八九"
SUBJ = {"生物": "生物", "理化": "理化", "地科": "地科", "自然": "自然"}


def exam_info(name):
    """檔名「112_南市復興_國七上二段_生物_題目」→ 年度、學校、考試名稱、題組 ID 前綴。認不出的欄位留空。"""
    m = re.match(r"(\d{3})_([^_]+)_國([七八九])([上下])([一二三四1-4])段(?:_([^_]+))?", name)
    if not m:
        return {"年度": "", "來源學校": "", "考試名稱": "", "gid": ""}
    year, school, grade, term, nth, subj = m.groups()
    school = school if re.search(r"(國中|高中|中學)$", school) else school + "國中"
    nth = CN[int(nth) - 1] if nth.isdigit() else nth
    subj = SUBJ.get(subj or "", subj or "")
    exam = f"{school}-{grade}年級-{year}-{term}學期-第{nth}次段考" + (f"-{subj}" if subj else "")
    return {"年度": year + term, "來源學校": school, "考試名稱": exam, "gid": f"{school}-{grade}年級-{year}{term}-第{nth}次段考"}


GAN = "甲乙丙丁戊己庚辛壬癸"
GAN_FIX = {"王": "壬", "已": "己", "巳": "己", "Z": "乙", "T": "丁", "了": "丁", "內": "丙"}
CJK = r"[\u4e00-\u9fff]"

# 只收錄在題庫草稿與人工正解反覆出現、且詞組本身可辨識的生物用語。
# 不可將其中的單字全域取代（例如「已知」的「已」不能改成「己」）。
BIOLOGY_PHRASE_FIXES = (
    (r"禁(?=片|脈|子|綠素)", "葉"),
    (r"氯(?=息|孔|蒸|中二氧化碳)", "氣"),
    (r"樹干", "樹幹"),
    (r"造血干細胞", "造血幹細胞"),
    (r"(?<![人])進人", "進入"),
    (r"加人(?=碘液|本氏液|水|酒精)", "加入"),
    (r"放人(?=沸水|熱水)", "放入"),
    (r"丕(?=正確|是|再)", "不"),
    (r"冀便", "糞便"),
    (r"器宮", "器官"),
    (r"酸齡", "酸鹼"),
    (r"(?<!備)註(?=重|射)", "注"),
    (r"心博(?=p|動|次)", "心搏"),
    (r"含成作用", "合成作用"),
    (r"(?<!採)集綠素", "葉綠素"),
    (r"消化業", "消化液"),
    (r"紀果(?=、|，|。|$)", "結果"),
    (r"(?<=何)考(?=正確|錯誤)", "者"),
    (r"(?<=哪)雨(?=組)", "兩"),
    (r"(?<=[加放滲送注插流進吸倒投輸混侵鑽掉帶移嵌陷傳])人(?![類們員工口士民體群生])", "入"),   # 加人→加入
    (r"(?<=疏|肉|放)松|松(?=弛|散|軟|開)", "鬆"),
    (r"自已", "自己"),
)


# 頁首頁尾：「國一生物第1頁/共4頁」「第2頁」「，共4頁」「考題範圍：2-3~3-4」不屬於題目
RE_PAGE_MARK = re.compile(r"[，,]?\s*(?:國[一二三七八九]\S{0,4}?)?第\s*\d+\s*頁(?:\s*[/／，,]?\s*共\s*\d+\s*頁)?|[，,]?\s*共\s*\d+\s*頁"
                          r"|考(?:題|試)範圍\s*[:：]\s*[\d\-~～、.]+"
                          r"|畫卡作答第[一二三四五六]頁|第[一二三四五六]面完成[，,]?\s*請換下一張試卷|請直接用\s*2B\s*鉛筆作答於答案卡上"
                          r"|[七八九]?\s*年\s*_*\s*班\s*_*\s*(?:座\s*)?號\s*_*\s*姓名\s*[:：]\s*_*|班座號\s*[:：]\s*姓名\s*[:：]"
                          r"|(?:[一二三四五六]\s*[、.]\s*)?(?:單選|多選|非選|是非|填充|題組|閱讀素養)題\s*[(（][^()（）]{0,20}分[^()（）]{0,12}[)）]")


def fix_ocr(t):
    """AI 校對統計出的固定誤認（27 份待匯入 AI 校對 vs 草稿逐字比對）；只在有上下文佐證時換，不靠 AI。"""
    # 天干序列：前後兩字內有別的天干字才換（「丙丁己王癸」→「壬」、「(C)Z丙」→「乙」）
    chars = list(t)
    for i, c in enumerate(chars):
        if c in GAN_FIX:
            near = "".join(chars[max(0, i - 2):i] + chars[i + 1:i + 3])
            if sum(ch in GAN for ch in near) >= 1 and (c not in "內了" or sum(ch in GAN for ch in near) >= 2):
                chars[i] = GAN_FIX[c]
    t = "".join(chars)
    # 選項代號正後面、或單獨括起來的天干：「(D)已的後段」「(已)」「(D)T。」「(C)內(D)」
    t = re.sub(r"(?<=\([A-D]\))已(?!經|知|被|有|成|完|達|在|是|將|於)|(?<=\()已(?=\))", "己", t)
    t = re.sub(r"(?<=\([A-D]\))\s*T(?=\s*(?:$|\n|[。、，,(（可的為與和]))", "丁", t)
    t = re.sub(r"(?<=\([A-D]\))\s*內(?=\s*(?:$|\n|[。、，,(（構為與和]))", "丙", t)
    for pattern, replacement in BIOLOGY_PHRASE_FIXES:
        t = re.sub(pattern, replacement, t)
    t = re.sub(rf"(?<={CJK})\s*-{{2,}}\s*(?={CJK})", "一", t)          # 「哪--支」→「哪一支」
    t = re.sub(rf"(?<={CJK})[·・](?={CJK})", "、", t)                  # 「胃液·胰液」→「胃液、胰液」
    t = re.sub(rf"(?<={CJK})[OoＯ○](?=\s*(?:\(|$|\n))", "。", t)      # 句尾 O →。
    t = re.sub(rf"(?<={CJK})[OoＯ○](?={CJK})", "。", t)               # 「體溫O下列」：中文夾英文 O 是句號（數字 0 不動）
    t = RE_PAGE_MARK.sub("", t)
    t = re.sub(rf"(?<=物質)\s*×|×(?=\s*(?:由|來自|是|為))", "X", t)    # 「物質×」是變數 X 不是乘號
    t = re.sub(r"\?(?=\s*(?:$|\n|[（(]))", "？", t)                  # 題句與選項前的問號
    return t


def relabel(opts):
    """選項代號依位置重排成 (A)(B)(C)(D)：OCR 常把 (B) 認成 (3)、(D) 認成 (C)。"""
    if not 2 <= len(opts) <= 5:
        return opts
    out = []
    for k, o in enumerate(opts):
        m = re.match(r"^\s*[（(]\s*[^()（）\s]{1,2}\s*[)）]", o)
        out.append(f"({'ABCDE'[k]})" + o[m.end():] if m else o)
    return out


# 題庫的國中單選題固定四個選項。這裡刻意只在已經找到「剛好四個」
# 代號切點時才重建，不能從一段普通題文猜四等分。
#
# ``(C`` 少右括號、``()``、``(）``、``①）``、``(2）`` 都是實卷中
# 出現過的 OCR 代號殘骸。正常代號允許少右括號，才能把 ``(B)(C`` 拆開。
HALF_ALNUM = str.maketrans("ＡＢＣＤＥａｂｃｄｅ０１２３４５６７８９", "ABCDEabcde0123456789")
RE_OPTION_MARK = re.compile(
    r"[（(]\s*[A-Ea-e]\s*[）)]?|[（(]\s*[）)]|[①②③④]\s*[）)]?|[（(]\s*[1-4]\s*[）)]"
)
RE_LETTER_OPTION_MARK = re.compile(r"[（(]\s*[A-Da-d]\s*[）)]?")


def rebuild_options(stem, opts):
    """回傳 ``(題幹, 四個選項或原選項, 是否已重建)``。

    先把全形英數字（Ａ～Ｄ、１～４）換成半形，再由代號位置切開（中文標點不動）。只有剛好
    四個切點才採用結果；因此殘缺的三個選項不會被硬湊成四個。
    """
    stem = stem.translate(HALF_ALNUM)
    opts = [o.translate(HALF_ALNUM) for o in opts]
    if len(opts) == 4 and all(re.match(r"^\s*\([A-D]\)", o) for o in opts):
        return stem, relabel(opts), False

    # 換行只是可讀性，切點以整題順序為準；所以同一 OCR 行的四個
    # 全形選項，以及題幹尾端黏住的第一個選項都能一起處理。
    whole = "\n".join([stem] + opts)
    # 先只看英文字母代號。選項文字裡常有 ``(1)(甲)``，若混進
    # 通用殘骸 regex 會把「四個 inline 選項」誤判成八個切點。
    marks = list(RE_LETTER_OPTION_MARK.finditer(whole))
    if len(marks) != 4:
        marks = list(RE_OPTION_MARK.finditer(whole))
    if len(marks) != 4:
        return stem, opts, False
    head = whole[:marks[0].start()].strip()
    # 以位置保留空白選項（純圖片）；不能以文字是否非空來過濾。
    parts = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(whole)
        parts.append(whole[mark.end():end].strip())
    return head, [f"({'ABCD'[i]}){part}" for i, part in enumerate(parts)], True


def clean(t):
    t = re.sub(r"\s*⚠需確認", "", t or "")
    t = re.sub(r"〔(?:含圖|表格)\s*\d*〕", "", t)
    return re.sub(r"[ 　]{2,}", " ", t).strip()


RE_LEAD = re.compile(r"^[\s()（）\[\]［］0-9０-９.,，、。:：;；|]{1,10}(?=[\u4e00-\u9fff「A-Za-z])")


def question_text(q, with_option_count=False):
    stem = clean(q["stem"])
    m = RE_LEAD.match(stem)
    if m and re.search(r"[()（）\[\]［］]", m.group()):
        stem = stem[m.end():]             # 題號殘骸「40,()」「17.0)」「( )」：含括號才剝，避免吃掉「100公克…」這種題幹開頭
    if "圖片" in q["kinds"] or "表格" in q["kinds"]:
        stem += "〔含圖〕"
    opts = [clean(o) for o in q["opts"] if clean(o)]
    if q.get("tf") and not opts:
        opts = ["(A)正確", "(B)錯誤"]       # 是非題：題庫格式把正確／錯誤當成 (A)(B) 選項
    elif not q.get("tf"):
        stem, opts, _ = rebuild_options(stem, opts)
    else:
        opts = relabel(opts)
    text = fix_ocr("\n".join([stem] + opts))
    return (text, len(opts)) if with_option_count else text


def resolved_question_numbers(questions):
    """以相鄰題目的連續性裁決 OCR 印刷題號，避免單一誤讀帶歪後續。"""
    out, prev = [], 0
    for i, q in enumerate(questions):
        expected = prev + 1
        printed = q.get("pn")
        following = questions[i + 1].get("pn") if i + 1 < len(questions) else None
        # 原則仍以印刷題號為準（有些題組／頁面在 collect 前已做換段
        # 換算）；但若當前跳號、下一題卻恰好接「本來應有的下一號」，
        # 前後連續性足以判定當前數字是 OCR 誤讀，才改用流水號。
        if printed and printed > prev and not (printed > expected and following == expected + 1):
            num = printed
        else:
            num = expected
        out.append(num)
        prev = num
    return out


def loose_name(name):
    """不照標準格式的檔名（「114八上一段＿華興」「華興114-8上-1段」）盡量抓：年度、年級、學期、段次、校名。"""
    y = re.search(r"(1\d{2})", name); g = re.search(r"[國]?([七八九])\s*[年上下]?", name)
    t = re.search(r"[七八九]?\s*([上下])", name); e = re.search(r"([一二三四1-4])\s*段", name)
    sch = [w for w in re.split(r"[_＿\-－\s]+", name) if re.fullmatch(r"[\u4e00-\u9fff]{2,8}", w) and not re.search(r"[段上下年級題目卷]|生物|理化|地科|自然", w)]
    return {"year": y.group(1) if y else "", "grade": g.group(1) if g else "", "term": t.group(1) if t else "",
            "exam": e.group(1) if e else "", "school": sch[-1] if sch else ""}


def info_from_meta(meta, text="", name=""):
    """檔名不是「年度_學校_國七上二段_科目」格式時，合併卷頭辨識到的與寬鬆檔名解析到的學年度／學期／年級／段次／校名。"""
    alt = loose_name(name)
    year, term, grade, nth, school = (meta.get(k) or alt.get(k, "") for k in ("year", "term", "grade", "exam", "school"))
    nth = "一二三四"[int(nth) - 1] if str(nth).isdigit() else nth
    if not (year and school):
        return None
    grade = {"7": "七", "8": "八", "9": "九", "1": "七", "2": "八", "3": "九"}.get(str(grade), grade)
    school = school if re.search(r"(國中|高中|中學)$", school) else school + "國中"
    subj = next((k for k in ("生物", "理化", "地科", "自然") if k in text[:400]), "")
    exam = f"{school}-{grade}年級-{year}-{term}學期-第{nth}次段考" + (f"-{subj}" if subj else "")
    return {"年度": year + term, "來源學校": school, "考試名稱": exam, "gid": f"{school}-{grade}年級-{year}{term}-第{nth}次段考"}


def rows(pyexam_dir, name):
    info = exam_info(name)
    col = {}
    with tempfile.TemporaryDirectory() as tmp:
        Z.build(pyexam_dir, tmp, dpi=300, src_name=name + ".pdf", collect=col)
    if not info["考試名稱"]:
        txt = next((os.path.join(pyexam_dir, f) for f in os.listdir(pyexam_dir) if f.endswith(".txt")), None)
        info = info_from_meta(col.get("meta", {}), open(txt, encoding="utf-8").read() if txt else "", name) or info
    out = []
    questions = col.get("questions", [])
    for q, num in zip(questions, resolved_question_numbers(questions)):
        kinds = q["kinds"]
        why = []
        if q["review"]:
            why.append("有辨識信心低的字")
        text, option_count = question_text(q, with_option_count=True)
        group_text = fix_ocr(clean(q["group"]))
        mid = re.search(rf"(?:[(（]\s*[)）]\s*)?(?:[(（]\s*{num}\s*[)）]|(?<!\d){num}\s*[.．、])(?=\s*[一-鿿「])", text.split("\n")[0])
        if mid and mid.start() >= 12:
            # 題組引言黏在題幹前面：「…試回答下列46-47問題：圖(一)46.請問…」——本題題號之前的是引言
            head = text[:mid.start()].strip()
            if not group_text:
                group_text = head
            text = text[mid.end():].lstrip()
        lead = re.match(r"(\d{1,2})\s*[.．、](?=\s*[一-鿿「])", text)
        if lead and str(num).endswith(lead.group(1)):
            text = text[lead.end():]        # 題號殘骸「7.可運輸…」（第 37 題只剩個位數）
        if not q["opts"] and option_count == 0:
            why.append("沒抓到選項（非選題或選項在圖上）")
        if not q.get("tf") and option_count != 4:
            why.append("選項數不是 4")
        if "圖片" in kinds:
            why.append("含圖，請確認圖表類型")
        why.extend(formula_doubts(text))
        out.append({
            "年度": info["年度"], "來源學校": info["來源學校"], "考試名稱": info["考試名稱"], "題號": str(num),
            "圖表類型": "表格" if kinds == ["表格"] else ("" if kinds else "無"),
            "判斷依據": "；".join(why), "待確認": "Y" if why else "",
            "題目": to_latex(text), "題組文章": to_latex(group_text),
            "題組ID": "",
            "_gid": q["gid"],
        })
    # 題組 ID 用該題組第一題的「Excel 題號」（不是 Word 流水號）
    first = {}
    for r in out:
        if r["_gid"] is not None:
            first.setdefault(r["_gid"], r["題號"])
            r["題組ID"] = f"{info['gid']}-{first[r['_gid']]}" if info["gid"] else ""
    return out


def write(rs, path):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "題目"
    ws.append(COLS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rs:
        ws.append([r.get(k, "") for k in COLS])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["T"].width = 70; ws.column_dimensions["V"].width = 40
    wb.save(path)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("dir"); ap.add_argument("-o"); ap.add_argument("--name"); a = ap.parse_args()
    name = a.name or os.path.basename(a.dir.rstrip("/"))
    out = a.o or os.path.join(a.dir, name + "_草稿.xlsx")
    rs = rows(a.dir, name); write(rs, out)
    print(f"{out}：{len(rs)} 題，待確認 {sum(r['待確認'] == 'Y' for r in rs)} 題")


if __name__ == "__main__":
    main()
