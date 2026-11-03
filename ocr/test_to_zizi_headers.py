"""Word 前的卷頭、頁尾過濾規則。"""
import unittest

from ocr.to_zizi_word import group_left, group_range, is_boilerplate_line, recover_truncated_qnum


class BoilerplateLineTest(unittest.TestCase):
    def test_header_variants_are_removed(self):
        for text in ("九年級：理化", "科目名稱：理化", "科目代碼：23", "命題教師：王老師",
                     "12班 座號：3 姓名：小明", "112學年度第二學期段考試卷"):
            self.assertTrue(is_boilerplate_line(text, in_header=True), text)

    def test_page_and_continuation_variants_are_removed(self):
        for text in ("第1頁，共4頁", "第 I 頁，共 O 頁", "請接第2頁繼續作答", "背面尚有試題"):
            self.assertTrue(is_boilerplate_line(text), text)

    def test_normal_question_is_kept(self):
        self.assertFalse(is_boilerplate_line("1. 下列何者正確？", in_header=True))
        self.assertFalse(is_boilerplate_line("班級制度的演變", in_header=False))

    def test_recover_only_unambiguous_truncated_question_numbers(self):
        self.assertEqual(recover_truncated_qnum("1).承上題，圖中何者正確？", 9), 10)
        self.assertEqual(recover_truncated_qnum("3.承上題，圖中何者正確？", 38), 39)
        self.assertIsNone(recover_truncated_qnum("3.下列何者正確？", 22))
        self.assertIsNone(recover_truncated_qnum("1).一般敘述", 9))

    def test_group_range_requires_an_explicit_question_span(self):
        self.assertEqual(group_range("請根據文章內容，回答41-43題："), (41, 43))
        self.assertEqual(group_range("回答第 7～9 題"), (7, 9))
        self.assertEqual(group_range("7－9題"), (7, 9))
        self.assertIsNone(group_range("請回答第 7 題"))
        self.assertIsNone(group_range("題組一、閱讀下文"))

    def test_group_span_overrides_conservative_fallback(self):
        self.assertEqual(group_left("請根據文章內容，回答41-43題：", 2), 3)
        self.assertEqual(group_left("題組一、請根據附圖回答下列問題", 2), 2)

    def test_figure_group_intro_requires_a_group_signal(self):
        from ocr.to_zizi_word import RE_INTRO
        self.assertTrue(RE_INTRO.search("（一）附圖為複式顯微鏡下的小魚尾鰭血管示意圖，試回答下列問題："))
        self.assertTrue(RE_INTRO.search("如圖為光合作用的示意圖，請根據圖中代號回答下列31－34問題："))
        self.assertFalse(RE_INTRO.search("附圖為人體三種血管的解剖圖。"))


class PlaceArticlesTest(unittest.TestCase):
    def test_articles_printed_elsewhere_go_to_matching_header_groups(self):
        from to_zizi_word import place_articles
        q = lambda n, gid, stem, group: {"n": n, "gid": gid, "stem": stem, "opts": [], "group": group}
        qs = [q(1, 1, "膽道閉鎖的孩子會出現什麼狀況？", "【文章一】請根據文章內容，回答1-2題："),
              q(2, 1, "嬰兒大便是什麼顏色要就醫？", "【文章一】請根據文章內容，回答1-2題："),
              q(3, 3, "牛的哪一個胃部與人類的胃最接近？", "【文章二】請根據文章內容，回答3題：")]
        orphans = [(3, "我國嬰兒比西方嬰兒好發膽道閉鎖，若未能在出生六十天內完成手術，延遲治療會使膽汁逆流，因此嬰兒大便顏色要注意。", None), (3, "【文章修改自：寶寶手冊】", None),
                   (3, "牛的胃部有四個，分別是瘤胃、蜂巢胃、重瓣胃與皺胃，其中皺胃會分泌胃液，和人類的胃最接近。", None)]
        place_articles(qs, orphans)
        self.assertTrue(qs[0]["group"].endswith("因此嬰兒大便顏色要注意。【文章修改自：寶寶手冊】"))
        self.assertTrue(qs[2]["group"].endswith("和人類的胃最接近。"))


if __name__ == "__main__":
    unittest.main()
