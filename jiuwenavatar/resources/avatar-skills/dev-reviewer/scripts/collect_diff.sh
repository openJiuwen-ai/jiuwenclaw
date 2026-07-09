#!/usr/bin/env bash
# 收集 staged + unstaged diff（在业务仓根目录执行：cd <repo> && bash path/to/collect_diff.sh）
set -uo pipefail

git status -sb

printf '\n## git diff (unstaged)\n\n'
git diff

printf '\n## git diff --staged\n\n'
git diff --staged
