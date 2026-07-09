# Google Python Style Guide — 审查参考

> **官方来源**：[Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)  
> **Lint 配置**：[pylintrc](https://google.github.io/styleguide/pylintrc)  
> **常用工具**：pylint、Black、Pyink  
> 本文件为 dev-reviewer `Clean` 维度离线参考；完整细则以官方文档为准。

---

## 1. Lint（§2.1）

- **必须**对代码运行 `pylint`（使用 Google pylintrc）
- Suppress 使用行级注释，**须说明原因**：

```python
def do_PUT(self):  # WSGI name, so pylint: disable=invalid-name
    ...
```

- 优先 `pylint: disable`，不用已废弃的 `disable-msg`
- 未使用参数：在函数开头 `del arg1, arg2  # Unused.` 并注释；不推荐 `_` 前缀或 `unused_` 前缀

| 审查信号 | 级别 |
|----------|------|
| pylint 可捕获的明显错误未修 | Must Fix |
| suppress 无原因注释 | Should Fix |

---

## 2. 导入（§2.2、§3.13）

### 规则

- `import` **仅**用于 package 和 module，不对单个 type/class/function 直接 import
- **豁免**：`typing`、`collections.abc`、`typing_extensions`
- **禁止**相对导入；同包也用完整包名
- 允许 `from x import y as z`：命名冲突、名称过长、过于泛化时

### 顺序（§3.13）

1. `from __future__ import …`
2. 标准库
3. 第三方库
4. 本地 / 项目模块

组间空一行；组内按字母序。

| 审查信号 | 级别 |
|----------|------|
| 相对 import | Should Fix → Must Fix（若项目已禁止） |
| 导入顺序混乱、循环 import | Should Fix |
| `from module import ClassName`（非豁免模块） | Should Fix |

---

## 3. 命名（§3.16）

| 类型 | 公开 | 内部 |
|------|------|------|
| Package | `lower_with_under` | — |
| Module | `lower_with_under` | `_lower_with_under` |
| Class | `CapWords` | `_CapWords` |
| Exception | `CapWords` | — |
| Function / Method | `lower_with_under()` | `_lower_with_under()` |
| 常量 | `CAPS_WITH_UNDER` | `_CAPS_WITH_UNDER` |
| 变量 | `lower_with_under` | `_lower_with_under` |
| 参数 | `lower_with_under` | — |

### 禁止

- 文件名含 `-`；须 `.py` 扩展名
- 歧义缩写、删字母缩写（`cstmr_id`）
- 匈牙利命名、冗余类型后缀（`id_to_name_dict`）
- 双下划线 name mangling（`__foo`）— 优先单 `_`
- 攻击性词汇

### 允许的单字符名

循环 `i/j/k/v`、异常 `e`、`with` 文件句柄 `f`、无约束 TypeVar `_T`

| 审查信号 | 级别 |
|----------|------|
| 公共 API 命名误导（如 getter 却写库） | Must Fix |
| 模块/类命名不符合约定 | Should Fix |
| 单字符名作用域过大 | Should Fix |

---

## 4. 语言规则（§2）

| 章节 | 要点 |
|------|------|
| §2.3 Packages | 使用绝对路径导入；`__init__.py` 仅做包标识或 namespace |
| §2.4 Exceptions | 内置异常优先；不用 `assert` 做业务控制流 |
| §2.5 Mutable Global | 避免模块级可变全局状态 |
| §2.6 Nested | 嵌套类/函数仅当闭包必要；测试小 helper 可例外 |
| §2.7 Comprehensions | 简单可用；**禁止**多层嵌套推导 |
| §2.8 Iterators | 用 `in` 迭代；不用 `.keys()` 等除非需要 mutating dict |
| §2.10 Lambda | 单行简单表达式；复杂逻辑用 `def` |
| §2.12 Default Args | **禁止** `[]` / `{}` 等可变默认值 |
| §2.14 True/False | `if foo:` 非 `if foo == True:`；空容器用 truthiness |
| §2.18 Threading | 不依赖内置数据原子性；用 `threading` 或 `queue` |
| §2.21 Types | 公共 API 使用 type hints |

| 审查信号 | 级别 |
|----------|------|
| 可变默认参数 | Must Fix |
| 多层嵌套推导 | Should Fix |
| 模块级可变全局 | Should Fix |

---

## 5. 格式（§3）

| 规则 | 值 |
|------|-----|
| 行宽 | 80 字符（Black/Pyink 团队常以 formatter 为准） |
| 缩进 | 4 空格，不用 tab |
| 括号 | 不用反斜杠换行时避免多余括号 |
| 尾随逗号 | 多行序列最后一项加逗号（便于 diff） |
| 分号 | 不用 |

---

## 6. 注释与 Docstring（§3.8）

### Docstring 格式（Google 风格）

```python
def fetch_data(url: str, timeout: int = 30) -> dict[str, Any]:
    """Fetches JSON from the given URL.

    Args:
        url: The endpoint to request.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON as a dictionary.

    Raises:
        requests.HTTPError: If the response status is not 2xx.
    """
```

- 公共 module / class / function **必须**有 docstring
- 注释解释 **why**，不复述 what
- TODO：`# TODO: buglink - 说明` 或 `# TODO(username): …`

| 审查信号 | 级别 |
|----------|------|
| 公共 API 无 docstring | Should Fix |
| 注释与代码矛盾 | Must Fix |

---

## 7. 字符串与日志（§3.10）

- 日志用 `%` 或 f-string，**不用** `+` 拼接日志消息
- 用户可见错误信息清晰、可操作

---

## 8. 资源与 Main（§3.11、§3.17）

```python
def main() -> None:
    ...

if __name__ == '__main__':
    main()
```

- 文件、socket 等用 `with` / context manager
- 模块 import 时**不应**执行副作用（除注册等必要初始化）

---

## 9. 函数长度（§3.18）

- 偏好短小专注函数
- 超 **~40 行** 考虑拆分（非硬限）

---

## 10. 类型注解（§3.19）

- 公共 API **应**注解参数与返回值
- 不必注解 `self` / `cls`（除非需要 `Self` 等）
- 用 `X | None` 或 `Optional[X]`；泛型用 `list[str]`（Py3.9+）
- 仅在必要时 `# type: ignore` 并注释原因

| 审查信号 | 级别 |
|----------|------|
| 公共 API 缺类型注解 | Should Fix |
| 滥用 `Any` 无说明 | Should Fix |

---

## 审查快速对照

```markdown
- [ ] pylint 已跑 / CI lint 通过
- [ ] import 绝对路径、顺序正确
- [ ] 命名：lower_with_under / CapWords / CAPS_WITH_UNDER
- [ ] 无 mutable 默认参数
- [ ] 公共 API 有 docstring + 类型
- [ ] 资源 with 管理；main 守卫
- [ ] 函数 ~40 行以上有拆分理由
```
