#!/usr/bin/env python3
"""把 NAS「自動切圖訓練資料」同步成評測用標準答案（ocr/results/figcrop_gt/<卷>/，不進 git）。
PDF「111_南市復興_國七上二段_…pdf」與 ZIP「南市復興國中-七年級-111-…_圖表素材（手動）.zip」用 學年＋學校 配對；
（手動）或沒標的 ZIP＝標準答案 manual/，（自動）ZIP 另存 auto/ 供比較。ZIP 中文檔名以 cp437→utf-8 解。
    python ocr/figcrop/sync_gt.py [來源資料夾]"""
import os, re, shutil, sys, zipfile

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/Desktop/自動切圖訓練資料")   # 使用者持續丟入；NAS 版：/Volumes/教務部門/…/自動切圖訓練資料
DST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "figcrop_gt")


def unzip(zp, dst):
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    os.makedirs(dst)
    for i in zipfile.ZipFile(zp).infolist():
        n = i.filename
        if not (i.flag_bits & 0x800):
            try: n = n.encode("cp437").decode("utf-8")
            except Exception: pass
        if not n.endswith("/"):
            open(os.path.join(dst, os.path.basename(n)), "wb").write(zipfile.ZipFile(zp).read(i))


def main():
    files = os.listdir(SRC)
    for pdf in sorted(f for f in files if f.lower().endswith(".pdf")):
        m = re.match(r"(\d{3})_([^_]+?)_", pdf)
        if m:
            year, school = m.groups()
        else:                                   # 「石牌國中-七年級-111-上學期-…_乾淨.pdf」
            m = re.match(r"(.+?)(?:國中|高中|中學)?-[^-]*年級-(\d{3})-", pdf)
            if not m:
                continue
            school, year = m.groups()
        zips = [z for z in files if z.lower().endswith(".zip") and school in z and year in z]
        if not zips:
            continue
        out = os.path.join(DST, f"{year}_{school}"); os.makedirs(out, exist_ok=True)
        shutil.copy(os.path.join(SRC, pdf), os.path.join(out, "exam.pdf"))
        for z in zips:
            unzip(os.path.join(SRC, z), os.path.join(out, "auto" if "自動" in z else "manual"))
        print(f"{year}_{school}：", "、".join(sorted("auto" if "自動" in z else "manual" for z in zips)))
        img_variant(out)


def img_variant(out):
    """有文字層的 PDF 另做一份「純圖片」版（200 dpi 影像、無文字層），模擬掃描卷：
    截圖程式在圖片型 PDF 只能靠 OCR，這才是實際會出錯的情況。標準答案共用同一份手動截圖。"""
    import pymupdf
    src = pymupdf.open(os.path.join(out, "exam.pdf"))
    if len(src[0].get_text().strip()) <= 40:
        return                                    # 本來就是掃描卷
    dst_dir = out + "_圖片型"; os.makedirs(dst_dir, exist_ok=True)
    doc = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=200)
        p2 = doc.new_page(width=page.rect.width, height=page.rect.height)
        p2.insert_image(p2.rect, stream=pix.tobytes("jpeg", jpg_quality=88))
    doc.save(os.path.join(dst_dir, "exam.pdf"))
    if os.path.isdir(os.path.join(dst_dir, "manual")):
        shutil.rmtree(os.path.join(dst_dir, "manual"))
    shutil.copytree(os.path.join(out, "manual"), os.path.join(dst_dir, "manual"))


if __name__ == "__main__":
    main()
