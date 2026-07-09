#!/usr/bin/env bash
# 输出最近提交与关键配置（在业务仓根目录执行：cd <repo> && bash path/to/collect_project_context.sh）
# 关键文件列表与 code_review_runner.collect_project_context() 对齐（各取前 200 行）。
set -uo pipefail

printf '\n## recent commits\n\n'
git log -n 10 --oneline

files=(
  pyproject.toml
  requirements.txt
  package.json
  tsconfig.json
  pom.xml
  build.gradle
  build.gradle.kts
  go.mod
  Cargo.toml
  Makefile
  application.yml
  application.yaml
  application.properties
)
for f in "${files[@]}"; do
  if [[ -f "$f" ]]; then
    printf '\n## %s (head)\n\n' "$f"
    head -n 200 "$f"
  fi
done
