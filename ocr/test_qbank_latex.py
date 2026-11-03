"""題庫 Excel 的公式整理規則。"""
import unittest

from ocr.qbank_latex import formula_doubts, latex_lint, to_latex


class ToLatexTest(unittest.TestCase):
    def check(self, cases):
        for raw, want in cases:
            self.assertEqual(to_latex(raw), want, raw)

    def test_quantities(self):
        self.check([
            ("裝入30mL的水", r"裝入$30\,\text{mL}$的水"),
            ("(A)4.60 cm (B)4.6 cm", r"(A)$4.60\,\text{cm}$ (B)$4.6\,\text{cm}$"),
            ("波速為60cm/s", r"波速為$60\,\text{cm/s}$"),
            ("(B) 1.5 g/cm^{3}", r"(B) $1.5\,\text{g/cm^{3}}$"),
            ("含量為 50mg/kg，", r"含量為 $50\,\text{mg/kg}$，"),
            ("(C)9,192,631,770Hz", r"(C)$9{,}192{,}631{,}770\,\text{Hz}$"),
            ("體積V(cm^{3})", r"體積V($\mathrm{cm^{3}}$)"),
            ("(A)204 cm^{2}204g", r"(A)$204\,\text{cm^{2}}$$204\,\text{g}$"),
            ("電流2A、電壓3V", r"電流$2\,\text{A}$、電壓$3\,\text{V}$"),
        ])

    def test_density_gets_cube(self):
        self.check([("密度10.5g/cm）", r"密度$10.5\,\text{g/cm^{3}}$）")])

    def test_temperature(self):
        self.check([
            ("4℃時", r"$4\,{}^\circ\mathrm{C}$時"),
            ("25°C的水", r"$25\,{}^\circ\mathrm{C}$的水"),
            ("-10.5℃", r"$-10.5\,{}^\circ\mathrm{C}$"),
            ("32℉", r"$32\,{}^\circ\mathrm{F}$"),
        ])

    def test_chemistry(self):
        self.check([
            ("水H2O與CO2", r"水$\mathrm{H_2O}$與$\mathrm{CO_2}$"),
            ("Ca(OH)2溶液", r"$\mathrm{Ca(OH)_2}$溶液"),
            ("H2SO4", r"$\mathrm{H_2SO_4}$"),
            ("Cu2+離子", r"$\mathrm{Cu^{2+}}$離子"),
            ("NaCl水溶液", r"$\mathrm{NaCl}$水溶液"),
        ])

    def test_dates_are_not_fractions(self):
        self.check([("今年11/1號碧華", "今年11/1號碧華"), ("於10/15進行", "於10/15進行"), ("經過 3/4週期", "經過 $\\frac{3}{4}$週期")])

    def test_numbers_and_fractions(self):
        self.check([
            ("3×10^{8}", r"$3\times10^{8}$"),
            ("x^{2}", r"$x^{2}$"),
            ("經過 3/4週期", r"經過 $\frac{3}{4}$週期"),
            ("X=74，", r"$X=74$，"),
        ])

    def test_calculation_fractions_stay_together(self):
        self.check([
            ("100/(100+500)*100%", r"$\frac{100}{100+500}\times100\%$"),
            ("(20-5)/5×100%", r"$\frac{20-5}{5}\times100\%$"),
            ("50÷(50+150)×100％", r"$\frac{50}{50+150}\times100\%$"),
            ("v=d/t", r"$v=\frac{d}{t}$"),
            ("密度=質量/體積", r"$\text{密度}=\frac{\text{質量}}{\text{體積}}$"),
            ("3*4，5x6，7X8", r"$3\times4$，$5\times6$，$7\times8$"),
            ("速率=60/3=20", r"$\text{速率}=\frac{60}{3}=20$"),
            ("5x10=50", r"$5\times10=50$"),
        ])

    def test_fraction_guards(self):
        self.check([
            ("120cm/4s", r"$120\,\text{cm}$/$4\,\text{s}$"),
            ("1/2 小時", r"$\frac{1}{2}$ 小時"),
            ("112/1", "112/1"),
        ])

    def test_left_alone(self):
        for raw in ("下列何者正確?", "(A)甲 (B)乙", "第3次段考", "1公斤", "250公克/100公克水", "112/10/3",
                    "A1 與 B2 兩點", "COVID", "$v=\\frac{d}{t}$", "〔含圖〕", "AB兩地", "4.6", "2 3 4 L 6 7"):
            self.assertEqual(to_latex(raw), raw, raw)

    def test_idempotent(self):
        for raw in ("30mL的水", "H2O", "4℃", "1.5 g/cm^{3}"):
            once = to_latex(raw)
            self.assertEqual(to_latex(once), once, raw)

    def test_repairs_known_broken_latex(self):
        self.check([
            (r"$6times10^{23}$", r"$6\times10^{23}$"),
            (r"$\frac{質量}{體積}$", r"$\frac{\text{質量}}{\text{體積}}$"),
        ])


class LatexLintTest(unittest.TestCase):
    def test_valid_generated_formats(self):
        self.assertEqual(latex_lint(to_latex("25°C，30mL，密度=質量/體積")), [])

    def test_reports_known_bad_latex(self):
        errors = latex_lint(r"$6times10^{23}$，$\frac{質量}{m}$，$29,\text{g}$，$x^{2$")
        self.assertEqual(errors, [
            "公式 1 有漏反斜線指令",
            "公式 2 的 \\frac 中文未用 \\text{}",
            "公式 3 的數字與 \\text{} 間不可加逗號",
            "公式 4 大括號不對稱",
        ])


class DoubtTest(unittest.TestCase):
    def test_flattened_fraction(self):
        self.assertTrue(formula_doubts("(A)50×100% (B) 36×100%"))

    def test_mixed_option_powers(self):
        self.assertTrue(formula_doubts("(A)10cm (B)20 cm (C)30cm (D)40cm^{3}"))

    def test_clean(self):
        self.assertEqual(formula_doubts("(A)10cm (B)20cm"), [])
        self.assertEqual(formula_doubts("下列何者正確？"), [])


if __name__ == "__main__":
    unittest.main()
