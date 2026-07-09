# Google 编码规范 — 参考索引

dev-reviewer **`Clean`** 维度（**代码编写**）使用的离线规范参考。架构/分层类问题见 [../references/dimensions/code.md](../references/dimensions/code.md)。

| 语言 | 本地参考 | 官方来源 | 推荐工具 |
|------|----------|----------|----------|
| Python | [google_python_style.md](google_python_style.md) | [pyguide.html](https://google.github.io/styleguide/pyguide.html) | pylint、[pylintrc](https://google.github.io/styleguide/pylintrc)、Black / Pyink |
| TypeScript | [google_typescript_style.md](google_typescript_style.md) | [tsguide.html](https://google.github.io/styleguide/tsguide.html) | [gts](https://github.com/google/gts) |
| JavaScript | [google_javascript_style.md](google_javascript_style.md) | [jsguide.html](https://google.github.io/styleguide/jsguide.html) | ESLint、clang-format |
| Java | [google_java_style.md](google_java_style.md) | [javaguide.html](https://google.github.io/styleguide/javaguide.html) | google-java-format、Checkstyle、Error Prone |

## 使用方式

1. 从 diff 识别语言 → 打开对应参考文档
2. 按 [../references/dimensions/clean.md](../references/dimensions/clean.md) 流程分级（Must Fix / Should Fix / Nice to Have）
3. 项目已有 formatter/linter 配置时，**项目配置优先**，Google 规范作补充
4. finding 标注 `"dimension": "Clean"`，`category` 通常为 `maintainability`

## 文件选择

```
.py          → google_python_style.md
.ts / .tsx   → google_typescript_style.md
.js / .jsx   → google_javascript_style.md（无 TS 时）
.java        → google_java_style.md
混合语言 diff → 各文件分别套用对应规范
```
