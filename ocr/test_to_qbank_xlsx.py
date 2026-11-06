import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from to_qbank_xlsx import complete_single_choice_options, fix_ocr, rebuild_options, resolved_question_numbers


class FixOcrTest(unittest.TestCase):
    def test_biology_phrase_fixes_require_context(self):
        self.assertEqual(fix_ocr("禁片有氯孔，形成冀便"), "葉片有氣孔，形成糞便")
        self.assertEqual(fix_ocr("酸齡度與器宮功能"), "酸鹼度與器官功能")
        self.assertEqual(fix_ocr("若甲為分解作用；乙為含成作用"), "若甲為分解作用；乙為合成作用")
        self.assertEqual(fix_ocr("消化業可分解澱粉"), "消化液可分解澱粉")

    def test_does_not_apply_ambiguous_character_globally(self):
        self.assertEqual(fix_ocr("已知有人進入禁區"), "已知有人進入禁區")
        self.assertEqual(fix_ocr("備註與採集資料"), "備註與採集資料")

    def test_terminal_question_mark(self):
        self.assertEqual(fix_ocr("何者正確？\n(A)甲"), "何者正確？\n(A)甲")

    def test_rebuilds_fullwidth_inline_options(self):
        stem, opts, rebuilt = rebuild_options("何者正確？ (Ａ)甲 (Ｂ)乙 (Ｃ)丙 (Ｄ)丁", [])
        self.assertTrue(rebuilt)
        self.assertEqual(stem, "何者正確？")
        self.assertEqual(opts, ["(A)甲", "(B)乙", "(C)丙", "(D)丁"])

    def test_rebuilds_bad_first_marker_and_preserves_empty_picture_options(self):
        stem, opts, rebuilt = rebuild_options("何者正確？()甲", ["(A)", "(B)", "(C)"])
        self.assertTrue(rebuilt)
        self.assertEqual(stem, "何者正確？")
        self.assertEqual(opts, ["(A)甲", "(B)", "(C)", "(D)"])

    def test_rebuilds_unclosed_and_glued_markers(self):
        stem, opts, rebuilt = rebuild_options("題幹", ["(A)甲(B)(C丙(D)丁"])
        self.assertTrue(rebuilt)
        self.assertEqual(stem, "題幹")
        self.assertEqual(opts, ["(A)甲", "(B)", "(C)丙", "(D)丁"])

    def test_rebuilds_inline_options_with_parenthesized_content(self):
        stem, opts, rebuilt = rebuild_options("題幹(A)(1)→(甲)→b(B)(2)→(甲)→a(C)(1)→(乙)→b(D)(2)→(乙)→a", [])
        self.assertTrue(rebuilt)
        self.assertEqual(opts, ["(A)(1)→(甲)→b", "(B)(2)→(甲)→a", "(C)(1)→(乙)→b", "(D)(2)→(乙)→a"])

    def test_does_not_force_three_markers_into_four_options(self):
        stem, opts, rebuilt = rebuild_options("題幹", ["(A)甲", "(B)乙", "(C)丙"])
        self.assertFalse(rebuilt)
        self.assertEqual(len(opts), 3)

    def test_completes_only_ordered_single_choice_labels(self):
        self.assertEqual(complete_single_choice_options(["(B)乙", "(C)丙", "(D)丁"]),
                         ["(A)", "(B)乙", "(C)丙", "(D)丁"])
        self.assertEqual(complete_single_choice_options([], picture=True), ["(A)", "(B)", "(C)", "(D)"])
        self.assertEqual(complete_single_choice_options(["(A)甲", "(C)丙", "(B)乙"]),
                         ["(A)甲", "(C)丙", "(B)乙"])

    def test_figure_marker_stays_at_stem_end_when_a_is_missing(self):
        from to_qbank_xlsx import question_text
        text = question_text({"stem": "哪一項正確？", "opts": ["(B)乙", "(C)丙", "(D)丁"],
                              "kinds": ["圖片"], "pn": 1, "tf": False})
        self.assertEqual(text.splitlines()[0], "哪一項正確？〔含圖〕")
        self.assertEqual(text.splitlines()[1], "(A)")

    def test_question_numbers_follow_continuity_when_print_is_wrong(self):
        self.assertEqual(resolved_question_numbers([
            {"pn": 1}, {"pn": 2}, {"pn": 17}, {"pn": 4}, {"pn": None}, {"pn": 6},
        ]), [1, 2, 3, 4, 5, 6])

    def test_removes_page_marks_and_keeps_zero(self):
        self.assertEqual(fix_ocr("(D)可以產生消化液分解養分。國一生物第1頁/共4頁"), "(D)可以產生消化液分解養分。")
        self.assertEqual(fix_ocr("流程改成下表：夏，共4頁則溶液"), "流程改成下表：夏則溶液")
        self.assertEqual(fix_ocr("以維持體溫O下列何者"), "以維持體溫。下列何者")
        self.assertEqual(fix_ocr("心搏停止時，血壓為0"), "心搏停止時，血壓為0")
        self.assertEqual(fix_ocr("(D)血液是黃色透明液體畫卡作答第二頁"), "(D)血液是黃色透明液體")
        self.assertEqual(fix_ocr("何者錯誤？第二面完成，請換下一張試卷"), "何者錯誤？")
        self.assertEqual(fix_ocr("屬於非專一性防禦。單選題(每題2.5分，共75分)"), "屬於非專一性防禦。")
        self.assertEqual(fix_ocr("(D)D。班座號：姓名："), "(D)D。")

    def test_common_misreads_need_context(self):
        self.assertEqual(fix_ocr("血液注人病人體內"), "血液注入病人體內")
        self.assertEqual(fix_ocr("組織疏松"), "組織疏鬆")
        self.assertEqual(fix_ocr("(D)已的後段"), "(D)己的後段")
        self.assertEqual(fix_ocr("(D)已經完成"), "(D)已經完成")
        self.assertEqual(fix_ocr("(C)內(D)T。"), "(C)丙(D)丁。")

    def test_trailing_intro_moves_to_next_group(self):
        from to_qbank_xlsx import split_trailing_intro
        rs = [{"題目": "甲、乙兩試管會呈現什麼顏色？甲：乙：（各2分）真真將天竺葵其中一枚葉片用黑色貼紙貼住，進行澱粉的檢驗實驗，回答下列問題", "題組文章": ""},
              {"題目": "下列何者正確？\n(A)甲\n(B)乙\n(C)丙\n(D)丁", "題組文章": ""}]
        split_trailing_intro(rs)
        self.assertEqual(rs[0]["題目"], "甲、乙兩試管會呈現什麼顏色？甲：乙：（各2分）")
        self.assertTrue(rs[1]["題組文章"].startswith("真真將天竺葵"))

    def test_passage_drops_headings_and_figure_labels(self):
        from to_qbank_xlsx import tidy_passage, restore_truncated_group
        self.assertEqual(tidy_passage("題組一、小軒由科學雜誌上得知"), "小軒由科學雜誌上得知")
        self.assertEqual(tidy_passage("(二)宏志要探討酵素"), "宏志要探討酵素")
        self.assertEqual(tidy_passage("圖為消化道示意圖,回答10-12題丙丁甲乙"), "圖為消化道示意圖,回答10-12題")
        self.assertEqual(tidy_passage("請閱讀文章,回答下列問題:人工心肺機主要的功能"), "請閱讀文章,回答下列問題:人工心肺機主要的功能")
        self.assertEqual(tidy_passage("請閱讀文章，回答下列問題：=====人工心肺機"), "請閱讀文章，回答下列問題：人工心肺機")
        self.assertEqual(tidy_passage("附圖為葉的構造，回答下列問題：(29~32題)太陽"),
                         "附圖為葉的構造，回答下列問題：(29~32題)")
        group, stem = restore_truncated_group("題組一、附圖為肺泡模式圖，請依據此圖回", "答下列問題：(33)何者正確？")
        self.assertEqual(group, "題組一、附圖為肺泡模式圖，請依據此圖回答下列問題：")
        self.assertEqual(stem, "(33)何者正確？")

    def test_passage_drops_reading_prompts_sources_and_terminal_figure_labels(self):
        from to_qbank_xlsx import tidy_passage
        self.assertEqual(tidy_passage("⚫請閱讀以下敘述後完成第39~41題：新冠肺炎資料。"),
                         "新冠肺炎資料。")
        self.assertEqual(tidy_passage("題題組一、下圖1為葉片圖。圖1 圖2"),
                         "下圖1為葉片圖。")
        self.assertEqual(tidy_passage("請參考圖片內容。（圖片來源：健談https://example.com/）"),
                         "請參考圖片內容。")

    def test_does_not_move_an_ordinary_question_into_group(self):
        from to_qbank_xlsx import restore_truncated_group
        self.assertEqual(restore_truncated_group("附圖為消化系統", "下列何者正確？"),
                         ("附圖為消化系統", "下列何者正確？"))

    def test_one_passage_per_group(self):
        from to_qbank_xlsx import one_passage_per_group
        rs = [{"_gid": 1, "題組文章": "引言"}, {"_gid": 1, "題組文章": "引言"}, {"_gid": None, "題組文章": ""}]
        one_passage_per_group(rs)
        self.assertEqual([r["題組文章"] for r in rs], ["引言", "", ""])

    def test_explicit_range_uses_excel_question_numbers(self):
        from to_qbank_xlsx import apply_explicit_group_ranges
        rows = [{"題號": str(n), "_gid": None} for n in range(40, 45)]
        questions = [{"gid": 9, "group": "請根據附圖回答40～42題"}]
        apply_explicit_group_ranges(rows, questions, [40])
        self.assertEqual([r["_gid"] is not None for r in rows], [True, True, True, False, False])


if __name__ == "__main__":
    unittest.main()
