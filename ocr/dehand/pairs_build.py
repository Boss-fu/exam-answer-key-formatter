#!/usr/bin/env python3
"""去筆跡訓練資料：「有筆跡的原稿掃描」對齊「同一份考卷的乾淨版」，相減得到筆跡遮罩（不用人工標註）。

找配對：資料夾裡檔名含 乾淨／clean／blank／重製／空白 的 PDF＝乾淨版；同資料夾其他 PDF（排除教師用、學生用、答案卷，以及含解答／答案／詳解的印刷答案）＝原稿候選。
對齊：每頁 150dpi 灰階 → ORB 特徵點 → RANSAC 透視變換，把乾淨版貼到原稿座標。
驗證：乾淨版的墨跡有 ≥ MIN_AGREE 落在原稿的墨跡上才採用（版面不同的重製版、配錯檔會被擋掉）。
遮罩：原稿有墨 且 乾淨版附近（3px 內）沒有墨 → 筆跡；去掉零碎雜點。

用法：
    python3 ocr/dehand/pairs_build.py <資料夾…> --out ocr/dehand/data
輸出：<out>/<配對名>/p<頁>_x.png（原稿灰階）、p<頁>_m.png（筆跡遮罩 0/255）、<out>/manifest.json
全部在本機處理；data 資料夾含學生考卷，已 gitignore。
"""
import argparse
import hashlib
import json
import os
import re
import sys

import cv2
import numpy as np
import pymupdf

DPI = 150
MIN_AGREE = 0.80
MAX_HAND = 0.15        # 一頁超過 15% 都是「筆跡」＝多半是陰影、配錯，不收
CLEAN = re.compile(r"乾淨|clean|blank|重製|空白", re.I)
SKIP = re.compile(r"教師用|老師用|學生用|答案卷|解答|答案|詳解|題庫|乾淨|clean|blank|重製|空白", re.I)


def pages(path):
    out = []
    for p in pymupdf.open(path):
        pix = p.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
        out.append(np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w).copy())
    return out


def flatten(gray):
    """去掉手機照片的陰影、光線不均：除以背景亮度（大範圍形態學閉運算估出來的紙色）"""
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31)))
    bg = cv2.GaussianBlur(bg, (0, 0), 8)
    return np.clip(gray.astype(np.float32) / np.maximum(bg, 1) * 255, 0, 255).astype(np.uint8)


def ink(gray):
    """墨跡：先拉平光線再 Otsu（乾淨版常被調深、原稿偏灰、照片有陰影，各自算門檻）；門檻最多到 200，紙色不會被當墨"""
    f = flatten(gray)
    t, _ = cv2.threshold(f, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return f < min(t, 200)


def align(orig, clean):
    """把乾淨版貼到原稿座標；回傳 (貼好的乾淨版, 一致度)。對不上回傳 (None, 0)"""
    orb = cv2.ORB_create(6000)
    k1, d1 = orb.detectAndCompute(orig, None)
    k2, d2 = orb.detectAndCompute(clean, None)
    if d1 is None or d2 is None or len(k1) < 50 or len(k2) < 50:
        return None, 0.0
    m = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(d2, d1)
    m = sorted(m, key=lambda x: x.distance)[:1500]
    if len(m) < 40:
        return None, 0.0
    src = np.float32([k2[x.queryIdx].pt for x in m]).reshape(-1, 1, 2)
    dst = np.float32([k1[x.trainIdx].pt for x in m]).reshape(-1, 1, 2)
    Hm, inl = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
    if Hm is None or inl.sum() < 30:
        return None, 0.0
    h, w = orig.shape
    warped = cv2.warpPerspective(clean, Hm, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
    ci, oi = ink(warped), ink(orig)
    if ci.sum() < 500:
        return None, 0.0
    oi_d = cv2.dilate(oi.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    return warped, float((ci & oi_d).sum() / ci.sum())


def hand_mask(orig, warped_clean):
    oi = ink(orig)
    ci = cv2.dilate(ink(warped_clean).astype(np.uint8), np.ones((7, 7), np.uint8)) > 0    # 留 3px 容差，印刷字邊緣不算筆跡
    m = (oi & ~ci).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = st[1:, cv2.CC_STAT_AREA] >= 12                                            # 零碎雜點（掃描噪點、對齊誤差）不算
    return (keep[lab] * 255).astype(np.uint8)


SCHOOL = re.compile(r"([\u4e00-\u9fff]{2,8}?(?:國中|高中|女中|附中|中學|高工|高商|實中|國小))")
TAGS = re.compile(r"【[^】]*】|[(（][^)）]*(?:原稿|乾淨|空白|重製|答案|解答|clean|blank)[^)）]*[)）]|_?(?:clean|blank)", re.I)


def key(fname):
    """同一份考卷的比對鍵：去掉【原稿】【乾淨版】(空白+答案) 這類標籤後的檔名"""
    return re.sub(r"\s+", "", TAGS.sub("", os.path.splitext(fname)[0]))


def school(fname):
    m = SCHOOL.search(fname)
    return m.group(1) if m else None


def find_pairs(roots):
    """一個資料夾常放好幾校：先用「去標籤檔名」一樣、再用「同校名」配；資料夾裡原稿 ≤ 2 份時才全部試"""
    pairs = []
    for root in roots:
        for d, _, files in os.walk(root):
            pdfs = [f for f in files if f.lower().endswith(".pdf")]
            cleans = [f for f in pdfs if CLEAN.search(f)]
            origs = [f for f in pdfs if not SKIP.search(f)]
            for c in cleans:
                cand = [o for o in origs if key(o) == key(c)]
                if not cand and school(c):
                    cand = [o for o in origs if school(o) == school(c)]
                if not cand and len(origs) <= 2:
                    cand = origs
                if cand:
                    pairs.append((d, c, cand))
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--out", default="ocr/dehand/data")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    mpath = os.path.join(a.out, "manifest.json")
    manifest = json.load(open(mpath, encoding="utf-8")) if os.path.exists(mpath) else []
    done_path = os.path.join(a.out, "done.txt")                # 已處理過的乾淨版（含失敗的），可中斷續跑
    done = set(open(done_path, encoding="utf-8").read().splitlines()) if os.path.exists(done_path) else set()
    todo = [p for p in find_pairs(a.roots) if os.path.join(p[0], p[1]) not in done]
    print(f"待處理 {len(todo)} 組（已完成 {len(done)}）", flush=True)
    have = {m["pair"] for m in manifest}

    def mark(d, c):
        with open(done_path, "a", encoding="utf-8") as f:
            f.write(os.path.join(d, c) + "\n")

    for d, c, origs in todo:
        origs = [o for o in origs if re.sub(r"[\\/:*?\"<>|\s]+", "_", os.path.splitext(o)[0])[:60] not in have]
        try:
            cp = pages(os.path.join(d, c))
        except Exception as e:                             # 壞檔、雲端沒下載到：記下來跳過
            print(f"✘ 讀不到 {c}：{e}", flush=True)
            mark(d, c)
            continue
        best, failed = None, False
        for o in origs:                                    # 同資料夾可能有好幾份原稿候選：取一致度最高的
            try:
                op = pages(os.path.join(d, o))
            except Exception as e:
                print(f"  ⚠ 讀不到原稿 {o}：{e}", flush=True)
                failed = True
                continue
            res = []
            for i, og in enumerate(op):
                cands = [cp[i]] if len(cp) == len(op) else cp
                tries = [(align(og, cg), j) for j, cg in enumerate(cands)]
                (w, s), j = max(tries, key=lambda t: t[0][1])
                res.append((i, w, s))
            score = float(np.mean([s for _, _, s in res])) if res else 0
            if best is None or score > best[0]:
                best = (score, o, op, res)
        if not best:
            if not failed:                                 # 讀檔失敗的不標完成，下次續跑會再試
                mark(d, c)
            continue
        score, o, op, res = best
        name = re.sub(r"[\\/:*?\"<>|\s]+", "_", os.path.splitext(o)[0])[:60]
        group = hashlib.md5(key(o).encode()).hexdigest()[:8]   # 同一場考試＝同一組（切訓練／驗證集用，不能跨組）
        kept = 0
        for i, w, s in res:
            if w is None or s < MIN_AGREE:
                continue
            m = hand_mask(op[i], w)
            if (m > 0).mean() > MAX_HAND:
                continue
            od = os.path.join(a.out, name)
            os.makedirs(od, exist_ok=True)
            cv2.imwrite(os.path.join(od, f"p{i + 1}_x.png"), op[i])
            cv2.imwrite(os.path.join(od, f"p{i + 1}_m.png"), m)
            manifest.append({"pair": name, "page": i + 1, "group": group, "agree": round(s, 3),
                             "hand_ratio": round(float((m > 0).mean()), 4), "orig": o, "clean": c})
            kept += 1
        if kept:
            have.add(name)
        json.dump(manifest, open(mpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        mark(d, c)
        print(f"{'✔' if kept else '✘'} {name}：{kept}/{len(res)} 頁可用（平均一致度 {score:.2f}；乾淨版 {c}）", flush=True)
    print(f"\n共 {len(manifest)} 頁訓練資料，{len({m['group'] for m in manifest})} 場考試")


if __name__ == "__main__":
    sys.exit(main())
