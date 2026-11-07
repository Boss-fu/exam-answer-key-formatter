#!/usr/bin/env python3
"""把 NAS「自動切圖訓練資料」同步成評測用標準答案（ocr/results/figcrop_gt/<卷>/，不進 git）。
PDF「111_南市復興_國七上二段_…pdf」與 ZIP「南市復興國中-七年級-111-…_圖表素材（手動）.zip」用 學年＋學校 配對；
ZIP 是手動還是自動，看 ZIP 內 manifest.json 各圖的 kind（手動框選／自動裁切），使用者不用改檔名；沒有 manifest 才看檔名
（含「自動」或 auto＝自動，其他＝手動）。手動＝標準答案 manual/；自動另存 auto/（同一份卷有多個自動 ZIP 時，
auto_<括號內的名稱>，例如 auto_V10 auto）。ZIP 中文檔名以 cp437→utf-8 解。
    python ocr/figcrop/sync_gt.py [來源資料夾]"""
import json, os, re, shutil, sys, zipfile

# 使用者持續丟入；資料夾曾從桌面搬進待匯入，兩處都找。NAS 版：/Volumes/教務部門/…/自動切圖訓練資料
_DEFAULTS = [os.path.expanduser(p) for p in ("~/Desktop/待匯入/自動切圖訓練資料", "~/Desktop/自動切圖訓練資料")]
SRC = sys.argv[1] if len(sys.argv) > 1 else next((p for p in _DEFAULTS if os.path.isdir(p)), _DEFAULTS[0])
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


def zip_kind(zp):
    """manual 或 auto。以 manifest 的 kind 為準（多數決），沒有 manifest 才看檔名。"""
    try:
        z = zipfile.ZipFile(zp)
        m = next((n for n in z.namelist() if n.endswith("manifest.json")), None)
        if m:
            kinds = [a.get("kind", "") for a in json.loads(z.read(m)).get("assets", [])]
            auto, manual = sum("自動" in k for k in kinds), sum("手動" in k for k in kinds)
            if auto != manual:
                return "auto" if auto > manual else "manual"
    except (zipfile.BadZipFile, ValueError, KeyError):
        pass
    name = os.path.basename(zp)
    return "auto" if ("自動" in name or "auto" in name.lower()) else "manual"


def zip_label(zp):
    m = re.search(r"[（(]([^（）()]+)[）)]\.zip$", os.path.basename(zp))
    return re.sub(r'[\/:*?"<>|]', "_", m.group(1)) if m else os.path.splitext(os.path.basename(zp))[0][-8:]


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
        kinds = {z: zip_kind(os.path.join(SRC, z)) for z in zips}
        manual = [z for z in zips if kinds[z] == "manual"]; autos = [z for z in zips if kinds[z] == "auto"]
        if len(manual) != 1:
            print(f"{year}_{school}：手動 ZIP 有 {len(manual)} 個，略過（{'、'.join(zips)}）")
            continue
        out = os.path.join(DST, f"{year}_{school}"); os.makedirs(out, exist_ok=True)
        shutil.copy(os.path.join(SRC, pdf), os.path.join(out, "exam.pdf"))
        unzip(os.path.join(SRC, manual[0]), os.path.join(out, "manual"))
        dirs = ["auto"] if len(autos) == 1 else [f"auto_{zip_label(os.path.join(SRC, z))}" for z in autos]
        for z, d in zip(autos, dirs):
            unzip(os.path.join(SRC, z), os.path.join(out, d))
        print(f"{year}_{school}：", "、".join(["manual"] + dirs))
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
