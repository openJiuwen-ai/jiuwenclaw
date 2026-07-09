# 代码编写审查指引（clean）

> 审查 diff 的**编写层面**：命名、格式、import、类型注解、docstring、语言惯用法与 linter 合规。  
> **不审**分层、模块边界、接口契约、事务设计等「怎么设计系统」—— 见 [code.md](code.md)。

## 与 Code 的分界

| 归 **Clean**（代码编写） | 归 **Code**（架构 / 设计） |
|-------------------------|---------------------------|
| 是否符合 Google / 项目 style guide | 是否该放在这一层、这一模块 |
| 函数是否 ~40 行（风格建议） | 函数是否混合互斥业务（设计问题） |
| `any`、mutable 默认参数、缺 Javadoc | API 语义错误、破坏兼容性 |
| import 顺序、wildcard、相对路径 | 循环依赖、跨层调用 |
| formatter/linter/CI 风格门禁 | 幂等、事务、并发模型 |

**同一段代码可能两个维度都有发现**：分开写 finding，各标对应 `dimension`。

对照 **assets/** 下 Google 规范参考（[google_style_index.md](../../assets/google_style_index.md)）评估 **Python / TS·JS / Java** 的编写规范。**不依赖** `doc/<module>/` 文档。

## 适用时机

- diff 含 `.py`、`.ts`、`.tsx`、`.js`、`.jsx`、`.java` 文件
- 返工轮：核对风格修复是否引入行为漂移
- 项目已有 formatter/linter（Black、Pyink、ESLint、gts、Prettier、google-java-format）时：以 **Google 规范 + 项目既有配置** 为准；冲突时项目配置优先，但须在 finding 中说明

## 参考文档（assets/）

| 语言 | 本地参考 | 官方来源 |
|------|----------|----------|
| Python | [assets/google_python_style.md](../../assets/google_python_style.md) | [pyguide.html](https://google.github.io/styleguide/pyguide.html) |
| TypeScript | [assets/google_typescript_style.md](../../assets/google_typescript_style.md) | [tsguide.html](https://google.github.io/styleguide/tsguide.html) |
| JavaScript | [assets/google_javascript_style.md](../../assets/google_javascript_style.md) | [jsguide.html](https://google.github.io/styleguide/jsguide.html) |
| Java | [assets/google_java_style.md](../../assets/google_java_style.md) | [javaguide.html](https://google.github.io/styleguide/javaguide.html) |
| 索引 | [assets/google_style_index.md](../../assets/google_style_index.md) | — |

审查时**先读 assets 参考**；细则冲突以官方文档为准。

## 审查流程

```
1. 识别 diff 中的 Python / TS / JS / Java 文件
2. 按语言套用下方检查表（命名 → 导入 → 类型 → 格式 → 禁项）
3. 与仓库既有风格对比：新代码不应无故引入第二套风格
4. 分级：Must Fix（可读性/维护性实质受损或违反禁项）/ Should Fix / Nice to Have
```

---

## Python（详见 [google_python_style.md](../../assets/google_python_style.md)）

### Lint 与格式

| 检查项 | Must Fix | Should Fix |
|--------|----------|------------|
| 静态检查 | 明显 pylint 可捕获错误未修（未使用变量、赋值前使用等） | 可安全 suppress 的 warning 无注释说明 |
| 行宽 | — | 超 80 字符且可读性受损（项目用 Black 时以 formatter 为准） |
| 缩进 | 混用 tab/space 或错误缩进 | — |

运行 `pylint`；suppress 须用 `# pylint: disable=…` 并**注释原因**（见 §2.1）。

### 导入（§2.2、§3.13）

- **仅**对 package/module 使用 `import`，不对单个 type/class/function 直接 `from x import y`（`typing` / `collections.abc` / `typing_extensions` 豁免）
- **禁止**相对导入；同包也用完整包名
- 导入顺序：未来导入 → 标准库 → 第三方 → 本地；组间空行
- 可用 `from x import y as z` 解决命名冲突或过长名称

### 命名（§3.16）

| 类型 | 约定 |
|------|------|
| 模块/包 | `lower_with_under.py`；文件名**不得**含 `-` |
| 类/异常 | `CapWords` |
| 函数/方法/变量 | `lower_with_under` |
| 常量 | `CAPS_WITH_UNDER` |
| 保护成员 | 单前导 `_`；**避免**双下划线 name mangling |

- 名称须**描述性**；避免歧义缩写、匈牙利命名、`id_to_name_dict` 式冗余类型后缀
- 单字符名仅限短作用域（循环 `i/j/k`、异常 `e`、文件句柄 `f` 等）

### 语言规则（§2）

| 规则 | 审查要点 |
|------|----------|
| 异常（§2.4） | 用内置/项目异常；`assert` 仅用于不变量，不用于业务控制流 |
| 可变全局（§2.5） | 避免模块级可变状态 |
| 推导式（§2.7） | 简单场景可用；**禁止**多层嵌套推导 |
| 默认参数（§2.12） | **禁止**可变对象作默认值（`[]`、`{}`） |
| True/False（§2.14） | 用 `if foo:` 而非 `if foo == True:`；空容器用 `if items:` |
| 函数长度（§3.18） | 超 ~40 行考虑拆分（非硬限，但 Should Fix 信号） |
| 类型注解（§2.21、§3.19） | 公共 API 应有类型；熟悉 `Optional`、`Union`、泛型写法 |

### 注释与 docstring（§3.8）

- 公共模块/类/函数须有 docstring（Google 格式）
- 注释解释**为何**，非复述代码
- TODO 格式：`# TODO: buglink - 说明` 或带 `@username`

### 资源与 main（§3.11、§3.17）

- 文件/连接用 context manager（`with`）
- 可执行脚本：`main()` + `if __name__ == '__main__':`

---

## TypeScript / JavaScript（详见 [google_typescript_style.md](../../assets/google_typescript_style.md)、[google_javascript_style.md](../../assets/google_javascript_style.md)）

### 导入与导出

| 检查项 | Must Fix | Should Fix |
|--------|----------|------------|
| 路径 | — | 同项目内过度 `../../../` |
| 风格 | 混用三种以上 import 风格且无理由 | 大 API 应用 namespace import（`import * as foo`）而非超长 destructuring |
| 类型导入 | — | 值与类型混 import 时应用 `import type` / `export type`（TS） |

- 同逻辑项目内优先**相对路径** `./foo`
- 命名 import 用于高频符号；namespace import 用于大 API 多符号

### 命名（tsguide §Naming / jsguide §6）

| 类型 | 约定 |
|------|------|
| 类/接口/类型/枚举 | `UpperCamelCase` |
| 变量/参数/函数/方法/属性 | `lowerCamelCase` |
| 模块级常量/枚举值 | `CONSTANT_CASE` |
| 文件名 | `snake_case.ts` / `snake_case.js` |

- 名称须描述性；**禁止**歧义缩写、`customerID`（应为 `customerId`）、匈牙利命名
- TS：**禁止** `_` 作前缀/后缀标识符；**禁止** `#` 私有字段（规范要求）
- 缩写作整词：`loadHttpUrl` 非 `loadHTTPURL`（平台 API 名除外）
- 10 行以内局部变量可用短名

### 类型系统（TS 专篇）

| 检查项 | Must Fix | Should Fix |
|--------|----------|------------|
| `any` | 公共 API 无理由使用 `any` | 内部临时 `any` 无 TODO |
| 空值 | — | 应区分 `undefined` / `null` 时混用 |
| 结构 | — | 可表达为 interface 时仍用 type alias |
| 推断 | — | 复杂表达式缺必要注解导致可读性下降 |

- 优先 **interface** 于 type literal alias（对象形状）
- 使用结构类型；避免多余 wrapper（`String`/`Boolean`/`Number` **禁止** `new`）
- **禁止** `const enum`；用普通 `enum`
- 启用 strict 模式（gts / `tsconfig-google.json` 基线）

### 语言禁项（Disallowed features）

Must Fix 若出现在生产代码路径：

- `eval`、`Function('…')` 构造器（loader 除外）
- `with`
- `debugger` 语句
- 修改内置对象 prototype
- 依赖 ASI（**必须**显式分号，jsguide §5）
- 非标准/未标准化 ECMAScript 特性（除非项目明确 target 该运行时）

### 格式（jsguide §4 / gts）

- 缩进 **2 空格**；列宽 **80**（Prettier/gts 默认）
- 大括号：K&R 风格；`else` 与 closing brace 同行
- 字符串：单引号（gts Prettier 默认）除非避免转义

### 注释与 JSDoc

- 导出符号须有 JSDoc / TSDoc
- 注释须**增加信息**，非复述签名
- `@override` 可替代重复 docstring（子类 trivial override）

---

## Java（详见 [google_java_style.md](../../assets/google_java_style.md)）

- 2 空格缩进、100 列、K&R 大括号；`if/for/while` 必须带 `{}`
- 禁止 wildcard import；static / non-static 分组、ASCII 名称序
- 命名：`UpperCamelCase` / `lowerCamelCase` / `UPPER_SNAKE_CASE`（真常量）
- `@Override`；空 catch 须注释；static 成员用类名限定
- switch 须 exhaustive；switch expression 用 `->`
- public/protected API 须有 Javadoc

---

## 与仓库既有风格的关系

1. **先读项目配置**：`pyproject.toml`、`setup.cfg`、`.eslintrc*`、`eslint.config.*`、`tsconfig.json`、`.prettierrc*`、`checkstyle.xml`、`spotless` / google-java-format 配置
2. 项目配置与 Google 规范冲突 → **以项目为准**，Nice to Have 可建议对齐 Google
3. 新文件不应引入与邻文件矛盾的命名/导入/引号风格 → Should Fix

## 与 `review/result.json` 的映射

| 情形 | `category` | `dimension` |
|------|------------|-------------|
| 命名/导入/格式违规 | `maintainability` | `Clean` |
| 类型安全（TS `any`、Python 缺公共 API 注解） | `maintainability` | `Clean` |
| 禁项（eval、可变默认参数等） | `correctness` 或 `maintainability` | `Clean` |
| 与项目 linter 冲突且 CI 会失败 | `maintainability` | `Clean` |

`security_review` / `performance` **不替代**本维度的风格判断。

## 快速清单

```markdown
- [ ] Python：导入顺序与 pyguide 一致；无相对 import；无 mutable 默认参数
- [ ] Python：命名符合 lower_with_under / CapWords / CAPS_WITH_UNDER
- [ ] Python：公共 API 有 docstring 与合理类型注解
- [ ] TS/JS：UpperCamelCase / lowerCamelCase / CONSTANT_CASE 一致
- [ ] TS/JS：无 any/eval/debugger/with；显式分号；无 primitive wrapper
- [ ] TS/JS：import 路径与 export 风格与邻文件一致
- [ ] Java：2 空格/100 列；无 wildcard import；@Override；Javadoc
- [ ] 新代码与项目 formatter/linter 配置兼容
- [ ] 风格问题已分级，未将纯偏好升格为 Must Fix
```

## 分级原则

| 级别 | 典型 |
|------|------|
| Must Fix | 禁项、mutable 默认参数、生产路径 `debugger`/`eval`、CI lint 必失败 |
| Should Fix | 命名不清晰、函数过长、缺公共 docstring、import 混乱 |
| Nice to Have | 与 Google 一致但项目未强制；纯格式（可由 `gts fix` / Black 自动修） |

**禁止**因「不符合个人偏好但与项目及 Google 均可接受」而阻塞 PASS。

## 信息不足时

- 无法判断项目 linter 基线 → `limitations` 注明，保守 Should Fix
- diff 仅改非 Py/TS/JS/Java 文件 → 本维度标 `not_applicable`，不强行凑项
