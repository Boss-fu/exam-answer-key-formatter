# -*- coding: utf-8 -*-
"""把產出的 docx 轉成 PDF 並拼成每 4 頁一張的預覽圖，用來目視校對排版。
用法：python render.py <docx> [輸出資料夾]"""
import sys, os, glob, subprocess, shutil
from PIL import Image
docx = sys.argv[1]; out = sys.argv[2] if len(sys.argv) > 2 else '/home/claude/exam/render'
shutil.rmtree(out, ignore_errors=True); os.makedirs(out)
tmp = os.path.join(out, 'x.docx'); shutil.copy(docx, tmp)
soffice = '/mnt/skills/public/docx/scripts/office/soffice.py'
cmd = ['python3', soffice, '--headless', '--convert-to', 'pdf', '--outdir', out, tmp] if os.path.exists(soffice) else ['soffice', '--headless', '--convert-to', 'pdf', '--outdir', out, tmp]
subprocess.run(cmd, cwd=out, capture_output=True)
pdf = os.path.join(out, 'x.pdf')
subprocess.run(['pdftoppm', '-jpeg', '-r', '60', pdf, os.path.join(out, 'p')], check=True)
fs = sorted(glob.glob(os.path.join(out, 'p-*.jpg'))); ims = [Image.open(f) for f in fs]
w, h = ims[0].size; res = []
for k in range(0, len(ims), 4):
    g = ims[k:k + 4]; S = Image.new('RGB', (w * len(g), h), 'white')
    for i, im in enumerate(g): S.paste(im, (i * w, 0))
    p = os.path.join(out, f'pages_{k // 4 + 1}.jpg'); S.save(p); res.append(p)
print(len(ims), 'pages'); print('\n'.join(res))
print('註：這是 LibreOffice 轉出的預覽，浮動圖片的位置可能和 Word 略有差異。')
