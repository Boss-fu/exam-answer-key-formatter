#!/usr/bin/env python3
"""以少量頁面比較 RapidLayout 與既有 pyexam 圖表候選器。"""
from __future__ import annotations
import argparse, json, sys
from collections import defaultdict
from pathlib import Path
import cv2, fitz, numpy as np

ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT / "ocr"))
import pyexam
try:
    from .refine import edge_cut_ink, has_body_line, refine_records
except ImportError:  # 允許直接 `python ocr/figcrop/bench.py`
    from refine import edge_cut_ink, has_body_line, refine_records

MODELS=("pp_doc_layoutv3", "doclayout_docstructbench", "pp_layout_cdla", "pp_layout_table")

def render(page):
    pix=page.get_pixmap(dpi=300, alpha=False)
    im=np.frombuffer(pix.samples, np.uint8).reshape(pix.height,pix.width,pix.n)
    return cv2.cvtColor(im, cv2.COLOR_RGB2BGR)

def ocr_lines(image):
    # 本機 PP-OCR 行框與辨識；只用於辨別正文停損線及圖號／軸標，無 AI。
    try:
        import ppocr_engine
        out=[]
        for b in ppocr_engine.det_boxes(image):
            xs=[p[0] for p in b]; ys=[p[1] for p in b]
            x0,y0,x1,y1=map(int,(min(xs),min(ys),max(xs),max(ys)))
            text, conf=ppocr_engine.rec_only(image[max(0,y0-3):y1+4,max(0,x0-3):x1+4])
            out.append((x0,y0,x1,y1,text if conf >= .45 else ""))
        return out
    except Exception:
        return []

def rapid_boxes(image, model):
    from rapid_layout import RapidLayout, ModelType
    result=RapidLayout(model_type=getattr(ModelType, model.upper()))(image)
    # 四個模型的類別名稱不同；image/chart 都是考卷上應裁出的圖形。
    figures={"figure", "image", "chart", "header_image"}
    return [{"box":[round(v) for v in b], "kind":"table" if n=="table" else "figure", "class":n, "score":round(float(s),4)}
            for b,n,s in zip(result.boxes, result.class_names, result.scores) if n in figures | {"table"}]

def base_boxes(image):
    out=[{"box":list(t["box"]),"kind":"table","class":"pyexam_table"} for t in pyexam.detect_tables(image, dpi=300)]
    out += [{"box":list(b),"kind":"figure","class":"pyexam_figure"} for b in pyexam.figure_candidates(image, dpi=300)]
    return out

def save_set(outdir, stem, image, rows, lines):
    outdir.mkdir(parents=True, exist_ok=True)
    refined=refine_records(image, rows, lines)
    payload={"boxes": rows, "refined": refined}
    (outdir/f"{stem}.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    for i,r in enumerate(refined):
        x0,y0,x1,y1=r["refined_box"]; cv2.imwrite(str(outdir/f"{stem}_{i:02d}.png"), image[y0:y1,x0:x1])
    return refined

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--input", default=str(Path.home()/"Desktop/待匯入")); ap.add_argument("--out",default=str(ROOT/"ocr/results/figcrop")); ap.add_argument("--max-pages",type=int,default=2); ap.add_argument("--max-pdfs",type=int,default=1)
    a=ap.parse_args(); out=Path(a.out); summary=defaultdict(lambda:[0,0])
    for pdf in sorted(Path(a.input).glob("*_題目.pdf"))[:a.max_pdfs]:
        doc=fitz.open(pdf)
        for pi,page in enumerate(doc[:a.max_pages]):
            image,_=pyexam.remove_color_ink(render(page)); lines=ocr_lines(image); stem=f"{pdf.stem}_p{pi+1:03d}"
            detectors={"pyexam":base_boxes(image)}
            for model in MODELS:
                try: detectors[model]=rapid_boxes(image,model)
                except Exception as e:
                    (out/model).mkdir(parents=True,exist_ok=True); (out/model/f"{stem}.json").write_text(json.dumps({"error":str(e),"boxes":[]},ensure_ascii=False)); detectors[model]=[]
            for name, rows in detectors.items():
                refined=save_set(out/name,stem,image,rows,lines)
                for r in refined:
                    b=r["refined_box"]; ok=edge_cut_ink(image,b)==0 and not has_body_line(b,lines,image.shape[1])
                    summary[name][0]+=int(ok); summary[name][1]+=1
    report={k:{"pass":v[0],"total":v[1],"pass_rate":round(v[0]/v[1],3) if v[1] else None} for k,v in summary.items()}
    (out/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)); print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__ == "__main__": main()
