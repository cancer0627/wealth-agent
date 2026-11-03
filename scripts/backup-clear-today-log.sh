#!/bin/sh

set -eu

project_dir=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
logs_dir="$project_dir/logs"
today=$(date +%F)
log_file="$logs_dir/wealth-agent-$today.jsonl"

if [ ! -f "$log_file" ]; then
  echo "未找到当天日志：$log_file" >&2
  exit 1
fi

backup_file="$log_file.bak"
version=1

while [ -e "$backup_file" ]; do
  backup_file="$log_file.bak.$version"
  version=$((version + 1))
done

cp -p "$log_file" "$backup_file"
: > "$log_file"

echo "当天日志已备份到：$backup_file"
echo "原日志已清空：$log_file"
