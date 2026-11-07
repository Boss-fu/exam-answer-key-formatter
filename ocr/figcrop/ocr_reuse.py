#!/usr/bin/env python3
"""切圖沿用 OCR 流程的結果（crop.py --ocr-dir）：題號不再用被清稿傷到的文字猜，改用原稿 OCR＋切題結果。

輸入是 pyexam 對「原稿」跑出的資料夾：
  *_ocr_boxes.json   每頁轉正後的 OCR 行框＋文字、閱讀順序文字行（recs）、縮圖（對齊用）
  *_lines.json 等    to_qbank_xlsx.rows() 切題要用的檔案（切題結果＝實際存在的題號、題組）
框仍在清稿上偵測。原稿和清稿可能縮放、轉向不同：每頁用縮圖和清稿做 SIFT 對齊（homography），OCR 座標換到清稿。

配題號（PageOcr.qno）：題目起點來自原稿的印刷題號行＋切題結果（anchors），在閱讀順序上找圖前後兩題 prev／next，依序：
  1. 圖在題組文章區（題組引言之後、第一小題之前）→ 題組第一題。
  2. 圖有圖號（圖(五)、表二）→ 題幹引用這個圖號的題。
  3. prev 的 (D) 已在圖上方結束，中間有 ≥2 行敘述或「回答第 N～M 題」這類引言（或 next 是題組第一題）→ next。
  4. next 題幹有「如圖／右圖…」、prev 沒有，next 又在圖的上半起頭或 prev 已結束 → next。
  5. 否則 guess_qno（吃原稿 OCR 的行）落在 prev／next 之一就採用；都不是 → prev。
  和 guess_qno 不同的答案標低信心；同頁閱讀順序倒退也標低信心（不改號；在 crop.crop_items）。
框的精修預設仍用清稿自己的 OCR（框和沒給 --ocr-dir 時一樣）；FIGCROP_REUSE_LINES=1 才連精修都用原稿 OCR（省時間但框會變）。
"""
from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

RE_CUE = re.compile(r"如圖|下圖|右圖|上圖|左圖|附圖|圖[（(]?[一二三四五六七八九十\d]|下表|如表|附表|右表")
# 圖號／表號：「圖(五)」「附圖（二）」「表一」「圖 3」；題幹引用與圖說都用它
RE_LABEL = re.compile(r"[圖表]\s*[（(]?\s*([一二三四五六七八九十]{1,3}|\d{1,2})\s*[)）]?")
RE_OPT_LINE = re.compile(r"^[(（]\s*[A-DＡ-Ｄ]\s*[)）]")
RE_INTRO = re.compile(r"回答|作答|第\s*\d+\s*[~～至\-－—]\s*\d+\s*題|題組|^[(（][一二三四五六七八九十][)）]|^[一二三四五六七八九十]、")
RE_RANGE = re.compile(r"(\d{1,2})\s*(?:[~～至到\-－—]|\s+to\s+)\s*(\d{1,2})\s*(?:題|問題)")
RE_OPT_D = re.compile(r"[(（]\s*[DＤ]\s*[)）]")
RE_QHEAD = re.compile(r"^\s*(?:[（(][^)）]{0,3}[)）])?\s*(\d{1,2})\s*[.．、]")


def _norm(t):
    return re.sub(r"\s|⚠需確認", "", str(t))


def _body(t):
    """去掉行首的題號、括號、標點，只留中文開頭的題幹（比對切題結果用）。"""
    return re.sub(r"^[^一-鿿「]*", "", _norm(t))


def _rot_matrix(k, W, H):
    """np.rot90(img, k) 的點座標轉換（img 寬 W 高 H）。"""
    return {0: np.eye(3), 1: np.array([[0, 1, 0], [-1, 0, W], [0, 0, 1]], float),
            2: np.array([[-1, 0, W], [0, -1, H], [0, 0, 1]], float), 3: np.array([[0, -1, H], [1, 0, 0], [0, 0, 1]], float)}[k % 4]


def _apply(Hm, box):
    pts = np.array([[box[0], box[1], 1], [box[2], box[1], 1], [box[0], box[3], 1], [box[2], box[3], 1]], float).T
    q = Hm @ pts; q = q[:2] / q[2]
    return (int(q[0].min()), int(q[1].min()), int(q[0].max()), int(q[1].max()))


class OcrReuse:
    def __init__(self, path):
        path = Path(path)
        found = sorted(path.glob("*_ocr_boxes.json"))
        if not found:
            raise FileNotFoundError(f"{path} 裡沒有 *_ocr_boxes.json（要用新版 pyexam 跑原稿）")
        self.dir, self.base = path, found[0].name[: -len("_ocr_boxes.json")]
        self.pages = {p["page"]: p for p in json.loads(found[0].read_text(encoding="utf-8"))["pages"]}
        self._feat, self._anchors = {}, None
        self.stats = {"aligned": 0, "fallback": 0}

    # ---------- 對齊 ----------
    def _thumb_feat(self, n):
        if n not in self._feat:
            p = self.pages[n]
            g = cv2.imdecode(np.frombuffer(base64.b64decode(p["thumb"]), np.uint8), cv2.IMREAD_GRAYSCALE) if p.get("thumb") else None
            self._feat[n] = (cv2.SIFT_create(4000).detectAndCompute(g, None), p["thumb_scale"]) if g is not None else (None, None)
        return self._feat[n]

    def _align(self, n, gray):
        """原稿第 n 頁（OCR 座標）→ 清稿頁（gray 的像素座標）的 homography 與 inlier 數。"""
        (feat, ts) = self._thumb_feat(n)
        if not feat or feat[1] is None:
            return None, 0
        cs = min(1.0, 1600.0 / max(gray.shape[:2]))
        small = cv2.resize(gray, None, fx=cs, fy=cs, interpolation=cv2.INTER_AREA) if cs < 1 else gray
        kb, db = cv2.SIFT_create(4000).detectAndCompute(small, None)
        if db is None or len(kb) < 20:
            return None, 0
        ka, da = feat
        good = [m for m, nn in (x for x in cv2.BFMatcher().knnMatch(da, db, k=2) if len(x) == 2) if m.distance < .7 * nn.distance]
        if len(good) < 25:
            return None, len(good)
        pa = np.float32([ka[m.queryIdx].pt for m in good]); pb = np.float32([kb[m.trainIdx].pt for m in good])
        Hs, mask = cv2.findHomography(pa, pb, cv2.RANSAC, 4.0)
        if Hs is None:
            return None, 0
        Hm = np.diag([1 / cs, 1 / cs, 1]) @ Hs @ np.diag([ts, ts, 1])
        return Hm, int(mask.sum())

    def page(self, pn, img):
        """清稿第 pn 頁的 OCR 沿用結果；對不上（空白頁、原稿沒有這頁、文字層頁）回 None，呼叫端照舊自己跑 OCR。"""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        order = ([pn] if pn in self.pages else []) + [n for n in sorted(self.pages) if n != pn]
        best = (None, 0, None)
        for n in order:
            Hm, inl = self._align(n, gray)
            if Hm is not None and inl > best[1]:
                best = (Hm, inl, n)
            if n == pn and inl >= 80:
                break
        Hm, inl, n = best
        if Hm is None or inl < 40:
            self.stats["fallback"] += 1
            return None
        H, W = gray.shape[:2]
        k = min(range(4), key=lambda k: _angle(_rot_matrix(k, W, H) @ Hm))
        Hu = _rot_matrix(k, W, H) @ Hm
        self.stats["aligned"] += 1
        return PageOcr(self, n, k, Hu, (W, H) if k % 2 == 0 else (H, W))

    # ---------- 切題結果 ----------
    def anchors(self):
        """每題起點 [(序, 題號, 是否題組第一題, 原稿頁, rec, 起點起三行文字)]、題組文章起點 {題組第一題號: (頁, rec)}、題號集合。
        起點以原稿 OCR 的印刷題號行為主（認錯的用前後連續性修、孤立的跳號丟掉；允許換段從頭編號，例如卷子先印 25～50 題）；
        切題結果（to_qbank_xlsx.rows）用題幹模糊比對找位置：對到題號行就補上題組資訊，對到沒題號的行（題號被蓋掉）就依前後題號補號。
        不拿切題的流水號當題號：它強制遞增，卷子頁序特殊時會整串錯號。"""
        if self._anchors is not None:
            return self._anchors
        try:
            from rapidfuzz import fuzz
            ratio = fuzz.partial_ratio               # 行首常黏著圖上的字或「題組二、」，用子字串對齊
        except ImportError:
            import difflib
            ratio = lambda a, b: 100 * difflib.SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b)).size / max(1, len(a))
        flat = [(n, r) for n in sorted(self.pages) for r in self.pages[n]["recs"]]
        body = [_body(r["text"]) for _n, r in flat]
        # 1) 印刷題號行
        pr = []
        for i, (_n, r) in enumerate(flat):
            t = _norm(r["text"]); m = RE_QHEAD.match(t)
            if m and len(re.findall(r"[一-鿿]", t[m.end():])) >= 2:
                pr.append([i, int(m.group(1))])
        for k in range(1, len(pr) - 1):              # 認錯：前後差 2、自己不是中間那號
            a, x, c = pr[k - 1][1], pr[k][1], pr[k + 1][1]
            if c - a == 2 and x != a + 1:
                pr[k][1] = a + 1
        num = {}
        for k, (i, n) in enumerate(pr):              # 孤立跳號：前後是遞增的、自己不在中間
            p = pr[k - 1][1] if k else None; nx = pr[k + 1][1] if k + 1 < len(pr) else None
            if p is not None and nx is not None and p < nx and not p < n < nx:
                continue
            if p is not None and n == p and num.get(pr[k - 1][0]) == n:
                continue                             # 同一題號重複（題號行被拆成兩行）
            num[i] = n
        # 2) 切題結果：每題題幹對到哪一行
        import to_qbank_xlsx as X
        try:
            rows = X.rows(str(self.dir), self.base)
        except Exception as e:
            print(f"[ocr_reuse] 切題失敗：{e}", file=sys.stderr)
            rows = []
        matched = []                                 # (行, 切題題號, 是否題組第一題, 題組文章開頭)
        for r in rows:
            key = _body(r["題目"])[:12]
            if len(key) < 4:
                continue
            sc, i = max(((ratio(key, body[i][:len(key) + 6]), i) for i in range(len(flat)) if len(body[i]) >= 3), default=(0, None))
            if sc < 85:
                continue
            try:
                rq = int(r["題號"])
            except ValueError:
                rq = None
            gid = r.get("題組ID") or ""
            fst = bool(gid) and gid.split("-")[-1] == r["題號"]
            matched.append((i, rq, fst, _body(r["題組文章"])[:12] if fst and r.get("題組文章") else "", gid))
        rowq = {i: rq for i, rq, _f, _p, _g in matched if rq is not None}
        printed = dict(num)                          # 換段位移前的印刷題號（對「回答第 4～6 題」用）
        # 3) 換段：題號變小就是新的一段。新段和前面撞號時，看切題題號（它把換段重編換算成連續號）在這段的多數差值：
        #    差值 > 0 → 整段加上（第二部分又從 1 起）；差值 0 → 兩段之中對到切題較少的那段不是題目（卷首注意事項的 1. 2. 3.）。
        runs, cur = [], []
        for i in sorted(num):
            if cur and num[i] < num[cur[-1]]:
                runs.append(cur); cur = []
            cur.append(i)
        if cur:
            runs.append(cur)
        kept = []
        from collections import Counter
        for run in runs:
            used = {num[i]: i for r_ in kept for i in r_}
            if not any(num[i] in used for i in run):
                kept.append(run); continue
            votes = Counter(rowq[i] - num[i] for i in run if i in rowq)
            off, cnt = votes.most_common(1)[0] if votes else (0, 0)
            if off > 0 and cnt >= max(2, len([i for i in run if i in rowq]) // 2):
                for i in run:
                    num[i] += off
                kept.append(run); continue
            hit = lambda r_: sum(1 for i in r_ if i in rowq) / max(1, len(r_))
            clash = [r_ for r_ in kept if any(num[i] in {num[j] for j in run} for i in r_)]
            if all(hit(run) > hit(r_) for r_ in clash):
                kept = [r_ for r_ in kept if r_ not in clash] + [run]
        num = {i: num[i] for r_ in kept for i in r_}
        # 4) 題號被蓋掉、只有切題對到的行：依前後題號補號
        first, passage_key, gid_of = set(), {}, {}
        for i, rq, fst, pk, gid in matched:
            if i not in num:
                before = [j for j in num if j < i]; after = [j for j in num if j > i]
                pn_ = num[max(before)] if before else None; nn_ = num[min(after)] if after else None
                if pn_ is not None and nn_ is not None and nn_ - pn_ == 2:
                    num[i] = pn_ + 1
                elif rq is not None and (pn_ is None or pn_ < rq) and (nn_ is None or rq < nn_):
                    num[i] = rq
                else:
                    continue
            if gid:
                gid_of[i] = gid
            if fst:
                first.add(i)
                if pk:
                    passage_key[i] = pk
        # 5) 題組範圍 {第一題: 最後一題}：切題的題組 ID ＋ 卷面印的「回答第 N～M 題」（範圍後面接著的第一題就是 N，換段重編照位移）
        groups, intro_at = {}, {}
        by_gid = {}
        for i, g in gid_of.items():
            by_gid.setdefault(g, []).append(num[i])
        for i in first:
            ns = sorted(by_gid.get(gid_of.get(i), [num[i]]))
            groups[num[i]] = max(ns[-1], num[i])
        for i, (_n, r) in enumerate(flat):
            for m in RE_RANGE.finditer(_norm(r["text"]).replace("［", "(").replace("］", ")")):
                a, b = int(m.group(1)), int(m.group(2))
                if not 1 <= b - a <= 9 or RE_QHEAD.match(_norm(r["text"])):
                    continue
                nxt = [j for j in sorted(num) if j > i][:1]
                if not nxt or nxt[0] - i > 40:
                    continue
                j = nxt[0]
                if printed.get(j, num[j]) == a or num[j] == a:
                    groups[num[j]] = num[j] + (b - a); first.add(j); intro_at.setdefault(num[j], i)
        out, passages = [], {}
        starts = sorted(num)
        for k, i in enumerate(starts):
            nxt_i = starts[k + 1] if k + 1 < len(starts) else len(flat)            # 題幹文字只到下一題起點
            txt = lambda n_: _norm(" ".join(x[1]["text"] for x in flat[i:min(i + n_, nxt_i)]))
            out.append((len(out), num[i], i in first, flat[i][0], flat[i][1], txt(3), txt(6)))
            pk = passage_key.get(i, "")
            if len(pk) >= 4:
                lo = max([j for j in num if j < i], default=-1) + 1
                cand = [(ratio(pk, body[j][:len(pk) + 8]), j) for j in range(max(lo, i - 40), i) if len(body[j]) >= 3]
                sc, j = max(cand, default=(0, None))
                if sc >= 85:
                    passages[num[i]] = (flat[j][0], flat[j][1])
            if num[i] not in passages and num[i] in intro_at:
                j = intro_at[num[i]]; passages[num[i]] = (flat[j][0], flat[j][1])
        self._anchors = (out, passages, set(num.values()), groups)   # 第三項：實際存在的題號；第四項：題組範圍
        return self._anchors

def _angle(Hm):
    v = Hm @ np.array([[0, 100], [0, 0], [1, 1]], float); v = v[:2] / v[2]
    d = v[:, 1] - v[:, 0]
    return abs(np.arctan2(d[1], d[0]))


class PageOcr:
    """清稿某頁（已轉正：np.rot90(img, k)）上的原稿 OCR。raw＝[(框, 文字)]，座標在轉正後的清稿上。"""

    def __init__(self, reuse, n, k, Hu, size):
        self.reuse, self.n, self.k, self.Hu = reuse, n, k, Hu
        self.W, self.H = size
        p = reuse.pages[n]
        self.raw = [(_apply(Hu, l["box"]), l["text"]) for l in p["lines"]]

    def _pos(self, page, rec):
        """原稿某頁的 rec 在本頁清稿座標的位置（不同頁回 None）。"""
        return _apply(self.Hu, (rec["x0"], rec["y0"], rec["x1"], rec["y1"])) if page == self.n else None

    def _starts(self):
        """本頁的欄起點：題目起點（題號行）的 x 分群（同 crop._columns 的想法）。
        不用 pyexam 的欄號：單欄卷右邊有圖時 pyexam 會在圖左邊切一刀，圖旁的字變成「第 2 欄」。"""
        if getattr(self, "_st", None) is None:
            anchors = self.reuse.anchors()[0]
            xs = sorted(self._pos(a[3], a[4])[0] for a in anchors if a[3] == self.n)
            cols = []
            for x in xs:
                if cols and x - cols[-1][-1] < .18 * self.W:      # 欄距至少約四分之一頁寬（A3 四欄也是）；題組小題縮排不算新欄
                    cols[-1].append(x)
                else:
                    cols.append([x])
            st = [c[0] for c in cols if len(c) >= 2]
            self._st = st if st else [0]
        return self._st

    def _col_x(self, x):
        return max((i for i, c in enumerate(self._starts()) if c <= x + .1 * self.W), default=0)   # 題組引言從欄的左緣起頭、比題號靠左

    def _col_of(self, box):
        return self._col_x((box[0] + box[2]) / 2 - .05 * self.W)

    def qno(self, box):
        """回傳 (題號, 信心 'high'|'low')。"""
        r = self.assign(box)
        return r["qno"], r["conf"]

    def assign(self, box, extra=None):
        """回傳 {qno, conf, rule, group}：group＝(題組第一題, 最後一題)（圖是題組共用圖時），否則 None。
        extra：本頁清稿自己的 OCR 行 [(框, 文字)]（有就拿來找圖號：文字層原卷的圖號常畫在圖裡，文字層沒有）。"""
        self._extra = extra or []
        q, conf, rule, key, akey = self._decide(box)
        anchors, _p, _q, groups = self.reuse.anchors()
        group = None
        if q in groups and key is not None:
            a = next((a for a in anchors if a[1] == q), None)
            # 題組文章區的圖、或圖在題組第一題起點之前（閱讀順序）：整組共用
            if rule in ("passage", "passage_text") or (a is not None and key < akey(a)):
                group = (q, groups[q])
        elif key is not None and (rule == "passage_text" or (rule not in ("cue_next", "label") and self._in_passage(box, q, anchors, akey, key))):
            group = (q, None)                        # 範圍沒認出來，但圖在文章區：題組共用圖，最後一題未知
        return {"qno": q, "conf": conf, "rule": rule, "group": group}

    def _decide(self, box):
        import crop
        g = crop.guess_qno(box, self.raw, self.W)
        anchors, passages, qset, _groups = self.reuse.anchors()
        if not anchors:
            return g, "low", "no_anchor", None, None
        col = self._col_of(box); key = (self.n, col, box[1] + max(12, .012 * self.W))
        def akey(a):
            if a[3] != self.n:
                return (a[3], -1, 0)
            x0, y0, _x1, _y1 = self._pos(a[3], a[4])
            return (a[3], self._col_x(x0), y0)
        prev = [a for a in anchors if akey(a) <= key]; nxt = [a for a in anchors if akey(a) > key]
        prev = prev[-1] if prev else None; nxt = nxt[0] if nxt else None
        # 題組文章區：題組引言在圖上方（或前一欄），第一小題在圖下方 → 題組第一題
        if nxt and nxt[2] and nxt[1] in passages:
            pp, prec = passages[nxt[1]]
            ps = (pp, self._col_x(self._pos(pp, prec)[0]), self._pos(pp, prec)[1]) if pp == self.n else (pp, -1, 0)
            if ps <= key and (prev is None or akey(prev) < ps):
                return nxt[1], "high", "passage", key, akey
        if prev is None or nxt is None:
            pick = (prev or nxt)[1]
            return pick, "high" if pick == g else "low", "edge", key, akey
        # 圖有圖號：配給題幹引用這個圖號的題（前後各看兩題）
        lab = self._label(box)
        cites = lambda a: {m.group(1) for m in RE_LABEL.finditer(a[5])}
        if lab:
            i0 = prev[0]
            near = [a for a in anchors[max(0, i0 - 1):i0 + 3] if lab in cites(a)]
            if near:
                pick = min(near, key=lambda a: abs(a[0] - i0 - .5))[1]
                # 引用它的是題組裡後面的小題、圖在題組文章區（下一題起點之前）：照使用者習慣配題組第一題
                if pick > nxt[1] and self._in_passage(box, nxt[1], anchors, akey, key):
                    return nxt[1], "low", "passage_text", key, akey
                return pick, "high" if pick == g else "low", "label", key, akey
        same_col = akey(nxt)[:2] == (self.n, col)
        p_y = akey(prev)[2] if akey(prev)[:2] == (self.n, col) else -1
        # prev 的選項 (D) 在圖上方就結束了
        ended = any(RE_OPT_D.search(str(t)) and p_y <= b[1] < box[1] for b, t in self.raw if self._col_of(b) == col)
        # 圖在題組文章區（文章起點沒對到時的備援）：圖在下一題上方、上一題的 (D) 已在圖上方結束，
        # 而且 (D) 到圖之間有 ≥2 行敘述（或「回答第 N～M 題」「題組」這類引言）；或下一題本來就是題組第一題。
        if ended and (not same_col or box[1] < akey(nxt)[2]):
            d_last = max(b[3] for b, t in self.raw if RE_OPT_D.search(str(t)) and p_y <= b[1] < box[1] and self._col_of(b) == col)
            after = [str(t) for b, t in self.raw if d_last <= b[1] < box[1] and self._col_of(b) == col
                     and not RE_OPT_LINE.match(_norm(t)) and len(re.findall(r"[一-鿿]", str(t))) >= 6]
            if (nxt[2] and same_col) or len(after) >= 2 or any(RE_INTRO.search(t) for t in after):
                return nxt[1], "high" if nxt[1] == g else "low", "passage_text", key, akey
        # 欄頂的題組圖：上一題在別欄／別頁（找不到它的 (D)），圖到下一題之間是題組文章
        if same_col and not ended and p_y < 0 and self._in_passage(box, nxt[1], anchors, akey, key):
            return nxt[1], "high" if nxt[1] == g else "low", "passage_text", key, akey
        # 下一題題幹引用的是別的、有編號的圖（這張圖沒有那個圖號）：不是它的圖
        other = bool(cites(nxt)) and lab not in cites(nxt)
        if same_col and RE_CUE.search(nxt[5]) and not RE_CUE.search(prev[5]) and not other:
            beside = akey(nxt)[2] < (box[1] + box[3]) / 2               # 下一題在圖的上半起頭（圖印在題目旁）
            if ended or beside:
                return nxt[1], "high" if nxt[1] == g else "low", "cue_next", key, akey
        # 上一題題幹說「如圖」、下一題沒有：圖印在上一題的選項後面
        if RE_CUE.search(prev[6]) and not RE_CUE.search(nxt[5]) and not lab and not cites(prev):
            return prev[1], "high" if prev[1] == g else "low", "cue_prev", key, akey
        cands = {prev[1], nxt[1]}
        if g in cands:
            return g, "high", "guess", key, akey
        return prev[1], "low", "prev", key, akey

    def _in_passage(self, box, q, anchors, akey, key):
        """圖在第 q 題起點之前，而且圖所在欄、上一題的 (D) 之後到 q 起點之間（圖本身以外）有 ≥2 行敘述或題組引言。"""
        a = next((a for a in anchors if a[1] == q), None)
        if a is None or not key < akey(a):
            return False
        col = self._col_of(box)
        same = akey(a)[:2] == (self.n, col)
        q_y = akey(a)[2] if same else float("inf")
        ds = [b[3] for b, t in self.raw if RE_OPT_D.search(str(t)) and b[1] < box[1] and self._col_of(b) == col]
        prev = [x for x in anchors if akey(x) < akey(a) and akey(x)[:2] == (self.n, col)]
        lo = max(ds + [akey(prev[-1])[2] + 1] if prev else ds) if (ds or prev) else -1   # 上一題起點之後、它的 (D) 之後
        txt = [str(t) for b, t in self.raw if self._col_of(b) == col and lo <= b[1] < q_y
               and not (b[0] >= box[0] - 10 and b[2] <= box[2] + 10 and b[1] >= box[1] - 10 and b[3] <= box[3] + 10)
               and not RE_OPT_LINE.match(_norm(t)) and not RE_QHEAD.match(_norm(t)) and len(re.findall(r"[一-鿿]", str(t))) >= 6]
        return len(txt) >= 2 or any(RE_INTRO.search(t) for t in txt)

    def _label(self, box):
        """圖框裡或緊貼上下緣的短行若是圖號（圖(五)、表二）就回傳號碼字串。"""
        x0, y0, x1, y1 = box; m = 40
        for b, t in list(self.raw) + list(getattr(self, "_extra", [])):
            t = _norm(t)
            if len(t) > 10 or not (b[0] >= x0 - m and b[2] <= x1 + m and b[1] >= y0 - m and b[3] <= y1 + m):
                continue
            mm = RE_LABEL.search(t)
            if mm:
                return mm.group(1)
        return None

