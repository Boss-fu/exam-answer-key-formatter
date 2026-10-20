#!/usr/bin/env python3
"""將去筆跡訓練資料打包到 Google Drive 同步資料夾，供 Colab 使用。"""
import argparse
import datetime as dt
import os
from pathlib import Path
import shutil
import tarfile


HERE = Path(__file__).resolve().parent
DEFAULT_DRIVE = Path("/Users/chiafuchang/Library/CloudStorage/GoogleDrive-sandy880507@gmail.com/My Drive/OCR訓練資料")
PAGE_SUFFIXES = ("_x.png", "_c.png", "_m.png", "_q.png")


def archive_members(data: Path):
    """回傳訓練頁與其 manifest，排除筆劃素材庫。"""
    for path in sorted(data.rglob("*")):
        if not path.is_file() or "_strokes" in path.relative_to(data).parts:
            continue
        if path.name == "manifest.json" or path.name.endswith(PAGE_SUFFIXES):
            yield path


def main():
    parser = argparse.ArgumentParser(description="打包去筆跡資料供 Google Colab 訓練")
    parser.add_argument("--data", type=Path, default=HERE / "data")
    parser.add_argument("--drive", type=Path, default=DEFAULT_DRIVE)
    parser.add_argument("--date", default=dt.date.today().strftime("%Y%m%d"), help="檔名日期（預設今天）")
    args = parser.parse_args()

    data = args.data.resolve()
    if not data.is_dir():
        parser.error(f"找不到訓練資料夾：{data}")
    drive = args.drive.expanduser()
    try:
        drive.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        parser.error(f"無法建立或寫入 Drive 資料夾：{drive}（{exc}）")
    target = drive / f"dehand_data_{args.date}.tar"
    members = list(archive_members(data))
    x_pages = sum(path.name.endswith("_x.png") for path in members)
    paired_pages = sum((path.parent / (path.name[:-6] + "_m.png")).is_file()
                       for path in members if path.name.endswith("_x.png"))
    if not x_pages:
        parser.error(f"{data} 沒有 *_x.png 訓練頁")

    # 以 repo 相對路徑封裝，Colab 解到 /content 後可直接得到 ocr/dehand/data/。
    repo = HERE.parents[1]
    with tarfile.open(target, "w") as archive:
        for path in members:
            archive.add(path, arcname=path.relative_to(repo), recursive=False)

    code_dir = drive / "code"
    code_dir.mkdir(exist_ok=True)
    for name in ("train.py", "train_img2img.py", "pairs_build.py"):
        shutil.copy2(HERE / name, code_dir / name)
    size = target.stat().st_size / (1024 * 1024)
    print(f"完成：{target}")
    print(f"大小：{size:.1f} MiB；訓練頁：{x_pages} 頁（有遮罩配對：{paired_pages} 頁）")
    print(f"程式：{code_dir / 'train.py'}、{code_dir / 'pairs_build.py'}")


if __name__ == "__main__":
    main()
