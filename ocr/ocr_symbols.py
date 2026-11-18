"""OCR／文字層常輸出的「特殊字元」換成一般字（純標準庫，可單獨 import）。

PP-OCRv5 的字典裡有一批長得像一般字的特殊字元，辨識時會冒出來，例如：
  ㈲㈬ （括號中文字，Unicode 3220～3243）→ 有、水
  ㆗㆙㆚㆛（漢文訓讀標記，3190～319F）→ 中、甲、乙、丙
  ㊀㊁（圈圈中文字，3280～32B0）、⼈⼦（康熙部首，2F00～2FD5）、相容漢字（F900～FAFF）
這些對老師看起來就是亂碼，也搜尋、比對不到，一律換成一般字。

規則：
  * 用 NFKC 逐字展開；括號中文字（㈲＝(有)）拿掉括號 → 有。
  * 例外：㈠～㈩ 本來就是「(一)～(十)」的題組編號寫法，保留括號。
  * 只動上述區段，其他字元（含全形標點、全形英數）完全不動。
"""
import re
import unicodedata

_RE = re.compile("[㆐-㆟㈠-㉃㊀-㊰⼀-⿕豈-﫿]")


def _one(ch):
    n = unicodedata.normalize("NFKC", ch)
    if n == ch:
        return ch
    if len(n) == 3 and n[0] == "(" and n[2] == ")" and not 0x3220 <= ord(ch) <= 0x3229:
        return n[1]
    return n


# 已知的「長得像」誤認：人工校對真實考卷時抓到的，規則寫得很窄（要有前後文才換），避免誤傷正常文字。
_CONFUSIONS = [
    (re.compile("板瑰"), "板塊"),                                   # 塊 ↔ 瑰（板塊、地塊）
    (re.compile(r"(丙\s*[>＞、，,]\s*)于"), r"\1丁"),                # 甲、乙、丙、于 → 丁（甲乙丙丁序列）
    (re.compile(r"(地殼|地函|地核)一(?=[玄橄花鐵])"), r"\1—"),         # 地殼一玄武岩質 → 地殼—玄武岩質（破折號被讀成「一」）
]


def fix_known_confusions(text):
    """套用上面的窄規則；沒有命中時原樣回傳。"""
    if not text:
        return text
    for rx, rep in _CONFUSIONS:
        text = rx.sub(rep, text)
    return text


def fix_symbols(text):
    """把特殊字元換成一般字，並修正已知的字形誤認；都沒有時原樣回傳（很快）。"""
    if not text:
        return text
    if _RE.search(text):
        text = _RE.sub(lambda m: _one(m.group(0)), text)
    return fix_known_confusions(text)
