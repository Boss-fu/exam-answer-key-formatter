#!/usr/bin/env python3
"""從國中原稿以 pencil_rule 建立 150dpi 偽標註，不覆寫真實配對資料。"""
import argparse, hashlib, json, os, re, sys
import cv2, numpy as np, pymupdf

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from pencil_rule import detect
from pairs_build import CLEAN, key

JUNIOR = re.compile(r"國中|[789]年級|國[七八九]", re.I)
ORIG = re.compile(r"原稿")

def safe(s): return re.sub(r'[\\/:*?"<>|\s]+', '_', os.path.splitext(s)[0])[:60]
def pdfs(roots):
    for root in roots:
        for d, _, fs in os.walk(root):
            allpdf = [f for f in fs if f.lower().endswith('.pdf')]
            cleans = {key(f) for f in allpdf if CLEAN.search(f)}
            for f in allpdf:
                if JUNIOR.search(f) and (ORIG.search(f) or key(f) not in cleans) and not CLEAN.search(f):
                    yield os.path.join(d, f)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--roots', nargs='+', required=True); ap.add_argument('--out', default='ocr/dehand/data')
    a = ap.parse_args(); base = os.path.join(a.out, '_pseudo'); os.makedirs(base, exist_ok=True)
    mp = os.path.join(base, 'manifest.json'); manifest = json.load(open(mp, encoding='utf-8')) if os.path.exists(mp) else []
    seen = {(m['pair'], m['page']) for m in manifest}
    for path in pdfs(a.roots):
        name = safe(os.path.basename(path)); group = hashlib.md5(key(os.path.basename(path)).encode()).hexdigest()[:8]
        doc = pymupdf.open(path); kept = 0
        for page_no, p in enumerate(doc, 1):
            if (name, page_no) in seen: continue
            pix = p.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY)
            gray300 = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w).copy()
            mask300 = detect(gray300)
            gray150 = cv2.resize(gray300, (max(1, pix.w // 2), max(1, pix.h // 2)), interpolation=cv2.INTER_AREA)
            mask150 = cv2.resize(mask300, (gray150.shape[1], gray150.shape[0]), interpolation=cv2.INTER_NEAREST)
            if not mask150.any(): continue
            out = os.path.join(base, name); os.makedirs(out, exist_ok=True)
            cv2.imwrite(os.path.join(out, f'p{page_no}_x.png'), gray150); cv2.imwrite(os.path.join(out, f'p{page_no}_m.png'), mask150)
            manifest.append({'pair': name, 'page': page_no, 'group': group, 'source_pdf': path, 'hand_ratio': round(float((mask150 > 0).mean()), 4)})
            kept += 1
        print(f'{name}: {kept} 頁')
    json.dump(manifest, open(mp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

if __name__ == '__main__': main()
