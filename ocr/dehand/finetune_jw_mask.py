#!/usr/bin/env python3
"""第 8 輪：從俊瑋 v10 出發，專練第 1 通道「筆跡機率」（乾淨卷 hand 模式用的就是它）。

拆解 v10：小型 U-Net（2.76M 參數），輸出 2 通道——0＝清稿灰階、1＝筆跡 logit。
v10 會把圖的細曲線也標成筆跡（111 石牌 pH 曲線被擦），所以這輪：
- 筆跡標籤＝std 配對「輸入有墨、乾淨版沒有」的像素（同 finetune_jw.py）；
- 1/4 批次是「乾淨版裡的圖」、1/4 是「乾淨版的一般印刷」，輸入就是乾淨版，筆跡目標全 0（反例：圖、字都不是筆跡）；
- 第 0 通道拉住不偏離 v10 原本輸出（老師模型），只動筆跡判斷。
挑 epoch：綜合分＝筆跡抓到率 − 3×(印刷誤標＋圖誤標)，且筆跡抓到率 ≥ 85%（v10 原版：90.6%／11.9%／5.6%，誤標是擦掉印刷與圖的主因）。
    python ocr/dehand/finetune_jw_mask.py --out ocr/dehand/runs/jw-mask-r8 [--epochs 8]
"""
import argparse, copy, json, os, random, sys
import cv2, numpy as np, onnx, torch
from onnx2torch import convert
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from bench_vs_jw import JW
from finetune_jw import load, read
from pairs_build import ink
from pyexam import figure_candidates

TH = .35        # 乾淨卷 hand 模式的門檻


def figs(p):
    """乾淨版上的圖框（快取在 dict 裡）。std 影像約 A4 寬，dpi 由寬度推。"""
    if 'figs' not in p:
        c = cv2.imread(p['cp'], 0)
        p['figs'] = [] if c is None else figure_candidates(cv2.cvtColor(c, cv2.COLOR_GRAY2BGR), dpi=max(100, round(c.shape[1] / 8.27)))
    return p['figs']


def pad(a, s, v):
    return np.pad(a, ((0, s - a.shape[0]), (0, s - a.shape[1])), constant_values=v)


def crop(tr, rng, s=384):
    """回傳 (輸入, 筆跡目標, 種類)。種類：0 筆跡頁、1 圖反例、2 印刷反例。"""
    kind = rng.choices((0, 1, 2), (.5, .25, .25))[0]
    while True:
        p = rng.choice(tr)
        if kind == 1 and not figs(p):
            continue
        q = read(p)
        if q is None:
            continue
        h, w = q['x'].shape
        if kind == 1:
            x0, y0, x1, y1 = rng.choice(p['figs'])
            y = min(max(0, (y0 + y1) // 2 - s // 2 + rng.randrange(-s // 4, s // 4 + 1)), max(0, h - s))
            x = min(max(0, (x0 + x1) // 2 - s // 2 + rng.randrange(-s // 4, s // 4 + 1)), max(0, w - s))
        else:
            y, x = rng.randrange(0, max(1, h - s)), rng.randrange(0, max(1, w - s))
            ys, xs = np.nonzero(q['hand'][::8, ::8])
            if kind == 0 and len(ys) and rng.random() < .6:
                j = rng.randrange(len(ys)); y = min(max(0, ys[j] * 8 - s // 2), max(0, h - s)); x = min(max(0, xs[j] * 8 - s // 2), max(0, w - s))
        if kind == 0:
            return pad(q['x'][y:y + s, x:x + s], s, 255), pad(q['hand'][y:y + s, x:x + s].astype(np.uint8), s, 0), 0
        c = q['c'][y:y + s, x:x + s]
        return pad(c, s, 255), np.zeros((s, s), np.uint8), kind


@torch.no_grad()
def score(model, va, dev):
    """回傳 (筆跡抓到率, 印刷誤標率, 圖誤標率)。"""
    got = [0, 0]; fp = [0, 0]; ffp = [0, 0]
    for p, q in va:
        for img, hand, pr, region in ((q['x'], q['hand'], ink(q['c']), None), (q['c'], None, ink(q['c']), p['figs'])):
            h, w = img.shape; H, W = h + (-h) % 16, w + (-w) % 16
            x = np.pad(img, ((0, H - h), (0, W - w)), mode='edge').astype('float32') / 255
            m = torch.sigmoid(model(torch.from_numpy(np.repeat(x[None, None], 3, 1)).to(dev))[0, 1, :h, :w]).cpu().numpy() > TH
            if hand is not None:
                got[0] += (m & hand).sum(); got[1] += hand.sum()
                fp[0] += (m & pr).sum(); fp[1] += pr.sum()
            else:
                for x0, y0, x1, y1 in region:
                    ffp[0] += (m & pr)[y0:y1, x0:x1].sum(); ffp[1] += pr[y0:y1, x0:x1].sum()
    return got[0] / max(1, got[1]), fp[0] / max(1, fp[1]), ffp[0] / max(1, ffp[1])


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--std', default='ocr/dehand/data/std'); ap.add_argument('--out', default='ocr/dehand/runs/jw-mask-r8')
    ap.add_argument('--epochs', type=int, default=8); ap.add_argument('--steps', type=int, default=150); ap.add_argument('--batch', type=int, default=6)
    ap.add_argument('--lr', type=float, default=2e-5); ap.add_argument('--seed', type=int, default=8); a = ap.parse_args()
    rng = random.Random(a.seed); torch.manual_seed(a.seed); dev = torch.device('mps' if torch.backends.mps.is_available() else 'cpu')
    held = {os.path.basename(os.path.dirname(p)) for p in open('ocr/dehand/bench_jw_pages.txt', encoding='utf8').read().split()}
    pages = load(a.std, held, 'ocr/dehand/data/std_qa.csv'); exams = sorted({p['exam'] for p in pages}); rng.shuffle(exams)
    ve = set(exams[:max(1, len(exams) // 8)]); tr = [p for p in pages if p['exam'] not in ve]
    va = [(p, q) for p, q in ((p, read(p)) for p in pages if p['exam'] in ve) if q is not None][:40]
    for p, _ in va: figs(p)
    print(f'裝置 {dev}；訓練 {len(tr)} 頁、驗證 {len(va)} 頁（含圖 {sum(bool(p["figs"]) for p, _ in va)} 頁）', flush=True)
    model = convert(onnx.load(JW)).to(dev); model.eval()
    teacher = copy.deepcopy(model).eval()
    for t in teacher.parameters(): t.requires_grad_(False)
    comb = lambda s: s[0] - 3 * (s[1] + s[2])
    base = score(model, va, dev); best = (comb(base), None)
    print(f'epoch 0（俊瑋原版） 筆跡抓到 {base[0]:.1%} 印刷誤標 {base[1]:.2%} 圖誤標 {base[2]:.2%}', flush=True)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-5)
    pw = torch.tensor(float(os.environ.get('FT_POS', 3)), device=dev)
    for ep in range(1, a.epochs + 1):
        model.train(); tot = 0.
        for _ in range(a.steps):
            b = [crop(tr, rng) for _ in range(a.batch)]
            x = torch.tensor(np.stack([z[0] for z in b]))[:, None].float().div(255).to(dev).repeat(1, 3, 1, 1)
            m = torch.tensor(np.stack([z[1] for z in b]))[:, None].float().to(dev)
            out = model(x)
            with torch.no_grad():
                ref = teacher(x)
            # 反例（圖、印刷）上的誤標加重罰：比筆跡漏抓更不能接受
            wneg = torch.tensor([1. if z[2] == 0 else float(os.environ.get('FT_NEG', 3)) for z in b], device=dev)[:, None, None, None]
            bce = torch.nn.functional.binary_cross_entropy_with_logits(out[:, 1:2], m, pos_weight=pw, reduction='none')
            loss = (bce * wneg).mean() + float(os.environ.get('FT_KEEP', 1)) * torch.abs(out[:, :1] - ref[:, :1]).mean()
            opt.zero_grad(); loss.backward(); opt.step(); tot += loss.item()
        model.eval(); s = score(model, va, dev)
        ok = s[0] >= .85
        print(f'epoch {ep}/{a.epochs} loss {tot / a.steps:.4f} 筆跡抓到 {s[0]:.1%} 印刷誤標 {s[1]:.2%} 圖誤標 {s[2]:.2%} 綜合 {comb(s):.3f}（原版 {comb(base):.3f}）{"" if ok else " ✗筆跡抓太少"}', flush=True)
        if ok and comb(s) > best[0]: best = (comb(s), {k: v.detach().cpu().clone() for k, v in model.state_dict().items()})
    os.makedirs(a.out, exist_ok=True)
    if best[1] is None: print('沒有任何 epoch 的綜合分贏過俊瑋原版，不輸出模型'); return
    model.load_state_dict(best[1]); model = model.cpu().eval()
    torch.save(best[1], os.path.join(a.out, 'dehand_jwmask.pt'))
    path = os.path.join(a.out, 'dehand_jwmask.onnx')
    torch.onnx.export(model, torch.ones(1, 3, 512, 512), path, input_names=['image'], output_names=['out'], opset_version=17, dynamo=False,
                      dynamic_axes={'image': {0: 'b', 2: 'h', 3: 'w'}, 'out': {0: 'b', 2: 'h', 3: 'w'}})
    s = score(model, va, torch.device('cpu'))
    json.dump({'base': base, 'final': s, 'metric': '(筆跡抓到率, 印刷誤標率, 圖誤標率) @ p>%.2f' % TH, 'validation_exams': sorted(ve)},
              open(os.path.join(a.out, 'dehand_jwmask.json'), 'w'), ensure_ascii=False, indent=2)
    print('完成：', path)


if __name__ == '__main__':
    main()
