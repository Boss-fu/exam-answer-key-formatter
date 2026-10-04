#!/usr/bin/env bash
# 只部署公開前端；從 HEAD 匯出可避免把 OCR 結果、憑證或未提交檔案送上 Vercel。
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "$script_dir/.." && pwd)"
stage_dir="$(mktemp -d "${TMPDIR:-/tmp}/tzutzu-vercel.XXXXXX")"
cleanup() { rm -rf "$stage_dir"; }
trap cleanup EXIT

short_sha="$(git -C "$repo_dir" rev-parse --short HEAD)"
built_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

git -C "$repo_dir" archive HEAD local-app/public | tar -x -C "$stage_dir" --strip-components=2
printf '{\n  "version": "%s",\n  "builtAt": "%s"\n}\n' "$short_sha" "$built_at" > "$stage_dir/version.json"

# .vercel 是本機登入／專案連結設定，不納入 Git；若已 link 過才複製它。
if [ -d "$script_dir/public/.vercel" ]; then
  cp -R "$script_dir/public/.vercel" "$stage_dir/.vercel"
fi

cd "$stage_dir"
vercel deploy --prod --scope boss-fus-projects --yes
