# 输出最近提交与关键配置（与 code_review_runner.collect_project_context() 对齐，各取前 200 行）
"`n## recent commits`n"
git log -n 10 --oneline

$files = @(
  "pyproject.toml",
  "requirements.txt",
  "package.json",
  "tsconfig.json",
  "pom.xml",
  "build.gradle",
  "build.gradle.kts",
  "go.mod",
  "Cargo.toml",
  "Makefile",
  "application.yml",
  "application.yaml",
  "application.properties"
)
foreach ($f in $files) {
  if (Test-Path $f) {
    "`n## $f (head)`n"
    Get-Content $f -TotalCount 200
  }
}
