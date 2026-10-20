#!/usr/bin/env python3
"""依既有 manifest 補回遺漏的對齊乾淨頁 *_c.png；不重建遮罩或改 manifest。"""
import argparse, json, os
import cv2
import pairs_build as pb


def index_files(roots):
    out = {}
    for root in roots:
        for d, _, fs in os.walk(root):
            for f in fs:
                if f.lower().endswith('.pdf'):
                    out.setdefault(f, []).append(os.path.join(d, f))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--roots', nargs='+', required=True, help='Google Drive 考卷根目錄')
    ap.add_argument('--data', default='ocr/dehand/data')
    a = ap.parse_args()
    manifest = json.load(open(os.path.join(a.data, 'manifest.json'), encoding='utf-8'))
    files, cache, skipped = index_files(a.roots), {}, []
    made = 0
    for row in manifest:
        pair, page = row['pair'], int(row['page'])
        target = os.path.join(a.data, pair, f'p{page}_c.png')
        if os.path.exists(target):
            continue
        origs, cleans = files.get(row.get('orig', ''), []), files.get(row.get('clean', ''), [])
        # 同名檔在不同資料夾時，優先挑同一資料夾的一對，避免 Drive 多份副本配錯。
        choices = [(o, c) for o in origs for c in cleans if os.path.dirname(o) == os.path.dirname(c)] or [(o, c) for o in origs for c in cleans]
        if not choices:
            skipped.append(f'{pair}/p{page}: 找不到 orig 或 clean PDF')
            continue
        done = False
        for opath, cpath in choices:
            try:
                op = cache.setdefault(opath, pb.pages(opath)); cp = cache.setdefault(cpath, pb.pages(cpath))
                if page > len(op):
                    continue
                candidates = [cp[page - 1]] if len(cp) == len(op) else cp
                warped, score = max((pb.align(op[page - 1], c) for c in candidates), key=lambda v: v[1])
                if warped is not None and score >= pb.MIN_AGREE:
                    os.makedirs(os.path.dirname(target), exist_ok=True); cv2.imwrite(target, warped)
                    made += 1; done = True; break
            except Exception as exc:
                last = str(exc)
        if not done:
            skipped.append(f'{pair}/p{page}: 對齊或讀檔失敗 ({locals().get("last", "no usable pair")})')
    log = os.path.join(a.data, 'backfill_clean_skipped.txt')
    with open(log, 'w', encoding='utf-8') as f: f.write('\n'.join(skipped) + ('\n' if skipped else ''))
    print(f'補齊 {made} 頁；跳過 {len(skipped)} 頁：{log}')


if __name__ == '__main__': main()
