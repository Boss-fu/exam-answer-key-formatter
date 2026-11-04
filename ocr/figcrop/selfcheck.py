#!/usr/bin/env python3
"""figcrop 本地自評：斷邊墨跡、正文行、題號缺失，並輸出覆框圖供目視。"""
import argparse, json, os, sys
from pathlib import Path
import cv2, numpy as np, pymupdf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import crop
from refine import edge_cut_ink


def body_lines(box, lines, width):
    x0, y0, x1, y1 = box
    return sum(1 for l, body in lines if body and l[0] >= x0 and l[2] <= x1 and l[1] >= y0 and l[3] <= y1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf"); ap.add_argument("--out", required=True)
    ap.add_argument("--pages"); ap.add_argument("--dpi", type=int, default=300)
    a = ap.parse_args(); out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(a.pdf); rows = []; totals = {"crops": 0, "edge_cut_ink": 0, "body_lines": 0, "qno_none": 0}
    for pn, page in enumerate(doc, 1):
        if pn not in crop.page_numbers(a.pages, len(doc)):
            continue
        pix = page.get_pixmap(dpi=a.dpi, alpha=False)
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3][:, :, ::-1].copy()
        # crop_page 已完成一次 PP-OCR；不可為自評再跑一次（大型掃描頁會超過
        # 30 秒沙箱限制），直接重用同一批文字行。
        raw = []; boxes, _ = crop.crop_page(img, raw=raw)
        lines = [(line, bool(crop.RE_TEXT_OPTION.search(text))) for line, text in raw]
        overlay = img.copy(); page_rows = []
        for i, b in enumerate(boxes, 1):
            edge = edge_cut_ink(img, b); body = body_lines(b, lines, img.shape[1]); qno = crop.guess_qno(b, raw, img.shape[1])
            totals["crops"] += 1; totals["edge_cut_ink"] += edge; totals["body_lines"] += body; totals["qno_none"] += qno is None
            page_rows.append({"box": list(map(int, b)), "qno": qno, "edge_cut_ink": edge, "body_lines": body})
            x0,y0,x1,y1=b; cv2.rectangle(overlay,(x0,y0),(x1,y1),(0,0,255) if edge or body else (0,160,0),3)
            cv2.putText(overlay, f"{i}:q{qno}",(x0,max(24,y0-6)),cv2.FONT_HERSHEY_SIMPLEX,.8,(255,0,0),2)
            cv2.imwrite(str(out / f"p{pn}_fig{i}.png"), img[y0:y1, x0:x1])
        cv2.imwrite(str(out / f"p{pn}_overlay.png"), overlay)
        rows.append({"page": pn, "items": page_rows})
    totals["qno_none_rate"] = round(totals["qno_none"] / totals["crops"], 4) if totals["crops"] else 0
    report = {"pdf": a.pdf, "totals": totals, "pages": rows}
    (out / "selfcheck.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report["totals"], ensure_ascii=False))


if __name__ == "__main__":
    main()
