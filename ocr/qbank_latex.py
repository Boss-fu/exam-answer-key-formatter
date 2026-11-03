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
RE_LATEX_COMMAND = re.compile(r"(?<![A-Za-z\\\\])(frac|times|cdot|rightarrow|leftarrow|rightleftharpoons|text|mathrm|circ)(?![A-Za-z])")
RE_FRAC_CJK = re.compile(r"(\\frac\{)([^{}]*)(\}\{)([^{}]*)(\})")
RE_TEMP = re.compile(rf"{BEFORE}({NUMBER})?\s*(?:℃|°\s*C{AFTER})")
RE_TEMP_F = re.compile(rf"{BEFORE}({NUMBER})?\s*(?:℉|°\s*F{AFTER})")
RE_QTY = re.compile(rf"{BEFORE}({NUMBER})(?:(?=[LNAVWJ](?![A-Za-z]))|\s*(?![LNAVWJ](?![A-Za-z])))({COMPOUND}){AFTER}")   # 大寫單字母單位不可隔空格：尺上刻度「4 L」不是 4 公升
RE_BARE_EXP_UNIT = re.compile(rf"{BEFORE}((?:{UNITS})\^\{{\d\}}(?:\s*/\s*{UNIT})?|{UNIT}\s*/\s*(?:{UNITS})\^\{{\d\}}){AFTER}")
RE_SCI = re.compile(rf"{BEFORE}(\d+(?:\.\d+)?)\s*[×xX]\s*10\^\{{(-?\d+)\}}{AFTER}")
RE_POW = re.compile(rf"{BEFORE}([A-Za-z0-9]+)\^\{{(-?\d+)\}}{AFTER}")
RE_FRAC = re.compile(r"(?<![\d./,:\-])(\d{1,3})/(\d{1,3})(?![\d./,:\-])")
RE_VAR = re.compile(rf"(?<![A-Za-z0-9.]) ?([A-Za-z])\s*=\s*({NUMBER}){AFTER}")
# 只收「看得出是算式」的分子／分母。單位（120cm/4s）不會符合，
# 因此仍交給 RE_QTY 的單位處理；也不要把任意斜線當成分數。
CALC_OPERAND = r"(?:\([^()]+\)|\d+(?:\.\d+)?)"
RE_NUMERIC_CALC = re.compile(
    rf"(?<![A-Za-z0-9./])(?P<num>{CALC_OPERAND})\s*(?P<div>/|÷)\s*"
    rf"(?P<den>{CALC_OPERAND})(?P<tail>(?:\s*[×*xX]\s*\d+(?:\.\d+)?\s*[%％])?)(?![A-Za-z0-9./])"
)
RE_NAMED_CALC = re.compile(
    r"(?<![A-Za-z0-9])(?P<lhs>[A-Za-z]|[\u4e00-\u9fff]{1,6})\s*=\s*"
    r"(?P<num>[A-Za-z]|[\u4e00-\u9fff]{1,6})\s*(?P<div>/|÷)\s*"
    r"(?P<den>[A-Za-z]|[\u4e00-\u9fff]{1,6})(?![A-Za-z0-9])"
)
RE_NAMED_NUMERIC_CALC = re.compile(
    rf"(?<![A-Za-z0-9])(?P<lhs>[A-Za-z]|[\u4e00-\u9fff]{{1,6}})\s*=\s*"
    rf"(?P<num>(?:{NUMBER}))\s*(?P<div>/|÷)\s*(?P<den>(?:{NUMBER}))"
    rf"(?P<rest>\s*=\s*(?:{NUMBER}))?(?![A-Za-z0-9./=])"
)
RE_NUMERIC_PRODUCT_EQ = re.compile(
    rf"(?<![A-Za-z0-9.])(?P<a>(?:{NUMBER}))\s*[×*xX]\s*(?P<b>(?:{NUMBER}))\s*=\s*(?P<c>(?:{NUMBER}))(?![A-Za-z0-9.])"
)
RE_MULTIPLY = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*[×*xX]\s*(\d+(?:\.\d+)?)(?![A-Za-z0-9.])")

ELEMENTS = ("He Li Be Ne Na Mg Al Si Cl Ar Ca Ti Cr Mn Fe Co Ni Cu Zn Br Ag Sn Ba Pt Au Hg Pb "
            "H B C N O F P S K I").split()
RE_CHEM_TOKEN = re.compile(r"(?<![A-Za-z0-9.^_{])((?:\(?(?:" + "|".join(sorted(ELEMENTS, key=len, reverse=True)) +
                           r")\d*\)?\d*){2,}|(?:Cl|O|H|N)2|O3)(\d?[+\-])?(?![A-Za-z0-9^_])")
RE_ION = re.compile(r"(?<![A-Za-z0-9.^_{])(Na|K|Ag|H|Cl|Cu|Fe|Ca|Mg|Zn|Pb|Ba|Al)(\d?[+\-])(?![A-Za-z0-9^_=\d])")
CHEM_WORDS = {"NaCl", "HCl", "NaOH", "KOH", "CuO", "MgO", "CaO", "ZnO", "KCl", "AgCl", "NaF", "KI", "NaBr", "CO", "NO"}


def _num(n):
    return n.replace(",", "{,}").replace("−", "-")


def _unit(u):
    return re.sub(r"\s+", "", u).replace("Ω", r"\Omega")


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
    t = _convert_calculations(t)
    return t


def not_fraction(num, den, before, after):
    """「a/b」其實是學年學期（112/1）或日期（11/1 號、今年 3/15）時回 True。"""
    num, den = num.strip(), den.strip()
    if not (num.isdigit() and den.isdigit()):
        return False
    if re.fullmatch(r"1(?:0|1|2)\d", num) and den in {"1", "2"}:
        return True
    if re.match(r"\s*(?:號|日|[(（]?(?:週(?!期)|星期))", after):
        return True
    return int(num) <= 12 and int(den) <= 31 and bool(re.search(r"(?:今年|去年|明年|日期|於)\s*$", before))


def _strip_parens(s):
    """括號是 OCR 的分組符號，不是 \\frac 的一部分。"""
    s = re.sub(r"\s+", "", s)
    return s[1:-1] if s.startswith("(") and s.endswith(")") else s


def _math_name(s):
    return rf"\text{{{s}}}" if re.search(r"[\u4e00-\u9fff]", s) else s


def _convert_calculations(t):
    """把同一條可辨識的除法算式一次包成一個 $...$。"""
    def numeric(m):
        if not m.group("tail") and not_fraction(m.group("num"), m.group("den"), m.string[:m.start()], m.string[m.end():]):
            return m.group(0)                 # 112/1 學年學期、11/1 號日期
        tail = re.sub(r"[×*xX]", r"\\times", m.group("tail")).replace("%", r"\%").replace("％", r"\%")
        return rf"$\frac{{{_strip_parens(m.group('num'))}}}{{{_strip_parens(m.group('den'))}}}{tail}$"

    def named(m):
        return rf"${_math_name(m.group('lhs'))}=\frac{{{_math_name(m.group('num'))}}}{{{_math_name(m.group('den'))}}}$"

    def named_numeric(m):
        return (rf"${_math_name(m.group('lhs'))}=\frac{{{_num(m.group('num'))}}}{{{_num(m.group('den'))}}}"
                + (f"={_num(m.group('rest').split('=')[-1].strip())}" if m.group("rest") else "") + "$" )

    def product_eq(m):
        return rf"${_num(m.group('a'))}\times{_num(m.group('b'))}={_num(m.group('c'))}$"

    # 先處理完整連等式，避免後面的除法／乘法規則把它拆成多個 $...$。
    t = RE_NAMED_NUMERIC_CALC.sub(named_numeric, t)
    t = RE_NUMERIC_PRODUCT_EQ.sub(product_eq, t)
    t = RE_NAMED_CALC.sub(named, t)
    t = RE_NUMERIC_CALC.sub(numeric, t)
    return RE_MULTIPLY.sub(lambda m: rf"${m.group(1)}\times{m.group(2)}$", t)


def _simple_frac(m):
    """學年／學期 112/1、日期 11/1 號都不是數學分數。"""
    if not_fraction(m.group(1), m.group(2), m.string[:m.start()], m.string[m.end():]):
        return m.group(0)
    return rf"$\frac{{{m.group(1)}}}{{{m.group(2)}}}$"


def _convert_plain(t):
    # 題庫正解的量值單位用 \text{}；\mathrm{} 只保留給溫度與化學式。
    t = RE_QTY.sub(lambda m: f"${_num(m.group(1))}\\,\\text{{{_unit(m.group(2))}}}$", t)
    return t


def _each_plain(t, fn):
    """只處理 $...$ 以外的文字，已經是公式的不重包。"""
    return "".join(p if RE_MATH.fullmatch(p) else fn(p) for p in RE_MATH.split(t))


def _repair_formula(formula):
    """修正 OCR／既有題目中明確可判定的壞指令，不能猜的錯誤交給 lint。"""
    body = formula[1:-1]
    body = RE_LATEX_COMMAND.sub(r"\\\1", body)

    # \frac 的中文名稱必須進 \text{}，否則 KaTeX 會把每個中文字當數學變數。
    def cjk_frac(m):
        def text_if_cjk(value):
            return rf"\text{{{value}}}" if re.search(r"[\u4e00-\u9fff]", value) and r"\text{" not in value else value
        return m.group(1) + text_if_cjk(m.group(2)) + m.group(3) + text_if_cjk(m.group(4)) + m.group(5)
    body = RE_FRAC_CJK.sub(cjk_frac, body)
    return "$" + body + "$"


def _repair_formulas(t):
    return "".join(_repair_formula(p) if RE_MATH.fullmatch(p) else p for p in RE_MATH.split(t))


def latex_lint(t):
    """回傳題庫 KaTeX 的明確錯誤；空串列代表沒有偵測到已知壞格式。"""
    errors = []
    if t.count("$") % 2:
        errors.append("$ 未成對")
    for i, formula in enumerate(re.findall(r"\$([^$]*)\$", t), 1):
        stack = []
        for ch in formula:
            if ch == "{": stack.append(ch)
            elif ch == "}":
                if stack: stack.pop()
                else: errors.append(f"公式 {i} 大括號不對稱"); break
        else:
            if stack: errors.append(f"公式 {i} 大括號不對稱")
        if RE_LATEX_COMMAND.search(formula):
            errors.append(f"公式 {i} 有漏反斜線指令")
        if re.search(r"\\frac(?!\{)", formula):
            errors.append(f"公式 {i} 的 \\frac 缺少大括號")
        for m in re.finditer(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", formula):
            if any(re.search(r"[\u4e00-\u9fff]", part) and r"\text{" not in part for part in m.groups()):
                errors.append(f"公式 {i} 的 \\frac 中文未用 \\text{{}}")
                break
        if re.search(r"\d,\\text\{", formula):
            errors.append(f"公式 {i} 的數字與 \\text{{}} 間不可加逗號")
    return errors


def to_latex(t):
    if not t:
        return t
    for fn in (_convert, _convert_plain,
               lambda s: RE_BARE_EXP_UNIT.sub(lambda m: f"$\\mathrm{{{_unit(m.group(1))}}}$", s),
               lambda s: RE_ION.sub(lambda m: f"$\\mathrm{{{m.group(1)}^{{{m.group(2)}}}}}$", s),
               lambda s: RE_CHEM_TOKEN.sub(_chem, s),
               lambda s: RE_POW.sub(lambda m: f"${m.group(1)}^{{{m.group(2)}}}$", s),
               lambda s: RE_FRAC.sub(_simple_frac, s),
               lambda s: RE_VAR.sub(lambda m: f"${m.group(1)}={_num(m.group(2))}$", s)):
        t = _each_plain(t, fn)
    return _repair_formulas(t)


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
