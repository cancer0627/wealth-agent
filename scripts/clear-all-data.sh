#!/bin/sh

set -eu

project_dir=$(CDPATH= cd "$(dirname "$0")/.." && pwd)

cd "$project_dir"

if [ ! -f "data/README.md" ] || [ ! -d "data/backups" ]; then
  echo "拒绝清理：未识别到预期的 data 目录结构。" >&2
  exit 1
fi

find data -mindepth 1 \
  ! -path "data/README.md" \
  ! -path "data/backups" \
  ! -path "data/backups/.gitkeep" \
  -delete

echo "已清空全部运行数据；保留 data/README.md 和 data/backups/.gitkeep。"
