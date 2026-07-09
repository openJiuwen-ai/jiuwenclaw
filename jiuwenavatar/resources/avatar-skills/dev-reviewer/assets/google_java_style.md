# Google Java Style Guide — 审查参考

> **官方来源**：[Google Java Style Guide](https://google.github.io/styleguide/javaguide.html)  
> **常用工具**：[google-java-format](https://github.com/google/google-java-format)、Checkstyle、Error Prone  
> 本文件为 dev-reviewer `Clean` 维度离线参考；完整细则以官方文档为准。

---

## 1. 源文件

| 项 | 规则 |
|----|------|
| 文件名 | 与顶层 public 类名一致，`CaseSensitive.java` |
| 编码 | UTF-8 |
| 空白 | **仅** ASCII 空格；**禁止** tab 缩进 |
| 结构 | 版权 → package → import → **恰好一个**顶层类；节间空一行 |

---

## 2. 导入（§3.3）

| 规则 | 说明 |
|------|------|
| 禁止 wildcard | 不用 `import java.util.*` |
| 禁止 module import | 不用 `import module java.base;` |
| 禁止换行 | import 行不 wrap（不受 100 列限制） |
| 顺序 | ① 全部 static import → 空行 → ② 全部非 static import |
| 组内排序 | 按 import **名称** ASCII 序（非整行字典序） |
| static 嵌套类 | 用普通 import，不用 static import |

| 审查信号 | 级别 |
|----------|------|
| wildcard import | Should Fix |
| import 顺序/分组错误 | Should Fix |
| 一文件多顶层类 | Must Fix |

---

## 3. 格式（§4）

| 项 | 值 |
|----|-----|
| 缩进 | **2 空格** |
| 列宽 | **100** 字符（package/import/文本块等例外） |
| 大括号 | K&R；`if/for/while/do` **必须**带 `{}`，即使单行 |
| 语句 | 一行一句 |
| 续行 | 至少 +4 空格缩进 |
| 数组 | `String[] args`，非 `String args[]` |
| 变量声明 | 一行一变量（`for` 头除外） |
| long 字面量 | 大写 `L`：`3000000000L` |

### Switch（§4.8.4）

- **必须** exhaustive（含 `default`，即使为空）
- switch **expression** 须 new-style（`->`）
- old-style fall-through 须 `// fall through` 注释
- new-style 无 fall-through

### 注解（§4.8.5）

- 类/包/模块注解：每行一个，在 Javadoc 之后
- 单参数无参注解可与方法签名同行：`@Override public int hashCode()`
- 字段注解：多个可同行

| 审查信号 | 级别 |
|----------|------|
| 单行 if 无大括号 | Must Fix |
| switch 非 exhaustive | Should Fix |
| 空 catch 无注释 | Must Fix |

---

## 4. 命名（§5）

| 类型 | 风格 | 示例 |
|------|------|------|
| 包 / 模块 | 全小写，无下划线 | `com.example.deepspace` |
| 类 / 接口 / record | `UpperCamelCase` | `ImmutableList` |
| 方法 | `lowerCamelCase` | `sendMessage` |
| 常量 | `UPPER_SNAKE_CASE` | 见下方「常量定义」 |
| 非常量字段 | `lowerCamelCase` | `computedValues` |
| 参数 / 局部变量 | `lowerCamelCase` | — |
| 类型参数 | 单大写字母或 `RequestT` | `T`, `E`, `FooBarT` |
| 测试类 | 类名 + `Test` | `HashImplTest` |

### 禁止

- 匈牙利 / 特殊前后缀：`mName`、`s_name`、`kName`、`name_`
- 公共方法单字符参数名

### 常量定义（§5.2.4）

`static final` 且**深度不可变**、方法无副作用 → `UPPER_SNAKE_CASE`。

**不是**常量：`Logger`、可变集合、`String[]` 非空数组、`final` 局部变量。

### CamelCase 规则（§5.3）

- `newCustomerId` 非 `newCustomerID`
- `XmlHttpRequest` 非 `XMLHTTPRequest`
- 缩写按整词处理（平台 API 名除外）

| 审查信号 | 级别 |
|----------|------|
| 包名含大写/下划线 | Should Fix |
| 误导性命名 | Must Fix |
| 可变字段用 CONSTANT_CASE | Should Fix |

---

## 5. 编程实践（§6）

| 规则 | 要点 |
|------|------|
| `@Override` | 合法处**必须**标注（父方法 `@Deprecated` 可省略） |
| 捕获异常 | 禁止空 catch；无操作须注释说明原因 |
| 静态成员 | 用 `Foo.staticMethod()`，非 `instance.staticMethod()` |
| `finalize` | **禁止** override |

```java
// Good: 空 catch 有说明
} catch (NumberFormatException _) {
  // it's not numeric; that's fine, just continue
}

// Bad: 静默吞异常
} catch (IOException e) {}
```

| 审查信号 | 级别 |
|----------|------|
| 空 catch 无注释 | Must Fix |
| 重写方法缺 @Override | Should Fix |
| 通过实例调用 static 方法 | Should Fix |

---

## 6. Javadoc（§7）

### 必须

- 所有 **visible**（public / protected）类、成员、record 组件须有 Javadoc
- 例外：真正自解释的 getter；`@Override` 方法可不重复

### 格式

```java
/**
 * Returns the customer identifier for this order.
 *
 * @param orderId the order to look up
 * @return the customer ID, or empty if not found
 */
public Optional<String> getCustomerId(String orderId) { ... }
```

- 摘要是**名词/动词短语**，非完整句首「This method…」
- 块标签顺序：`@param` → `@return` → `@throws` → `@deprecated`
- 段落间空行（仅 `*` 的行）

| 审查信号 | 级别 |
|----------|------|
| public API 无 Javadoc（非自解释） | Should Fix |
| Javadoc 与签名/行为矛盾 | Must Fix |
| 错误 summary 格式 | Nice to Have |

---

## 7. 类组织（§3.4）

- 每类**逻辑顺序**维护成员（非纯按添加时间堆末尾）
- **同名 overload** 须连续排列，中间不插其它成员
- 局部变量**就近声明**，非块开头一次性声明

---

## 8. TODO 注释（§4.8.6.2）

```
// TODO: crbug.com/12345678 - Remove after 2047q4 compatibility window expires.
```

- `TODO` 全大写 + 冒号 + bug 链接 + `-` 说明
- 避免仅指向个人/团队

---

## 9. 修饰符顺序（§4.8.7）

```
public protected private abstract default static final sealed non-sealed
  transient volatile synchronized native strictfp
```

---

## 10. 与 Spring / 企业 Java 审查衔接

Clean 维度审**风格**；以下若在 diff 中出现且违反 Google 惯例，可标 Clean 或 Code：

| 项 | Clean 信号 |
|----|------------|
| 字段注入 `@Autowired` 于 private 字段 | Should Fix（推荐构造器注入，属 Code/架构） |
| 公开 API 缺 Javadoc | Should Fix |
| Lombok 过度掩盖可读性 | Should Fix（Nice to Have） |

事务、并发、安全见 `code.md` / `security.md`，不单占 Clean。

---

## 审查快速对照

```markdown
- [ ] 2 空格、100 列、K&R 大括号；if/for 必带 {}
- [ ] 无 wildcard import；static / non-static 分组正确
- [ ] UpperCamelCase / lowerCamelCase / UPPER_SNAKE_CASE（真常量）
- [ ] @Override；空 catch 有注释；static 用类名限定
- [ ] switch exhaustive；expression 用 ->
- [ ] public/protected API 有 Javadoc
- [ ] google-java-format / Checkstyle 与 CI 一致
```
