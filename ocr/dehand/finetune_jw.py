#!/usr/bin/env python3
"""第 7 輪：從俊瑋 v10 出發，用我們的 std 資料微調（俊瑋口頭授權，見 ocr/models/jw/README.md）。

學的是他網頁的「打印版」＝ clip((ch0-0.25)/0.63)，目標是我們的乾淨版 gt（有 _gt_fixed.png 就用修補版，
std_qa.csv 判 bad 的頁不用）。比賽頁的考卷整份不進訓練。第 0 個 epoch 先量他原版，之後只有比原版好才存。
    python ocr/dehand/finetune_jw.py --out ocr/dehand/runs/jw-ft-r7 [--epochs 12]
"""
import argparse, csv, glob, json, os, random, sys
import cv2, numpy as np, onnx, torch
from onnx2torch import convert
sys.path.insert(0, os.path.dirname(__file__))
from bench_vs_jw import JW
from pairs_build import ink


def load(std, held, qa):
    bad = set()
    if os.path.exists(qa):
        for r in csv.DictReader(open(qa, encoding='utf-8-sig')):
            if r.get('判定') == 'bad': bad.add((r['卷'], int(r['頁'])))
    pages = []
    for x in sorted(glob.glob(os.path.join(std, '*', '*_in.jpg'))):
        exam = os.path.basename(os.path.dirname(x)); page = int(os.path.basename(x)[:3])
        if exam in held or (exam, page) in bad: continue
        g = x.replace('_in.jpg', '_gt_fixed.png'); g = g if os.path.exists(g) else x.replace('_in.jpg', '_gt.png')
        pages.append(dict(xp=x, cp=g, exam=exam))   # 只存路徑：1500 頁全載入會吃掉十幾 GB 記憶體
    return pages


def read(p):
    a, b = cv2.imread(p['xp'], 0), cv2.imread(p['cp'], 0)
    if a is None or b is None or a.shape != b.shape: return None
    return dict(x=a, c=b, hand=ink(a) & ~(cv2.dilate(ink(b).astype('uint8'), np.ones((7, 7), np.uint8)) > 0))


def to_print(out):
    return torch.clamp((out[:, :1] - .25) / (.88 - .25), 0, 1)


def crop(tr, rng, s=384):
    p = None
    while p is None: p = read(rng.choice(tr))
    h, w = p['x'].shape; y, x = rng.randrange(0, max(1, h - s)), rng.randrange(0, max(1, w - s))
    ys, xs = np.nonzero(p['hand'][::8, ::8])
    if len(ys) and rng.random() < .5:   # 一半切塊對準筆跡（Gemini 建議），不然多數是白底、學不到擦
        j = rng.randrange(len(ys)); y = min(max(0, ys[j] * 8 - s // 2), max(0, h - s)); x = min(max(0, xs[j] * 8 - s // 2), max(0, w - s))
    return [np.pad(a[y:y + s, x:x + s], ((0, s - min(s, h - y)), (0, s - min(s, w - x))), constant_values=v)
            for a, v in ((p['x'], 255), (p['c'], 255), (p['hand'], 0))]


@torch.no_grad()
def score(model, pages, dev):
    ps, keep, rem = [], [0, 0], [0, 0]
    for p in pages:
        h, w = p['x'].shape; H, W = h + (-h) % 16, w + (-w) % 16
        x = np.pad(p['x'], ((0, H - h), (0, W - w)), mode='edge').astype('float32') / 255
        y = to_print(model(torch.from_numpy(np.repeat(x[None, None], 3, 1)).to(dev)))[0, 0, :h, :w].cpu().numpy()
        c = np.clip((p['c'] / 255. - .25) / .63, 0, 1); ps.append(np.mean((y - c) ** 2)); dark = y < 200 / 255; pr = ink(p['c'])
        keep[0] += (dark & pr).sum(); keep[1] += pr.sum(); rem[0] += (dark & p['hand']).sum(); rem[1] += p['hand'].sum()
    return 10 * np.log10(1 / max(np.mean(ps), 1e-10)), keep[0] / max(1, keep[1]), rem[0] / max(1, rem[1])


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--std', default='ocr/dehand/data/std'); ap.add_argument('--out', default='ocr/dehand/runs/jw-ft-r7')
    ap.add_argument('--epochs', type=int, default=12); ap.add_argument('--steps', type=int, default=150); ap.add_argument('--batch', type=int, default=6)
    ap.add_argument('--lr', type=float, default=2e-5); ap.add_argument('--seed', type=int, default=7); a = ap.parse_args()
    rng = random.Random(a.seed); torch.manual_seed(a.seed); dev = torch.device('mps' if torch.backends.mps.is_available() else 'cpu')
    held = {os.path.basename(os.path.dirname(p)) for p in open('ocr/dehand/bench_jw_pages.txt', encoding='utf8').read().split()}
    pages = load(a.std, held, 'ocr/dehand/data/std_qa.csv'); exams = sorted({p['exam'] for p in pages}); rng.shuffle(exams)
    ve = set(exams[:max(1, len(exams) // 8)]); tr = [p for p in pages if p['exam'] not in ve]; va = [q for q in (read(p) for p in pages if p['exam'] in ve) if q is not None][:60]
    print(f'裝置 {dev}；訓練 {len(tr)} 頁、驗證 {len(va)} 頁（{len(ve)} 份考卷）', flush=True)
    model = convert(onnx.load(JW)).to(dev); model.eval()
    base = score(model, va, dev); best = (-base[2], None)   # 印刷保留不掉超過 0.5 點、PSNR 不低於原版時，挑筆跡殘留最低（練久了殘留會回升，見 r7 log）
    print(f'epoch 0（俊瑋原版） PSNR {base[0]:.2f} 印刷保留 {base[1]:.1%} 筆跡殘留 {base[2]:.1%}', flush=True)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-5)
    for ep in range(1, a.epochs + 1):
        model.train(); tot = 0.
        for _ in range(a.steps):
            b = [crop(tr, rng) for _ in range(a.batch)]
            x = torch.tensor(np.stack([z[0] for z in b]))[:, None].float().div(255).to(dev).repeat(1, 3, 1, 1)
            c = to_print(torch.tensor(np.stack([z[1] for z in b]))[:, None].float().div(255).to(dev))   # gt 也拉成打印版色調，只學「擦不擦」不學色調
            m = torch.tensor(np.stack([z[2] for z in b]))[:, None].float().to(dev)
            y = to_print(model(x)); pr = (c < .78).float()
            loss = (torch.abs(y - c) * float(os.environ.get('FT_BW', 1)) + float(os.environ.get('FT_HW', 3)) * m * torch.relu(c - y)
                    + float(os.environ.get('FT_PW', .5)) * pr * torch.relu(y - c)).mean()   # 筆跡只罰沒擦、印刷只罰擦淡
            opt.zero_grad(); loss.backward(); opt.step(); tot += loss.item()
        model.eval(); s = score(model, va, dev); comb = -s[2] if (s[1] >= base[1] - .005 and s[0] >= base[0]) else -1e9
        print(f'epoch {ep}/{a.epochs} loss {tot / a.steps:.4f} PSNR {s[0]:.2f} 印刷保留 {s[1]:.1%} 筆跡殘留 {s[2]:.1%}', flush=True)
        if comb > best[0]: best = (comb, {k: v.detach().cpu().clone() for k, v in model.state_dict().items()})
    os.makedirs(a.out, exist_ok=True)
    if best[1] is None: print('沒有任何 epoch 贏過俊瑋原版，不輸出模型'); return
    model.load_state_dict(best[1]); model = model.cpu().eval()
    torch.save(best[1], os.path.join(a.out, 'dehand_jwft.pt'))
    path = os.path.join(a.out, 'dehand_jwft.onnx')
    # 轉換來的 Resize 會把追蹤時的尺寸寫死，所以用推論實際用的 512 切塊匯出（apply／網頁都固定 512）
    torch.onnx.export(model, torch.ones(1, 3, 512, 512), path, input_names=['image'], output_names=['out'], opset_version=17, dynamo=False,
                      dynamic_axes={'image': {0: 'b', 2: 'h', 3: 'w'}, 'out': {0: 'b', 2: 'h', 3: 'w'}})
    s = score(model, va, torch.device('cpu'))
    json.dump({'base': base, 'final': s, 'validation_exams': sorted(ve), 'held_bench_exams': sorted(held), 'note': '從俊瑋 v10 微調；輸入輸出格式同原版'},
              open(os.path.join(a.out, 'dehand_jwft.json'), 'w'), ensure_ascii=False, indent=2)
    print('完成：', path)


if __name__ == '__main__':
    main()
