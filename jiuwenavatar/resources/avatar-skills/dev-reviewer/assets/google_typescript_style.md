# Google TypeScript Style Guide — 审查参考

> **官方来源**：[Google TypeScript Style Guide](https://google.github.io/styleguide/tsguide.html)  
> **常用工具**：[gts](https://github.com/google/gts)（ESLint + Prettier + `tsconfig-google.json`）  
> 本文件为 dev-reviewer `Clean` 维度离线参考；完整细则以官方文档为准。

---

## 1. 源文件

| 项 | 规则 |
|----|------|
| 编码 | UTF-8 |
| 文件名 | `snake_case.ts` / `snake_case.tsx` |
| 版权 / `@fileoverview` | 按项目要求（Google 内部惯例） |

---

## 2. 导入（Imports）

| 类型 | 示例 | 用途 |
|------|------|------|
| namespace | `import * as foo from './foo';` | 大 API 多符号 |
| named | `import {Bar} from './bar';` | 高频、名称清晰的符号 |
| default | `import Button from 'Button';` | 仅外部库要求时 |
| side-effect | `import './polyfill';` | 副作用初始化 |

### 路径

- 同逻辑项目内优先 **相对路径** `./foo`
- 限制 `../../../` 层级过深
- 值与类型分离时用 `import type` / `export type`

### 选择原则

- 高频符号 → named import（可 `as` 重命名）
- 大 API 多符号 → namespace import，避免超长 destructuring

```typescript
// Bad: 过长 destructuring
import {Item as TableviewItem, Header as TableviewHeader, ...} from './tableview';

// Good
import * as tableview from './tableview';
let item: tableview.Item | undefined;
```

| 审查信号 | 级别 |
|----------|------|
| 混用多种 import 风格无理由 | Should Fix |
| 应用 `import type` 却混 import 值类型 | Should Fix |
| 路径层级过深可读性差 | Should Fix |

---

## 3. 导出（Exports）

- 优先 named export
- 避免 default export（除非框架要求）
- 重新导出：`export {Foo} from './foo';`
- 类型专用：`export type {Foo};`

---

## 4. 命名（Naming）

| 类型 | 风格 |
|------|------|
| 类 / 接口 / 类型 / 枚举 / 装饰器 | `UpperCamelCase` |
| 变量 / 参数 / 函数 / 方法 / 属性 | `lowerCamelCase` |
| 模块级常量 / 枚举值 | `CONSTANT_CASE` |
| namespace import 别名 | `lowerCamelCase`（文件名可为 snake_case） |

### 原则

- 名称描述性；**禁止**歧义缩写、删字母缩写
- **禁止**匈牙利命名（`kSecondsPerDay`）
- `Id` 写 `customerId`，非 `customerID`
- 缩写作整词：`loadHttpUrl` 非 `loadHTTPURL`（平台 API 除外）
- **禁止** `_` 前缀/后缀标识符；**禁止**单独 `_` 表未使用
- **禁止** `#` 私有字段（规范要求）
- 10 行以内局部变量可用短名
- Observable 后缀 `$` 为团队可选约定

### 测试方法名

可结构化：`testX_whenY_doesZ()` 或 `test_feature_expectedBehavior`

| 审查信号 | 级别 |
|----------|------|
| 公共 API 歧义缩写 | Should Fix |
| `customerID` 等错误 camelCase | Should Fix |
| 使用 `#private` 或 `_` 前缀 | Should Fix（除非项目明确例外） |

---

## 5. 类型系统

### 推断与注解

- 优先让 TS 推断简单类型
- 公共 API、复杂表达式**应**显式注解
- 优先 **interface** 于 type literal alias（对象形状）

### 空值

- 明确 `undefined` vs `null` 语义；不混用无约定

### 禁止 / 限制

| 项 | 规则 |
|----|------|
| `any` | 公共 API **禁止**无理由 `any` |
| `{}` | 不用于「任意非 null 对象」；用 `unknown` 或具体类型 |
| Wrapper | **禁止** `new String/Boolean/Number` |
| `const enum` | **禁止**；用普通 `enum` |
| 结构类型 | 优先 structural typing |
| 索引签名 | 谨慎 `{[key: string]: T}` |

### 泛型

- 类型参数单字母 `T` 或 `UpperCamelCase`
- 避免 return-type-only generics 滥用

| 审查信号 | 级别 |
|----------|------|
| 公共 API 返回 `any` | Must Fix |
| 无理由 `as` 断言 | Should Fix |
| 应用 interface 却用 type alias | Nice to Have |

---

## 6. 语言特性

### 变量声明

- 优先 `const`；需重新赋值用 `let`；**不用** `var`

### 类

- 明确可见性；不依赖 `#` 私有
- 参数属性谨慎使用并文档化
- `@override` 标记重写

### 函数

- 优先箭头函数用于回调；命名函数用于 hoisting / 调试栈
- 异步：优先 `async/await`

### 控制结构

- 始终使用大括号（即使单行）
- `switch` 含 `default`；fall-through 须注释
- 不用 `==`；用 `===`

---

## 7. 禁项（Disallowed Features）

生产代码路径 **Must Fix**：

| 禁项 | 原因 |
|------|------|
| `eval` / `Function('…')` | 安全与 CSP |
| `with` | 可读性；strict mode 禁止 |
| `debugger` | 不应进入生产 |
| 修改 builtin prototype | 全局污染 |
| 依赖 ASI | **必须**显式分号 |
| 非标准 / 提案阶段 ECMAScript | 兼容性 |
| `const enum` | 优化特性，破坏 JS 互操作 |

---

## 8. 格式（gts / Prettier 默认）

| 项 | 值 |
|----|-----|
| 缩进 | 2 空格 |
| 列宽 | 80 |
| 引号 | 单引号（除非避免转义） |
| 尾随逗号 | 多行加逗号 |
| 分号 | 必须 |

---

## 9. 注释与 JSDoc

- **所有顶层 export** 须有 JSDoc / TSDoc
- 注释须**增加信息**，不复述签名
- 支持 Markdown 子集
- `@param` / `@returns` / `@throws` 与实现一致
- `@override` 可替代 trivial 重复的 docstring
- 文档放在 decorator **之前**

```typescript
/** Fetches user profile by ID. */
export async function getUser(id: string): Promise<User> {
  ...
}
```

| 审查信号 | 级别 |
|----------|------|
| export 无文档 | Should Fix |
| JSDoc 与签名矛盾 | Must Fix |

---

## 10. 工具链

- TypeScript **strict** 模式（gts `tsconfig-google.json` 基线）
- 运行 `gts lint` / `eslint` / `tsc --noEmit`
- 自动修复：`gts fix`

---

## 审查快速对照

```markdown
- [ ] import/export 风格一致；类型用 import type
- [ ] UpperCamelCase / lowerCamelCase / CONSTANT_CASE
- [ ] 无 any（公共 API）、无 const enum、无 primitive wrapper
- [ ] 无 eval / debugger / with；显式分号
- [ ] strict TS；export 有 JSDoc
- [ ] gts/ESLint/Prettier 与项目 CI 一致
```
