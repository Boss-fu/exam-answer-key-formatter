"""題庫 Excel 的公式整理：把 OCR 文字裡的數值單位、次方、溫度、化學式、簡單分數包成 $...$ LaTeX。

規則對照題庫系統的匯入指令（index.html buildPrompt 的 LaTeX 規則）。只動認得出來的寫法，其他照舊；
已經被壓扁的分數（OCR 只留下「50×100%」）文字救不回來，由 formula_doubts 標出來請人確認。
"""
import re

UNITS = ("kcal|cal|kHz|Hz|ppm|kg|mg|g|km|cm|mm|m|mL|ml|L|min|s|kW|W|kJ|J|kPa|Pa|mA|A|mV|V|N|Ω")
UNIT = rf"(?:{UNITS})(?:\^\{{\d\}})?"
COMPOUND = rf"{UNIT}(?:\s*/\s*{UNIT})?"
NUMBER = r"[-−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-−]?\d+(?:\.\d+)?"
BEFORE = r"(?<![A-Za-z0-9.^{])"
AFTER = r"(?:(?<=\})|(?![A-Za-z0-9^]))"   # 次方結尾後面可以直接接數字（OCR 漏掉頓號：cm^{2}204g）

RE_MATH = re.compile(r"(\$[^$]*\$)")
RE_TEMP = re.compile(rf"{BEFORE}({NUMBER})?\s*(?:℃|°\s*C{AFTER})")
RE_TEMP_F = re.compile(rf"{BEFORE}({NUMBER})?\s*(?:℉|°\s*F{AFTER})")
RE_QTY = re.compile(rf"{BEFORE}({NUMBER})(?:(?=[LNAVWJ](?![A-Za-z]))|\s*(?![LNAVWJ](?![A-Za-z])))({COMPOUND}){AFTER}")   # 大寫單字母單位不可隔空格：尺上刻度「4 L」不是 4 公升
RE_BARE_EXP_UNIT = re.compile(rf"{BEFORE}((?:{UNITS})\^\{{\d\}}(?:\s*/\s*{UNIT})?|{UNIT}\s*/\s*(?:{UNITS})\^\{{\d\}}){AFTER}")
RE_SCI = re.compile(rf"{BEFORE}(\d+(?:\.\d+)?)\s*[×xX]\s*10\^\{{(-?\d+)\}}{AFTER}")
RE_POW = re.compile(rf"{BEFORE}([A-Za-z0-9]+)\^\{{(-?\d+)\}}{AFTER}")
RE_FRAC = re.compile(r"(?<![\d./,:\-])(\d{1,3})/(\d{1,3})(?![\d./,:\-])")
RE_VAR = re.compile(rf"(?<![A-Za-z0-9.]) ?([A-Za-z])\s*=\s*({NUMBER}){AFTER}")

ELEMENTS = ("He Li Be Ne Na Mg Al Si Cl Ar Ca Ti Cr Mn Fe Co Ni Cu Zn Br Ag Sn Ba Pt Au Hg Pb "
            "H B C N O F P S K I").split()
RE_CHEM_TOKEN = re.compile(r"(?<![A-Za-z0-9.^_{])((?:\(?(?:" + "|".join(sorted(ELEMENTS, key=len, reverse=True)) +
                           r")\d*\)?\d*){2,}|(?:Cl|O|H|N)2|O3)(\d?[+\-])?(?![A-Za-z0-9^_])")
RE_ION = re.compile(r"(?<![A-Za-z0-9.^_{])(Na|K|Ag|H|Cl|Cu|Fe|Ca|Mg|Zn|Pb|Ba|Al)(\d?[+\-])(?![A-Za-z0-9^_=\d])")
CHEM_WORDS = {"NaCl", "HCl", "NaOH", "KOH", "CuO", "MgO", "CaO", "ZnO", "KCl", "AgCl", "NaF", "KI", "NaBr", "CO", "NO"}


def _num(n):
    return n.replace(",", "{,}").replace("−", "-")


def _unit(u):
    return re.sub(r"\s+", "", u).replace("Ω", r"\Omega ")


def _fix_density(t):
    """g/cm 不是有效單位，是 OCR 吃掉立方；其他長度單位不猜。"""
    return re.sub(rf"(g\s*/\s*cm)(?![A-Za-z0-9^])", r"\1^{3}", t)


def _chem_body(token):
    return re.sub(r"(?<=[A-Za-z)])(\d+)", r"_\1", token)


def _chem(m):
    token, charge = m.group(1), m.group(2) or ""
    if not re.search(r"\d", token) and token not in CHEM_WORDS and not charge:
        return m.group(0)                     # 沒有數字的字母串（CO、AB、HI…）只認白名單，避免吃到英文或代號
    sup = f"^{{{charge}}}" if charge else ""
    return f"$\\mathrm{{{_chem_body(token)}{sup}}}$"


def _convert(t):
    t = _fix_density(t)
    t = RE_TEMP.sub(lambda m: f"${_num(m.group(1) or '')}\\,{{}}^\\circ\\mathrm{{C}}$", t)
    t = RE_TEMP_F.sub(lambda m: f"${_num(m.group(1) or '')}\\,{{}}^\\circ\\mathrm{{F}}$", t)
    t = RE_SCI.sub(lambda m: f"${m.group(1)}\\times10^{{{m.group(2)}}}$", t)
    return t


def _convert_plain(t):
    t = RE_QTY.sub(lambda m: f"${_num(m.group(1))}\\,\\mathrm{{{_unit(m.group(2))}}}$", t)
    return t


def _each_plain(t, fn):
    """只處理 $...$ 以外的文字，已經是公式的不重包。"""
    return "".join(p if RE_MATH.fullmatch(p) else fn(p) for p in RE_MATH.split(t))


def to_latex(t):
    if not t:
        return t
    for fn in (_convert, _convert_plain,
               lambda s: RE_BARE_EXP_UNIT.sub(lambda m: f"$\\mathrm{{{_unit(m.group(1))}}}$", s),
               lambda s: RE_ION.sub(lambda m: f"$\\mathrm{{{m.group(1)}^{{{m.group(2)}}}}}$", s),
               lambda s: RE_CHEM_TOKEN.sub(_chem, s),
               lambda s: RE_POW.sub(lambda m: f"${m.group(1)}^{{{m.group(2)}}}$", s),
               lambda s: RE_FRAC.sub(lambda m: f"$\\frac{{{m.group(1)}}}{{{m.group(2)}}}$", s),
               lambda s: RE_VAR.sub(lambda m: f"${m.group(1)}={_num(m.group(2))}$", s)):
        t = _each_plain(t, fn)
    return t


def formula_doubts(raw):
    """回傳公式上需要人工確認的原因：寫法本身救不回來，或選項單位互相矛盾。raw 是轉換前的文字。"""
    why = []
    if re.search(r"\d\s*[×xX]\s*100\s*[%％]", raw) or re.search(r"[×xX]\s*100\s*[%％]", raw) and "/" not in raw:
        why.append("公式可能遺失（分數被壓成一行），請對照原卷")
    opts = re.findall(r"\(\s*[A-Ea-e]\s*\)[^()（）]*", raw)
    powers = {p for o in opts for p in re.findall(r"cm(\^\{\d\})?", o)}
    if len(powers) > 1:
        why.append("選項的單位次方不一致，可能被 OCR 吃掉")
    return why
