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


def fix_symbols(text):
    """把特殊字元換成一般字；沒有特殊字元時原樣回傳（很快）。"""
    if not text or not _RE.search(text):
        return text
    return _RE.sub(lambda m: _one(m.group(0)), text)
