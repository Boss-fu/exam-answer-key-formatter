"""題庫 Excel 的公式整理規則。"""
import unittest

from ocr.qbank_latex import formula_doubts, to_latex


class ToLatexTest(unittest.TestCase):
    def check(self, cases):
        for raw, want in cases:
            self.assertEqual(to_latex(raw), want, raw)

    def test_quantities(self):
        self.check([
            ("裝入30mL的水", r"裝入$30\,\mathrm{mL}$的水"),
            ("(A)4.60 cm (B)4.6 cm", r"(A)$4.60\,\mathrm{cm}$ (B)$4.6\,\mathrm{cm}$"),
            ("波速為60cm/s", r"波速為$60\,\mathrm{cm/s}$"),
            ("(B) 1.5 g/cm^{3}", r"(B) $1.5\,\mathrm{g/cm^{3}}$"),
            ("含量為 50mg/kg，", r"含量為 $50\,\mathrm{mg/kg}$，"),
            ("(C)9,192,631,770Hz", r"(C)$9{,}192{,}631{,}770\,\mathrm{Hz}$"),
            ("體積V(cm^{3})", r"體積V($\mathrm{cm^{3}}$)"),
            ("(A)204 cm^{2}204g", r"(A)$204\,\mathrm{cm^{2}}$$204\,\mathrm{g}$"),
            ("電流2A、電壓3V", r"電流$2\,\mathrm{A}$、電壓$3\,\mathrm{V}$"),
        ])

    def test_density_gets_cube(self):
        self.check([("密度10.5g/cm）", r"密度$10.5\,\mathrm{g/cm^{3}}$）")])

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

    def test_numbers_and_fractions(self):
        self.check([
            ("3×10^{8}", r"$3\times10^{8}$"),
            ("x^{2}", r"$x^{2}$"),
            ("經過 3/4週期", r"經過 $\frac{3}{4}$週期"),
            ("X=74，", r"$X=74$，"),
        ])

    def test_left_alone(self):
        for raw in ("下列何者正確?", "(A)甲 (B)乙", "第3次段考", "1公斤", "250公克/100公克水", "112/10/3",
                    "A1 與 B2 兩點", "COVID", "$v=\\frac{d}{t}$", "〔含圖〕", "AB兩地", "4.6", "2 3 4 L 6 7"):
            self.assertEqual(to_latex(raw), raw, raw)

    def test_idempotent(self):
        for raw in ("30mL的水", "H2O", "4℃", "1.5 g/cm^{3}"):
            once = to_latex(raw)
            self.assertEqual(to_latex(once), once, raw)


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
