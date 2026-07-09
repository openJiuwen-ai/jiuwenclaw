# Python 开发规范与示例

> 本文档是 [principles.md](principles.md) 的语言落地补充，用于说明 Python 项目中的常见规范、约定和代码示例。

## 1. 编码风格 (Coding Style)

- **PEP 8 标准**：严格遵守 [PEP 8](https://peps.python.org/pep-0008/)。
- **缩进**：统一使用 4 个空格，绝不混用 Tab。
- **行宽**：建议单行不超过 88 个字符，兼容 `black` 默认格式。
- **空行**：顶级函数和类之间用两个空行；类方法之间用一个空行。
- **导入规范**：
  - 所有 `import` 放在文件顶部（模块文档字符串之后，全局变量之前）。
  - 导入顺序为：标准库、第三方库、本地模块，组间空一行。
  - 禁止 `from module import *`。

## 2. 命名规范 (Naming Conventions)

- **变量名与函数名**：使用 `snake_case`，如 `user_name`、`calculate_total()`。
- **类名**：使用 `PascalCase`，如 `DatabaseConnection`。
- **常量名**：使用 `UPPER_CASE_WITH_UNDERSCORES`，如 `MAX_RETRIES`。
- **私有成员**：内部方法或属性使用前导下划线，如 `_load_cache()`。
- **语义化**：避免 `tmp`、`data`、`a`、`b` 这类弱语义命名，除非语境极短且含义明确。

## 3. 类型注解 (Type Hinting)

- 为函数参数和返回值补充类型注解。
- 优先使用 Python 3.9+ 内置泛型，如 `list[str]`、`dict[str, int]`。
- 对可能为空的返回值显式表达可空语义。

示例：

```python
from typing import Any


def process_user_data(user_id: int, payload: dict[str, Any]) -> str | None:
    ...
```

## 4. 文档与注释 (Documentation & Comments)

- 模块、类、公开方法和复杂函数应包含 Docstring。
- 建议统一使用 Google Style Docstring。
- 注释优先解释“为什么”，不要逐行复述“做了什么”。

示例：

```python
def calculate_total(items: list[int]) -> int:
    """计算总和。

    Args:
        items: 待求和的整数列表。

    Returns:
        所有元素之和。
    """
    return sum(items)
```

## 5. 异常处理 (Exception Handling)

- 禁止裸 `except:`。
- 谨慎使用 `except Exception:`，除非位于明确的边界层，且伴随日志记录和再次抛出。
- 对文件、连接、锁等资源优先使用上下文管理器。

示例：

```python
from pathlib import Path


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"文件不存在: {path}") from exc
```

## 6. 日志记录 (Logging)

- 生产代码中不使用 `print()` 充当日志。
- 优先使用标准 `logging` 或项目统一日志方案。
- 避免在日志中泄露敏感信息。

示例：

```python
import logging

logger = logging.getLogger(__name__)


def sync_order(order_id: str) -> None:
    logger.info("start syncing order", extra={"order_id": order_id})
```

## 7. 安全与配置 (Security & Configuration)

- 敏感配置从环境变量或安全配置源读取。
- 调用系统命令时避免 `shell=True`。
- 数据库查询、模板渲染、路径处理等高风险场景使用安全方式。

示例：

```python
import os
import subprocess


API_TOKEN = os.environ["API_TOKEN"]


def list_directory(path: str) -> None:
    subprocess.run(["ls", path], check=True)
```

## 8. 性能建议 (Performance Tips)

- 大量字符串拼接优先使用 `''.join(...)` 或 f-string。
- 流式处理大数据时优先生成器，而不是一次性构造大列表。
- 对热点路径避免重复计算、重复 I/O 和无意义对象创建。

## 9. 常见质量工具 (Quality Tooling)

- **格式化**：`black`
- **Lint**：`ruff`
- **类型检查**：`mypy` 或 `pyright`
- **测试**：`pytest`
