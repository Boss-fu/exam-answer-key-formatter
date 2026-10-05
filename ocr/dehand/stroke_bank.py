#!/usr/bin/env python3
"""由既有配對遮罩萃取可重用的手寫筆畫庫。"""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="ocr/dehand/data")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0, help="最多處理幾頁（煙霧測試用；0=全部）")
    a = ap.parse_args(); out = a.out or os.path.join(a.data, "_strokes")
    os.makedirs(out, exist_ok=True)
    rows, counts, n = [], {"short": 0, "work": 0, "long": 0}, 0
    for page_i, mp in enumerate(glob.glob(os.path.join(a.data, "*", "p*_m.png"))):
        if a.limit and page_i >= a.limit: break
        xp = mp[:-6] + "_x.png"; gray = cv2.imread(xp, 0); mask = cv2.imread(mp, 0)
        if gray is None or mask is None: continue
        cc, lab, stats, _ = cv2.connectedComponentsWithStats((mask > 127).astype(np.uint8), 8)
        for i in range(1, cc):
            x, y, w, h, area = stats[i]
            if not 30 <= area <= 20000 or w > gray.shape[1] * .4: continue
            pad = 2; x0, y0 = max(0, x-pad), max(0, y-pad); x1, y1 = min(gray.shape[1], x+w+pad), min(gray.shape[0], y+h+pad)
            alpha = (lab[y0:y1, x0:x1] == i).astype(np.uint8) * 255
            # alpha 同時帶入原稿深度，淡鉛筆不會被硬變成黑色。
            alpha = (alpha.astype(np.float32) * (255-gray[y0:y1, x0:x1]) / 255).astype(np.uint8)
            rgba = cv2.cvtColor(gray[y0:y1, x0:x1], cv2.COLOR_GRAY2BGRA); rgba[:, :, 3] = alpha
            typ = kind(int(area), int(w), int(h)); name = f"s{n:06d}.png"
            cv2.imwrite(os.path.join(out, name), rgba)
            rows.append({"file": name, "kind": typ, "area": int(area), "w": int(w), "h": int(h)})
            counts[typ] += 1; n += 1
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f: json.dump(rows, f, ensure_ascii=False, indent=1)
    print(json.dumps({"strokes": n, **counts}, ensure_ascii=False))


if __name__ == "__main__": main()
