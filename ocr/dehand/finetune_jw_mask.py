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
from ppocr_engine import ocr_lines

TH = .35        # 乾淨卷 hand 模式的門檻


def figs(p):
    """乾淨版上的圖框（快取在 dict 裡）。std 影像約 A4 寬，dpi 由寬度推。"""
    if 'figs' not in p:
        c = cv2.imread(p['cp'], 0)
        p['figs'] = [] if c is None else figure_candidates(cv2.cvtColor(c, cv2.COLOR_GRAY2BGR), dpi=max(100, round(c.shape[1] / 8.27)))
    return p['figs']


def print_boxes(p):
    """本機 OCR 的高信心印刷框，特別用來教模型別擦題號、括號與細字。"""
    if 'print_boxes' not in p:
        c = cv2.imread(p['cp'], 0)
        boxes = []
        if c is not None:
            for line in ocr_lines(c):
                if float(line.get('conf', 0)) < .80:
                    continue
                q = np.asarray(line['box'], dtype=np.float32)
                x0, y0 = np.floor(q.min(axis=0)).astype(int)
                x1, y1 = np.ceil(q.max(axis=0)).astype(int)
                # 單一題號、括號、粗字或淡灰字也要保留；OCR 有信心就是反例。
                if x1 > x0 and y1 > y0:
                    boxes.append((x0, y0, x1, y1))
        p['print_boxes'] = boxes
    return p['print_boxes']


def load_pseudo(root, limit=None):
    """讀 AI 偽標註；缺檔或空 mask 一律略過，不能讓開發資料污染訓練。"""
    manifest = os.path.join(root, 'manifest.json')
    if not os.path.exists(manifest):
        return []
    try:
        rows = json.load(open(manifest, encoding='utf8'))
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f'無法讀取偽標註 manifest：{manifest}') from e
    out = []
    for row in rows:
        pair, page = str(row.get('pair', '')), str(row.get('page', ''))
        if not pair or not page:
            continue
        stem = page if page.startswith('p') else f'p{page}'
        x = os.path.join(root, pair, stem + '_x.png')
        m = os.path.join(root, pair, stem + '_m.png')
        if os.path.exists(x) and os.path.exists(m):
            out.append(dict(xp=x, mp=m, pair=pair, page=stem))
    return out[:limit] if limit else out


def read_pseudo(p):
    x, m = cv2.imread(p['xp'], 0), cv2.imread(p['mp'], 0)
    if x is None or m is None or x.shape != m.shape or not np.any(m):
        return None
    return x, (m > 0).astype(np.uint8)


def pad(a, s, v):
    return np.pad(a, ((0, s - a.shape[0]), (0, s - a.shape[1])), constant_values=v)


def crop(tr, pseudo, rng, s=384):
    """回傳 (輸入, 筆跡目標, 種類)。種類：0 筆跡頁、1 圖反例、2 印刷反例。"""
    # 額外以 OCR 框集中抽印刷字，涵蓋題號／括號／粗體／淡灰字等最容易誤擦區域。
    kinds, weights = ((0, 1, 2, 3, 4), (.45, .18, .10, .27, .12)) if pseudo else ((0, 1, 2, 3), (.5, .2, .1, .2))
    kind = rng.choices(kinds, weights)[0]
    while True:
        if kind == 4:
            q = read_pseudo(rng.choice(pseudo))
            if q is None:
                continue
            img, hand = q; h, w = img.shape
            y, x = rng.randrange(0, max(1, h - s)), rng.randrange(0, max(1, w - s))
            ys, xs = np.nonzero(hand[::8, ::8])
            if len(ys) and rng.random() < .6:
                j = rng.randrange(len(ys)); y = min(max(0, ys[j] * 8 - s // 2), max(0, h - s)); x = min(max(0, xs[j] * 8 - s // 2), max(0, w - s))
            return pad(img[y:y + s, x:x + s], s, 255), pad(hand[y:y + s, x:x + s], s, 0), 4
        p = rng.choice(tr)
        if kind == 1 and not figs(p):
            continue
        if kind == 3 and not print_boxes(p):
            continue
        q = read(p)
        if q is None:
            continue
        h, w = q['x'].shape
        if kind == 1:
            x0, y0, x1, y1 = rng.choice(p['figs'])
            y = min(max(0, (y0 + y1) // 2 - s // 2 + rng.randrange(-s // 4, s // 4 + 1)), max(0, h - s))
            x = min(max(0, (x0 + x1) // 2 - s // 2 + rng.randrange(-s // 4, s // 4 + 1)), max(0, w - s))
        elif kind == 3:
            x0, y0, x1, y1 = rng.choice(p['print_boxes'])
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
def score(model, va, dev, th=None, max_side=None):
    """回傳 (筆跡抓到率, 印刷誤標率, 圖誤標率)。th 給 list 時回傳每個門檻的結果。"""
    ths = th if isinstance(th, (list, tuple)) else [TH if th is None else th]
    got = np.zeros((len(ths), 2)); fp = np.zeros((len(ths), 2)); ffp = np.zeros((len(ths), 2))
    for p, q in va:
        for img, hand, pr, region in ((q['x'], q['hand'], ink(q['c']), None), (q['c'], None, ink(q['c']), p['figs'])):
            if max_side and max(img.shape) > max_side:
                scale = max_side / max(img.shape)
                size = (round(img.shape[1] * scale), round(img.shape[0] * scale))
                img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
                pr = cv2.resize(pr.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST).astype(bool)
                hand = None if hand is None else cv2.resize(hand.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST).astype(bool)
                region = None if region is None else [tuple(round(v * scale) for v in box) for box in region]
            h, w = img.shape; H, W = h + (-h) % 16, w + (-w) % 16
            x = np.pad(img, ((0, H - h), (0, W - w)), mode='edge').astype('float32') / 255
            prob = torch.sigmoid(model(torch.from_numpy(np.repeat(x[None, None], 3, 1)).to(dev))[0, 1, :h, :w]).cpu().numpy()
            for k, t in enumerate(ths):
                m = prob > t
                if hand is not None:
                    got[k] += ((m & hand).sum(), hand.sum()); fp[k] += ((m & pr).sum(), pr.sum())
                else:
                    for x0, y0, x1, y1 in region:
                        ffp[k] += ((m & pr)[y0:y1, x0:x1].sum(), pr[y0:y1, x0:x1].sum())
    res = [(g[0] / max(1, g[1]), f[0] / max(1, f[1]), ff[0] / max(1, ff[1])) for g, f, ff in zip(got, fp, ffp)]
    return res if isinstance(th, (list, tuple)) else res[0]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--std', default='ocr/dehand/data/std'); ap.add_argument('--out', default='ocr/dehand/runs/jw-mask-r8')
    ap.add_argument('--epochs', type=int, default=8); ap.add_argument('--steps', type=int, default=150); ap.add_argument('--batch', type=int, default=6)
    ap.add_argument('--lr', type=float, default=2e-5); ap.add_argument('--seed', type=int, default=8)
    ap.add_argument('--init', nargs='?', const='r8', default='r8', help='起點：r8（預設）、ONNX 或 checkpoint；找不到 r8 時退回 v10')
    ap.add_argument('--pseudo', action='store_true', help='加入 data/_pseudo/manifest.json 的低權重偽標註')
    ap.add_argument('--pseudo-dir', default='ocr/dehand/data/_pseudo'); ap.add_argument('--pseudo-weight', type=float, default=.3)
    ap.add_argument('--pseudo-limit', type=int, default=242, help='manifest 可用偽標註的使用上限（第 9 輪為 242）')
    ap.add_argument('--limit', type=int, help='smoke test 用：限制訓練、偽標註及驗證頁數；正式訓練勿設')
    a = ap.parse_args()
    rng = random.Random(a.seed); torch.manual_seed(a.seed); dev = torch.device('mps' if torch.backends.mps.is_available() else 'cpu')
    held = {os.path.basename(os.path.dirname(p)) for p in open('ocr/dehand/bench_jw_pages.txt', encoding='utf8').read().split()}
    pages = load(a.std, held, 'ocr/dehand/data/std_qa.csv')
    r8_json = 'ocr/models/jw/jwmask-r8.json'
    if not os.path.exists(r8_json):
        raise RuntimeError(f'缺少 r8 驗證清單：{r8_json}')
    ve = set(json.load(open(r8_json, encoding='utf8'))['validation_exams'])
    tr = [p for p in pages if p['exam'] not in ve]
    va = [(p, q) for p, q in ((p, read(p)) for p in pages if p['exam'] in ve) if q is not None][:40]
    pseudo = load_pseudo(a.pseudo_dir, a.pseudo_limit) if a.pseudo else []
    if a.limit:
        tr, pseudo, va = tr[:a.limit], pseudo[:a.limit], va[:a.limit]
    for p, _ in va: figs(p)
    print(f'裝置 {dev}；訓練 {len(tr)} 頁、偽標註 {len(pseudo)} 頁、驗證 {len(va)} 頁（含圖 {sum(bool(p["figs"]) for p, _ in va)} 頁）', flush=True)
    r8_pt = 'ocr/dehand/runs/jw-mask-r8/dehand_jwmask.pt'; r8_onnx = 'ocr/dehand/runs/jw-mask-r8/dehand_jwmask.onnx'
    init = a.init
    if init == 'r8': init = r8_pt if os.path.exists(r8_pt) else (r8_onnx if os.path.exists(r8_onnx) else JW)
    if not os.path.exists(init):
        raise RuntimeError(f'找不到 --init {init}')
    model = convert(onnx.load(init if init.endswith('.onnx') else JW)).to(dev)
    if init.endswith('.pt'):
        state = torch.load(init, map_location='cpu', weights_only=True)
        model.load_state_dict(state.get('state_dict', state))
    print(f'初始化：{init}', flush=True); model.eval()
    teacher = copy.deepcopy(model).eval()
    for t in teacher.parameters(): t.requires_grad_(False)
    comb = lambda s: s[0] - 3 * (s[1] + s[2])
    smoke_side = 768 if a.limit else None
    base = score(model, va, dev, max_side=smoke_side); best = (comb(base), None, TH)
    print(f'epoch 0（俊瑋原版） 筆跡抓到 {base[0]:.1%} 印刷誤標 {base[1]:.2%} 圖誤標 {base[2]:.2%}', flush=True)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-5)
    pw = torch.tensor(float(os.environ.get('FT_POS', 3)), device=dev)
    for ep in range(1, a.epochs + 1):
        model.train(); tot = 0.
        for _ in range(a.steps):
            b = [crop(tr, pseudo, rng) for _ in range(a.batch)]
            x = torch.tensor(np.stack([z[0] for z in b]))[:, None].float().div(255).to(dev).repeat(1, 3, 1, 1)
            m = torch.tensor(np.stack([z[1] for z in b]))[:, None].float().to(dev)
            out = model(x)
            with torch.no_grad():
                ref = teacher(x)
            # 反例（圖、印刷）上的誤標加重罰：比筆跡漏抓更不能接受
            wneg = torch.tensor([1. if z[2] == 0 else (a.pseudo_weight if z[2] == 4 else float(os.environ.get('FT_NEG', 3))) for z in b], device=dev)[:, None, None, None]
            bce = torch.nn.functional.binary_cross_entropy_with_logits(out[:, 1:2], m, pos_weight=pw, reduction='none')
            loss = (bce * wneg).mean() + float(os.environ.get('FT_KEEP', 1)) * torch.abs(out[:, :1] - ref[:, :1]).mean()
            opt.zero_grad(); loss.backward(); opt.step(); tot += loss.item()
        # 誤標壓低後可以用更低門檻多抓筆跡：每個 epoch 掃 3 個門檻，取綜合分最高者
        model.eval(); THS = (.15, .25, .35); res = score(model, va, dev, list(THS), max_side=smoke_side)
        k = max(range(len(THS)), key=lambda i: comb(res[i]) if res[i][0] >= .85 else -9); s = res[k]
        ok = s[0] >= .85
        print(f'epoch {ep}/{a.epochs} loss {tot / a.steps:.4f} 門檻 {THS[k]} 筆跡抓到 {s[0]:.1%} 印刷誤標 {s[1]:.2%} 圖誤標 {s[2]:.2%} 綜合 {comb(s):.3f}（原版 {comb(base):.3f}）{"" if ok else " ✗筆跡抓太少"}', flush=True)
        os.makedirs(a.out, exist_ok=True)
        torch.save({k2: v.detach().cpu() for k2, v in model.state_dict().items()}, os.path.join(a.out, f'ep{ep}.pt'))   # 每輪都留，事後可挑
        if ok and comb(s) > best[0]: best = (comb(s), {k2: v.detach().cpu().clone() for k2, v in model.state_dict().items()}, THS[k])
    os.makedirs(a.out, exist_ok=True)
    if best[1] is None: print('沒有任何 epoch 的綜合分贏過俊瑋原版，不輸出模型'); return
    model.load_state_dict(best[1]); model = model.cpu().eval()
    torch.save(best[1], os.path.join(a.out, 'dehand_jwmask.pt'))
    path = os.path.join(a.out, 'dehand_jwmask.onnx')
    torch.onnx.export(model, torch.ones(1, 3, 512, 512), path, input_names=['image'], output_names=['out'], opset_version=17, dynamo=False,
                      dynamic_axes={'image': {0: 'b', 2: 'h', 3: 'w'}, 'out': {0: 'b', 2: 'h', 3: 'w'}})
    s = score(model, va, torch.device('cpu'), best[2], max_side=smoke_side)
    json.dump({'base': base, 'final': s, 'threshold': best[2], 'metric': '(筆跡抓到率, 印刷誤標率, 圖誤標率) @ p>threshold', 'validation_exams': sorted(ve), 'init': init, 'pseudo_pages': len(pseudo), 'pseudo_weight': a.pseudo_weight if a.pseudo else 0},
              open(os.path.join(a.out, 'dehand_jwmask.json'), 'w'), ensure_ascii=False, indent=2)
    print('完成：', path)


if __name__ == '__main__':
    main()
