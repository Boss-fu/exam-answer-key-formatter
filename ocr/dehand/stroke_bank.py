#!/usr/bin/env python3
"""由既有配對遮罩萃取可重用的手寫筆畫庫。

除了單一連通筆畫，也保留相鄰的多筆畫「筆跡群」。真實作答常是一串
數字、括號或算式；只合成單筆畫會讓模型只會擦小記號，對整串作答泛化差。
"""
import argparse, glob, json, os
import cv2
import numpy as np


def kind(area, w, h):
    ratio = max(w, h) / max(1, min(w, h))
    if ratio >= 7 or max(w, h) >= 220:
        return "long"
    if area >= 900 or max(w, h) >= 90:
        return "work"
    return "short"


def save_rgba(gray, alpha, x, y, w, h, out, name, typ, rows, counts, extra=None):
    """將指定 ROI 的半透明筆跡存成可重貼的素材。"""
    pad = 2
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(gray.shape[1], x + w + pad), min(gray.shape[0], y + h + pad)
    a = alpha[y0:y1, x0:x1]
    # alpha 同時帶入原稿深度，淡鉛筆不會被硬變成黑色。
    a = (a.astype(np.float32) * (255 - gray[y0:y1, x0:x1]) / 255).astype(np.uint8)
    rgba = cv2.cvtColor(gray[y0:y1, x0:x1], cv2.COLOR_GRAY2BGRA)
    rgba[:, :, 3] = a
    cv2.imwrite(os.path.join(out, name), rgba)
    row = {"file": name, "kind": typ, "area": int((a > 0).sum()), "w": int(w), "h": int(h)}
    if extra:
        row.update(extra)
    rows.append(row); counts[typ] = counts.get(typ, 0) + 1


def save_crop_rgba(gray, alpha, x0, y0, out, name, typ, rows, counts, extra=None):
    """存已裁切好的 alpha，避免每個筆畫都配置一張整頁遮罩。"""
    a = (alpha.astype(np.float32) * (255 - gray[y0:y0 + alpha.shape[0], x0:x0 + alpha.shape[1]]) / 255).astype(np.uint8)
    rgba = cv2.cvtColor(gray[y0:y0 + a.shape[0], x0:x0 + a.shape[1]], cv2.COLOR_GRAY2BGRA)
    rgba[:, :, 3] = a
    cv2.imwrite(os.path.join(out, name), rgba)
    row = {"file": name, "kind": typ, "area": int((a > 0).sum()), "w": int(a.shape[1]), "h": int(a.shape[0])}
    if extra:
        row.update(extra)
    rows.append(row); counts[typ] = counts.get(typ, 0) + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="ocr/dehand/data")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0, help="最多處理幾頁（煙霧測試用；0=全部）")
    ap.add_argument("--clusters-per-page", type=int, default=18,
                    help="每頁最多保留幾個多筆畫群（0=不產生群）")
    a = ap.parse_args(); out = a.out or os.path.join(a.data, "_strokes")
    os.makedirs(out, exist_ok=True)
    rows, counts, n = [], {"short": 0, "work": 0, "long": 0, "cluster": 0}, 0
    for page_i, mp in enumerate(glob.glob(os.path.join(a.data, "*", "p*_m.png"))):
        if a.limit and page_i >= a.limit: break
        xp = mp[:-6] + "_x.png"; gray = cv2.imread(xp, 0); mask = cv2.imread(mp, 0)
        if gray is None or mask is None: continue
        binary = (mask > 127).astype(np.uint8)
        cc, lab, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
        for i in range(1, cc):
            x, y, w, h, area = stats[i]
            if not 30 <= area <= 20000 or w > gray.shape[1] * .4: continue
            typ = kind(int(area), int(w), int(h)); name = f"s{n:06d}.png"
            pad = 2; x0, y0 = max(0, x-pad), max(0, y-pad); x1, y1 = min(gray.shape[1], x+w+pad), min(gray.shape[0], y+h+pad)
            alpha = (lab[y0:y1, x0:x1] == i).astype(np.uint8) * 255
            save_crop_rgba(gray, alpha, x0, y0, out, name, typ, rows, counts)
            n += 1

        # 把 9px 以內的筆畫視為同一段手寫；只收含至少 3 個原始筆畫、
        # 尺寸合理的群，避免把整頁的雜訊或整欄題目塞進資料庫。
        if a.clusters_per_page:
            dil = cv2.dilate(binary, np.ones((19, 19), np.uint8))
            nc, clab, cstats, _ = cv2.connectedComponentsWithStats(dil, 8)
            candidates = []
            for i in range(1, nc):
                x, y, w, h, area = cstats[i]
                if not (80 <= w * h <= 120000 and w <= gray.shape[1] * .45 and h <= gray.shape[0] * .30):
                    continue
                within = clab[y:y+h, x:x+w] == i
                parts = np.unique(lab[y:y+h, x:x+w][within])
                parts = parts[parts > 0]
                if len(parts) < 3:
                    continue
                pad = 2; x0, y0 = max(0, x-pad), max(0, y-pad); x1, y1 = min(gray.shape[1], x+w+pad), min(gray.shape[0], y+h+pad)
                alpha = ((clab[y0:y1, x0:x1] == i) & (lab[y0:y1, x0:x1] > 0)).astype(np.uint8) * 255
                candidates.append((int((alpha > 0).sum()), x0, y0, alpha, len(parts)))
            # 取最大的群，優先保留算式與作答文字；同一頁有上限避免資料偏斜。
            for _, x0, y0, alpha, parts in sorted(candidates, reverse=True)[:a.clusters_per_page]:
                name = f"s{n:06d}.png"
                save_crop_rgba(gray, alpha, x0, y0, out, name, "cluster", rows, counts, {"parts": int(parts)})
                n += 1
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f: json.dump(rows, f, ensure_ascii=False, indent=1)
    print(json.dumps({"strokes": n, **counts}, ensure_ascii=False))


if __name__ == "__main__": main()
