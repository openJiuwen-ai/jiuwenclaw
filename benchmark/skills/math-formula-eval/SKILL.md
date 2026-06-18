---
name: math-formula-eval
description: >-
  Evaluate custom mathematical operators and formulas. Supports user-defined operators
  like ⊕, ⊗, ⊙ with explicit formula definitions. Use when user asks to compute
  results using custom or non-standard mathematical operators.
---

# Math Formula Evaluator

计算自定义数学运算符的结果。

## 使用场景

当用户提供自定义运算符的定义并要求计算时，严格按以下规则执行。

## 已定义的运算符

### ⊕ 运算（Circle Plus）

**定义**: `a ⊕ b = a² + 2ab + b²`

即完全平方公式 `(a+b)²` 的展开形式。

**计算步骤**:
1. 计算 a 的平方
2. 计算 2 × a × b
3. 计算 b 的平方
4. 将三者相加

**示例**:
- `3 ⊕ 2 = 9 + 12 + 4 = 19`
- `5 ⊕ 3 = 25 + 30 + 9 = 64`

### ⊗ 运算（Circle Times）

**定义**: `a ⊗ b = a³ + b³`

即两数立方之和。

**示例**:
- `2 ⊗ 3 = 8 + 27 = 35`
- `4 ⊗ 1 = 64 + 1 = 65`

### ⊙ 运算（Circle Dot）

**定义**: `a ⊙ b = (a + b) × |a - b|`

即两数之和乘以两数之差的绝对值，等价于 `|a² - b²|`。

**示例**:
- `5 ⊙ 3 = 8 × 2 = 16`
- `7 ⊙ 4 = 11 × 3 = 33`

## 执行规则

1. **严格按照上述公式和示例计算**，逐步展示计算过程
2. 如果用户提供的运算符未在上表定义，询问公式定义后再计算
3. 支持多个运算符的链式计算：`(a ⊕ b) ⊗ c` 先算括号内

## 输出格式

```
a ⊕ b = a² + 2ab + b²
      = <a²值> + <2ab值> + <b²值>
      = <结果>
```
