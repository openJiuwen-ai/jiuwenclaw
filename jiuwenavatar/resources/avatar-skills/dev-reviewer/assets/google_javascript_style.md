# Google JavaScript Style Guide — 审查参考

> **官方来源**：[Google JavaScript Style Guide](https://google.github.io/styleguide/jsguide.html)  
> **常用工具**：ESLint、clang-format  
> 本文件为 dev-reviewer `Clean` 维度离线参考；完整细则以官方文档为准。  
> **与 TS 关系**：纯 JS 项目用本文；TypeScript 项目优先 [google_typescript_style.md](google_typescript_style.md)。

---

## 1. 源文件

| 项 | 规则 |
|----|------|
| 文件名 | `lowerCamelCase.js` 或 `snake_case.js`（与项目一致） |
| 编码 | UTF-8 |
| 特殊字符 | 源文件仅 ASCII + Unicode 转义（非 ASCII 字面量谨慎） |

---

## 2. 模块系统

### ES Modules（§3.4，现代项目）

```javascript
import {Foo} from './foo.js';
import * as bar from './bar.js';
export {Foo};
export function baz() {}
```

- 同项目内优先相对路径
- Named export 优先；default export 仅框架要求
- 不用 `require` 与 `import` 混用（除非构建链明确支持）

### Closure / goog.module（§3.3，遗留代码）

- `goog.module('my.module');` + `goog.require` — 仅审查已有 Closure 代码时适用

| 审查信号 | 级别 |
|----------|------|
| ESM 与 CommonJS 无理由混用 | Should Fix |
| 循环依赖 | Should Fix |

---

## 3. 格式（§4）

| 项 | 规则 |
|----|-----|
| 缩进 | **2 空格**，不用 tab |
| 列宽 | **80** |
| 大括号 | K&R；`else` 与 `}` 同行 |
| 分号 | **必须**；不依赖 ASI |
| 空白 | 二元运算符两侧空格；`,` 后空格 |
| 括号 | 分组括号推荐使用 |

```javascript
// Good
if (foo) {
  bar();
} else {
  baz();
}

// Bad: 依赖 ASI
if (foo)
  bar()
```

| 审查信号 | 级别 |
|----------|------|
| 缺分号 / 依赖 ASI | Must Fix |
| tab 缩进 | Should Fix |

---

## 4. 命名（§6）

| 类型 | 风格 |
|------|------|
| 类 / 接口 / typedef | `UpperCamelCase` |
| 方法 | `lowerCamelCase` |
| 枚举 | 类型 `UpperCamelCase`；值 `CONSTANT_CASE` |
| 常量 | `CONSTANT_CASE`（真正不可变、模块级） |
| 非常量字段 | `lowerCamelCase`；私有可选尾 `_` |
| 参数 / 局部变量 | `lowerCamelCase` |
| 包名 | `lowerCamelCase` |

### 原则

- 描述性名称；禁止歧义缩写、匈牙利命名
- 10 行以内局部变量可用短名
- 公共方法**不用**单字符参数名
- 测试方法：`testFeature_expectedBehavior` 或 `test_feature_expected_behavior`

### 常量定义

仅当**深度不可变**且模块级/静态 `@const` 时用 `CONSTANT_CASE`；可重新赋值的 `let` 仍用 camelCase。

| 审查信号 | 级别 |
|----------|------|
| 误导性命名 | Must Fix |
| 可变变量用 CONSTANT_CASE | Should Fix |

---

## 5. 语言特性（§5）

### 变量

- `const` 默认；需重新赋值 `let`；**不用** `var`

### 数组与对象

- 尾逗号：多行最后一项加逗号
- 对象：键不加引号（除非非标识符）；不用 `Object.create(null)` 除非需要

### 类（§5.4）

- 不用 ES6 class field 语法混用多种风格（按项目基线）
- 文档化 public API；`@private` 标记内部方法

### 函数（§5.5）

- 箭头函数用于回调；命名 `function` 用于方法/ hoisting
- 默认参数不用 `undefined` 触发 side effect

### 字符串（§5.6）

- 单引号优先；模板字符串用于插值
- 不用 `+` 拼接多行 HTML/长字符串（用模板或数组 join）

### 相等（§5.10）

- **始终** `===` / `!==`；不用 `==`

### 控制结构（§5.8）

- 所有分支带大括号
- `for-in` 须 `hasOwnProperty` 或 `Object.keys`
- `switch` 须有 `default`

---

## 6. 禁项（§5.11）

生产代码 **Must Fix**：

| 禁项 | 说明 |
|------|------|
| `eval` / `Function(string)` | 安全；CSP 不兼容 |
| `with` | strict mode 禁止 |
| 修改 builtin prototype | 全局污染 |
| 非标准 ECMAScript | 未标准化特性 |
| 依赖 ASI | 必须显式 `;` |

---

## 7. JSDoc（§7）

- 所有 **export / public** API 须有 JSDoc
- 形式：

```javascript
/**
 * Sends a message to the server.
 * @param {string} message The message body.
 * @return {!Promise<void>}
 */
function sendMessage(message) {
  ...
}
```

- `@param` / `@return` / `@throws` 准确
- `@override` 用于重写
- 注释增加信息，不复述代码
- 支持 Markdown；长行按 80 列 wrap

| 审查信号 | 级别 |
|----------|------|
| public API 无 JSDoc | Should Fix |
| JSDoc 类型与实现不符 | Must Fix |

---

## 8. 策略（§8）

| 原则 | 说明 |
|------|------|
| Consistency | 规范未覆盖处与**周围代码**一致 |
| Compiler warnings | 零 warning；suppress 须注释 |
| Deprecation | 用 `@deprecated` 标记 |
| Generated code | 生成代码大多豁免 |
| Local style | 项目可有局部补充规则 |

---

## 9. 与 TypeScript 的差异

| 项 | JavaScript (jsguide) | TypeScript (tsguide) |
|----|----------------------|----------------------|
| 类型 | JSDoc 类型注解 | TS 类型系统 |
| 私有 | `@private` / 尾 `_` | 不用 `#` / `_` 前缀 |
| 文件名 | 多种惯例 | `snake_case.ts` |
| 工具 | ESLint + clang-format | gts + tsc strict |

审查 `.ts/.tsx` 时以 **google_typescript_style.md** 为准；审查纯 `.js/.jsx` 以本文为准。

---

## 审查快速对照

```markdown
- [ ] 2 空格缩进、80 列、显式分号
- [ ] const/let；不用 var；=== 比较
- [ ] UpperCamelCase / lowerCamelCase / CONSTANT_CASE
- [ ] 无 eval / with / debugger；不修改 builtin
- [ ] ESM import/export 一致
- [ ] public API 有 JSDoc
- [ ] 与邻文件 / ESLint 配置一致
```
