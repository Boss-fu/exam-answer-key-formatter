#!/usr/bin/env python3
"""建立本機考卷資料清單；不修改來源，也不把檔案內容送出電腦。

用途：先把同一考試資料夾的原稿、教師版、重製版固定分在同一組，
避免後續模型訓練時把近乎相同的卷子同時放進訓練與測試集。
檔名僅提供候選角色，絕不代表已人工驗證的標準答案。
"""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


SUPPORTED = {".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def candidate_role(name):
    name = name.lower()
    if any(token in name for token in ("答案", "answer", "_ans", "-ans")):
        return "answer_candidate"
    if any(token in name for token in ("教師", "teacher")):
        return "teacher_candidate"
    if any(token in name for token in ("乾淨", "重製", "清稿", "clean", "remade")):
        return "clean_candidate"
    if any(token in name for token in ("原稿", "原卷", "original", "學生")):
        return "source_candidate"
    return "unclassified"


def split_for_group(group, seed):
    value = int(hashlib.sha256(f"{seed}:{group}".encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "train" if value < 0.8 else "validation" if value < 0.9 else "test"


def build_catalog(root, seed):
    records = []
    duplicate_candidates = {}
    for source in sorted(root.rglob("*")):
        if not source.is_file() or source.suffix.lower() not in SUPPORTED:
            continue
        rel = source.relative_to(root)
        # 以「同一考試資料夾」為最小分組，避免原稿和重製版跨訓練/測試。
        folder = str(rel.parent)
        size = source.stat().st_size
        records.append({
            "path": str(rel),
            "extension": source.suffix.lower(),
            "bytes": size,
            "role_candidate": candidate_role(source.name),
            "source_folder": folder,
            "label_verified": False,
        })
        duplicate_candidates.setdefault((source.name, size), []).append((source, folder))

    parents = {record["source_folder"]: record["source_folder"] for record in records}

    def find(folder):
        while parents[folder] != folder:
            parents[folder] = parents[parents[folder]]
            folder = parents[folder]
        return folder

    def union(first, second):
        a, b = find(first), find(second)
        if a != b:
            parents[max(a, b)] = min(a, b)

    # 同名同大小只是候選；僅在內容雜湊完全相同時，才把跨資料夾複本合組。
    for candidates in duplicate_candidates.values():
        if len(candidates) < 2:
            continue
        same_content = {}
        for source, folder in candidates:
            digest = hashlib.sha256()
            with source.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            same_content.setdefault(digest.hexdigest(), []).append(folder)
        for folders in same_content.values():
            for folder in folders[1:]:
                union(folders[0], folder)

    for record in records:
        group = find(record["source_folder"])
        record["group"] = group
        record["split_candidate"] = split_for_group(group, seed)
    return records


def summarize(records):
    groups = {}
    for record in records:
        groups[record["group"]] = record["split_candidate"]
    return {
        "files": len(records),
        "groups": len(groups),
        "extensions": dict(sorted(Counter(r["extension"] for r in records).items())),
        "role_candidates": dict(sorted(Counter(r["role_candidate"] for r in records).items())),
        "file_splits": dict(sorted(Counter(r["split_candidate"] for r in records).items())),
        "group_splits": dict(sorted(Counter(groups.values()).items())),
        "verified_labels": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="原始考卷資料夾（唯讀）")
    parser.add_argument("--out", required=True, type=Path, help="輸出 JSON 清單")
    parser.add_argument("--seed", default="tzutzu-ocr-v1", help="固定資料分組的種子")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"找不到資料夾：{root}")
    records = build_catalog(root, args.seed)
    result = {
        "schema_version": 1,
        "source_root": str(root),
        "seed": args.seed,
        "warning": "檔名與分組僅供候選；乾淨版可能仍有筆跡，任何檔案都尚未成為訓練標籤。",
        "summary": summarize(records),
        "files": records,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"清單：{args.out}")


if __name__ == "__main__":
    main()
