# -*- coding: utf-8 -*-
import os, re, shutil, zipfile
from PIL import Image
import glob as _glob
def _find_template():
    c = [os.environ.get("EXAM_TEMPLATE", ""),
         os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "template.docx")]
    c += sorted(_glob.glob("/mnt/skills/*/exam-to-word/assets/template.docx"))
    for f in c:
        if f and os.path.exists(f): return f
    raise FileNotFoundError("找不到範本：請設定 EXAM_TEMPLATE=<skill>/assets/template.docx")
SRC = _find_template()
BUILD = None; FIGS = None; img_rels = []; next_rid = 100; next_did = 900
def start(build, figs):
    """build: 暫存解壓目錄；figs: 裁好的圖片資料夾（p_image 傳檔名不含 .png）"""
    global BUILD, FIGS, img_rels, next_rid, next_did
    BUILD, FIGS = build, figs; img_rels = []; next_rid = 100; next_did = 900
    if os.path.exists(BUILD): shutil.rmtree(BUILD)
    os.makedirs(BUILD)
    with zipfile.ZipFile(SRC) as z: z.extractall(BUILD)   # 不呼叫 unzip／zip：Windows 主機沒有這兩個指令
# ---------- helpers ----------
def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

FQ = 'w:eastAsia="華康中圓體(P)"'
FH = 'w:eastAsia="華康中圓體"'

def runs(text, font=FQ):
    """markup: _x_ underline ; *x* bold+underline ; \t tab ; \n break"""
    out = []
    tokens = re.split(r'(\*[^*]+\*|\^[^^]+\^|~[^~]+~|_[^_]+_|\t|\n|§[^§]+§)', text)
    for tk in tokens:
        if tk == "":
            continue
        if tk == "\t":
            out.append(f'<w:r><w:rPr><w:rFonts {font}/></w:rPr><w:tab/></w:r>')
        elif tk == "\n":
            out.append(f'<w:r><w:rPr><w:rFonts {font}/></w:rPr><w:br/></w:r>')
        elif tk.startswith("*") and tk.endswith("*") and len(tk) > 2:
            out.append(f'<w:r><w:rPr><w:rFonts {font}/><w:b/><w:bCs/><w:u w:val="single"/></w:rPr>'
                       f'<w:t xml:space="preserve">{esc(tk[1:-1])}</w:t></w:r>')
        elif tk.startswith("~") and tk.endswith("~") and len(tk) > 2:
            out.append(f'<w:r><w:rPr><w:rFonts {font}/><w:vertAlign w:val="subscript"/></w:rPr>'
                       f'<w:t xml:space="preserve">{esc(tk[1:-1])}</w:t></w:r>')
        elif tk.startswith("§") and tk.endswith("§"):
            nm,h=tk[1:-1].split(':'); out.append(inline_img(nm,float(h)))
        elif tk.startswith("^") and tk.endswith("^") and len(tk) > 2:
            out.append(f'<w:r><w:rPr><w:rFonts {font}/><w:b/><w:bCs/></w:rPr>'
                       f'<w:t xml:space="preserve">{esc(tk[1:-1])}</w:t></w:r>')
        elif tk.startswith("_") and tk.endswith("_") and len(tk) > 2:
            out.append(f'<w:r><w:rPr><w:rFonts {font}/><w:u w:val="single"/></w:rPr>'
                       f'<w:t xml:space="preserve">{esc(tk[1:-1])}</w:t></w:r>')
        else:
            out.append(f'<w:r><w:rPr><w:rFonts {font}/></w:rPr>'
                       f'<w:t xml:space="preserve">{esc(tk)}</w:t></w:r>')
    return "".join(out)

def p_question(text):
    ppr = ('<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="18"/></w:numPr>'
           '<w:tabs><w:tab w:val="left" w:pos="3000"/><w:tab w:val="left" w:pos="5280"/>'
           '<w:tab w:val="left" w:pos="7560"/></w:tabs>'
           '<w:adjustRightInd w:val="0"/><w:snapToGrid w:val="0"/>'
           '<w:spacing w:beforeLines="20" w:before="72" w:afterLines="20" w:after="72"/>'
           f'<w:rPr><w:rFonts {FQ}/></w:rPr></w:pPr>')
    return "<w:p>" + ppr + runs(text) + "</w:p>"

def p_intro(text):
    ppr = ('<w:pPr><w:adjustRightInd w:val="0"/><w:snapToGrid w:val="0"/>'
           '<w:spacing w:beforeLines="30" w:before="108" w:afterLines="10" w:after="36"/>'
           '<w:ind w:left="480" w:hanging="240"/>'
           f'<w:rPr><w:rFonts {FQ}/></w:rPr></w:pPr>')
    return "<w:p>" + ppr + runs(text) + "</w:p>"

def p_section(text):
    ppr = ('<w:pPr><w:pStyle w:val="a4"/>'
           '<w:spacing w:beforeLines="30" w:before="108" w:afterLines="30" w:after="108" '
           'w:line="0" w:lineRule="atLeast"/><w:ind w:leftChars="0" w:left="0"/>'
           f'<w:rPr><w:rFonts {FH}/></w:rPr></w:pPr>')
    title, tail = text.split("【", 1)
    body = (f'<w:r><w:rPr><w:rFonts {FH} w:hint="eastAsia"/><w:b/><w:bCs/>'
            '<w:bdr w:val="single" w:sz="4" w:space="0" w:color="auto"/></w:rPr>'
            f'<w:t>{esc(title)}</w:t></w:r>'
            f'<w:r><w:rPr><w:rFonts {FH}/></w:rPr><w:t xml:space="preserve">【{esc(tail)}</w:t></w:r>')
    return "<w:p>" + ppr + body + "</w:p>"

# ---------- images ----------
EMU_IN = 914400

def p_image(name, width_in):
    global next_rid, next_did
    src = os.path.join(FIGS, name + ".png")
    im = Image.open(src)
    ratio = im.height / im.width
    cx = int(width_in * EMU_IN)
    cy = int(cx * ratio)
    rid = f"rIdX{next_rid}"; next_rid += 1
    did = next_did; next_did += 1
    target = f"media/{name}.png"
    shutil.copy(src, os.path.join(BUILD, "word", target))
    img_rels.append((rid, target))
    return ('<w:p><w:pPr><w:adjustRightInd w:val="0"/>'
            '<w:snapToGrid w:val="0"/>'
            '<w:spacing w:beforeLines="10" w:before="36" w:afterLines="10" w:after="36"/>'
            '<w:jc w:val="center"/>'
            f'<w:rPr><w:rFonts {FQ}/></w:rPr></w:pPr>'
            '<w:r><w:rPr><w:noProof/></w:rPr><w:drawing>'
            '<wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{cx}" cy="{cy}"/>'
            '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
            f'<wp:docPr id="{did}" name="圖片 {did}"/>'
            '<wp:cNvGraphicFramePr><a:graphicFrameLocks '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/>'
            '</wp:cNvGraphicFramePr>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:nvPicPr><pic:cNvPr id="{did}" name="{name}.png"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')

# ---------- table for Q2-5 ----------
def tbl(rows, widths, center=True, first_col_center=True):
    tw=sum(widths)
    out=[f'<w:tbl><w:tblPr><w:tblW w:w="{tw}" w:type="dxa"/>'+('<w:jc w:val="center"/>' if center else '')+
         '<w:tblBorders>'+''.join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="auto"/>' for s in ['top','left','bottom','right','insideH','insideV'])+
         '</w:tblBorders><w:tblLayout w:type="fixed"/></w:tblPr><w:tblGrid>'+''.join(f'<w:gridCol w:w="{w}"/>' for w in widths)+'</w:tblGrid>']
    for r in rows:
        out.append('<w:tr><w:trPr><w:jc w:val="center"/></w:trPr>'); ci=0
        for cell in r:
            span=1
            if isinstance(cell,tuple): cell,span=cell
            w=sum(widths[ci:ci+span]); ci+=span
            diag=(cell=='╲')
            if diag: cell=''
            out.append(f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>'+(f'<w:gridSpan w:val="{span}"/>' if span>1 else '')+
                       ('<w:tcBorders><w:tl2br w:val="single" w:sz="4" w:space="0" w:color="auto"/></w:tcBorders>' if diag else '')+
                       '<w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:snapToGrid w:val="0"/>'
                       '<w:spacing w:beforeLines="5" w:before="18" w:afterLines="5" w:after="18"/><w:jc w:val="center"/>'
                       f'<w:rPr><w:rFonts {FQ}/></w:rPr></w:pPr>'+(runs(cell) if cell else '')+'</w:p></w:tc>')
        out.append('</w:tr>')
    out.append('</w:tbl>')
    out.append(f'<w:p><w:pPr><w:spacing w:after="0" w:line="120" w:lineRule="exact"/><w:rPr><w:rFonts {FQ}/></w:rPr></w:pPr></w:p>')
    return ''.join(out)
# ---------- content ----------
def q(stem, opts, tab=None):
    """tab=None -> auto"""
    def w(s):
        return sum(2 if ord(c) > 0x2000 else 1 for c in s)
    plain = [re.sub(r'[*_^~]', '', o) for o in opts]
    if not opts:
        return p_question(stem)
    if tab is None:
        tab = len(opts) <= 4 and max(w(o) for o in plain) <= 13
    sep = "\t" if tab else "　"
    return p_question(stem + "\n" + sep.join(opts))


def opts_para(t):
    """題目之後獨立一段的選項（題幹與選項之間夾了圖或表時使用）"""
    return ('<w:p><w:pPr><w:adjustRightInd w:val="0"/><w:snapToGrid w:val="0"/><w:spacing w:afterLines="20" w:after="72"/>'
            f'<w:ind w:left="960"/><w:rPr><w:rFonts {FQ}/></w:rPr></w:pPr>'+runs(t)+'</w:p>')


def note(t):
    """純文字模式的圖片佔位標註：置中輸出「〔含圖：<簡短說明>〕」。
    用於不裁圖、由使用者自行框選截圖貼上的流程；<t> 只寫簡短說明，別重覆題幹。"""
    return ('<w:p><w:pPr><w:snapToGrid w:val="0"/>'
            '<w:spacing w:beforeLines="10" w:before="36" w:afterLines="10" w:after="36"/>'
            f'<w:jc w:val="center"/><w:rPr><w:rFonts {FQ}/></w:rPr></w:pPr>'
            + runs("〔含圖：" + t + "〕") + '</w:p>')

def finish(C, out, repl=()):
    """C: 段落 XML 字串清單；out: 輸出 .docx；repl: 橫幅文字替換 [(舊,新)]"""
    DOC = os.path.join(BUILD, "word/document.xml"); RELS = os.path.join(BUILD, "word/_rels/document.xml.rels")
    d = open(DOC, encoding="utf-8").read()
    mi = d.index('w14:paraId="54D94374"'); cut = d.rindex('<w:p ', 0, mi); prefix = d[:cut]
    for a,b in repl: prefix = prefix.replace(a,b)
    suffix = '<w:sectPr w:rsidR="006C572B" w:rsidRPr="0065334C" w:rsidSect="00730088"><w:footerReference w:type="default" r:id="rId40"/><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="567" w:right="567" w:bottom="567" w:left="567" w:header="0" w:footer="340" w:gutter="0"/><w:cols w:space="425"/><w:docGrid w:type="lines" w:linePitch="360"/></w:sectPr></w:body></w:document>'
    open(DOC, "w", encoding="utf-8").write(prefix + "".join(C) + suffix)
    r = open(RELS, encoding="utf-8").read()
    r = r.replace("</Relationships>", "".join(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="{tgt}"/>' for rid,tgt in img_rels) + "</Relationships>")
    open(RELS, "w", encoding="utf-8").write(r)
    # 範本可能附帶上一份考卷的圖片。內容截斷後那些圖片不再被任何 XML 引用，
    # 若照原樣打包，使用者下載的 Word 會帶著錯誤的舊圖，也會讓檔案無故變大。
    # 只刪除「整個 word/ XML 都沒有引用」的 media，保留頁首頁尾與本次新插入的圖。
    media_dir = os.path.join(BUILD, "word", "media")
    if os.path.isdir(media_dir):
        xml = ""
        rel_ids = set()
        for root, _dirs, names in os.walk(os.path.join(BUILD, "word")):
            for name in names:
                if name.endswith(".xml") and not name.endswith(".rels"):
                    try:
                        part = open(os.path.join(root, name), encoding="utf-8").read()
                        xml += part
                        rel_ids.update(re.findall(r'\b(?:r:embed|r:id)="([^"]+)"', part))
                    except UnicodeDecodeError:
                        pass
        relationships = ""
        rel_files = []
        for root, _dirs, names in os.walk(os.path.join(BUILD, "word")):
            for name in names:
                if name.endswith(".rels"):
                    try:
                        rel_path = os.path.join(root, name)
                        rel_text = open(rel_path, encoding="utf-8").read()
                        rel_files.append((rel_path, rel_text))
                        relationships += rel_text
                    except UnicodeDecodeError:
                        pass
        def keep_rel(match):
            tag, rid, target = match.group(0), match.group(1), match.group(2)
            return "" if target.startswith("media/") and rid not in rel_ids else tag
        for rel_path, rel_text in rel_files:
            rel_text = re.sub(r'<Relationship\b(?=[^>]*\bId="([^"]+)")(?=[^>]*\bTarget="([^"]+)")[^>]*/>', keep_rel, rel_text)
            open(rel_path, "w", encoding="utf-8").write(rel_text)
        relationships = "".join(open(path, encoding="utf-8").read() for path, _ in rel_files)
        for name in os.listdir(media_dir):
            direct = f"media/{name}" in xml
            linked = any(rid in rel_ids and target.endswith(f"media/{name}")
                         for rid, target in re.findall(r'<Relationship\b[^>]*\bId="([^"]+)"[^>]*\bTarget="([^"]+)"', relationships))
            if not direct and not linked:
                os.remove(os.path.join(media_dir, name))
    if os.path.exists(out): os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, names in os.walk(BUILD):
            for name in names:
                f = os.path.join(root, name)
                z.write(f, os.path.relpath(f, BUILD))
    print("ok", out)

def inline_img(name, h_in):
    global next_rid, next_did
    src=os.path.join(FIGS,name+".png"); im=Image.open(src)
    cy=int(h_in*914400); cx=int(cy*im.width/im.height)
    rid=f"rIdX{next_rid}"; next_rid+=1; did=next_did; next_did+=1
    target=f"media/{name}.png"; shutil.copy(src,os.path.join(BUILD,"word",target)); img_rels.append((rid,target))
    return ('<w:r><w:rPr><w:noProof/><w:position w:val="-8"/></w:rPr><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{cx}" cy="{cy}"/><wp:effectExtent l="0" t="0" r="0" b="0"/><wp:docPr id="{did}" name="圖片 {did}"/>'
            '<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:nvPicPr><pic:cNvPr id="{did}" name="{name}.png"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>')
