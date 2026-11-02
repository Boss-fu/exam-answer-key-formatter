#!/usr/bin/env python3
"""跟俊瑋「考卷去筆跡」網頁工具（handwriting-eraser-v10）用同一把尺比。

他的模型（俊瑋口頭授權，見 ocr/models/jw/README.md）放在 ocr/models/jw/。
前後處理照他網頁 eraser.html：RGB 0–1 輸入、512 切塊重疊 48、取輸出第 0 通道，
再做「打印版」對比拉伸 (g-0.25)/(0.88-0.25)——這就是使用者在他網頁上看到的成品。

    python ocr/dehand/bench_vs_jw.py --ours ocr/dehand/runs/img2img-r6/dehand_img2img.onnx
頁面清單：ocr/dehand/bench_jw_pages.txt（每行一個 std 的 *_in.jpg；這些考卷之後不可進訓練）。
"""
import argparse, os, sys
import cv2, numpy as np
import onnxruntime as ort
sys.path.insert(0, os.path.dirname(__file__))
from bench_zizi import run_onnx, score
from pairs_build import ink

JW = os.environ.get('JW_MODEL', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'models', 'jw', 'handwriting-eraser-v10.onnx'))
TILE, OVERLAP = 512, 48


def run_jw(sess, gray):
    H, W = gray.shape; x = np.repeat((gray.astype('float32') / 255)[None], 3, 0)
    Hp, Wp = max(H, TILE), max(W, TILE); x = np.pad(x, ((0, 0), (0, Hp - H), (0, Wp - W)), mode='edge')
    yy, xx = np.mgrid[0:TILE, 0:TILE]; wt = np.minimum(np.minimum(np.minimum(yy + 1, TILE - yy), np.minimum(xx + 1, TILE - xx)), OVERLAP).astype('float32')
    def starts(n):
        s = list(range(0, n - TILE + 1, TILE - OVERLAP)) or [0]
        return s if s[-1] == n - TILE else s + [n - TILE]
    acc = np.zeros((Hp, Wp), 'float32'); ws = np.zeros((Hp, Wp), 'float32')
    for y0 in starts(Hp):
        for x0 in starts(Wp):
            out = sess.run(None, {'image': np.ascontiguousarray(x[None, :, y0:y0 + TILE, x0:x0 + TILE])})[0][0, 0]
            acc[y0:y0 + TILE, x0:x0 + TILE] += out * wt; ws[y0:y0 + TILE, x0:x0 + TILE] += wt
    g = (acc / ws)[:H, :W]
    return (np.clip((g - .25) / (.88 - .25), 0, 1) * 255).astype('uint8')


def mask_erase(gray):
    # --ours mask：現行遮罩法（apply.erase，DEHAND_MODEL_DIR 可指定候選），當作舊基準
    from apply import erase
    out, _ = erase(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), dpi=150)
    return cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--ours', required=True); ap.add_argument('--pages', default='ocr/dehand/bench_jw_pages.txt')
    ap.add_argument('--out', default='ocr/results/vs_jw'); a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    sess = ort.InferenceSession(JW, providers=['CPUExecutionProvider'])
    tot = {'俊瑋v10': [], '我們': []}
    for k, src in enumerate(open(a.pages, encoding='utf8').read().split()):
        inp = cv2.imread(src, 0); gt = cv2.imread(src.replace('_in.jpg', '_gt.png'), 0)
        hand = ink(inp) & ~(cv2.dilate(ink(gt).astype('uint8'), np.ones((7, 7), np.uint8)) > 0)
        res = {'俊瑋v10': run_jw(sess, inp), '我們': mask_erase(inp) if a.ours == 'mask' else run_onnx(a.ours, inp)}
        for n, img in res.items():
            s = score(img, gt, hand); tot[n].append(s)
            print(f'頁{k + 1} {n}: PSNR {s[0]:.2f} 印刷保留 {s[1]:.1%} 筆跡殘留 {s[2]:.1%}')
        panel = np.hstack([inp, gt, res['俊瑋v10'], res['我們']])
        for i, t in enumerate(('input', 'GT', 'JW v10', 'ours')): cv2.putText(panel, t, (10 + i * inp.shape[1], 40), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0,), 3)
        cv2.imwrite(os.path.join(a.out, f'p{k + 1}.jpg'), panel, [cv2.IMWRITE_JPEG_QUALITY, 80])
    for n, v in tot.items():
        m = np.mean(v, 0); print(f'平均 {n}: PSNR {m[0]:.2f} 印刷保留 {m[1]:.1%} 筆跡殘留 {m[2]:.1%}')


if __name__ == '__main__':
    main()
