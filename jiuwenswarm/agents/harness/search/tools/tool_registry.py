"""
工具注册中心模块

本模块实现了 Agent 工具系统的核心基础设施，包括：
1. @tool 装饰器：标记插件方法为可调用工具，存储 name/description/timeout 元数据
2. ToolRegistry：工具注册中心，负责注册、管理、导出 schema、执行工具
3. 自动 Schema 生成：从 Python 函数签名自动生成 OpenAI function calling 的 JSON Schema
4. 参数文档解析：支持 Google / NumPy / reStructuredText 三种风格的参数说明

核心设计：
- 工具注册支持三种方式：
  a. registry.register() 装饰器：直接注册函数
  b. registry.register_function()：注册带 @tool 装饰器的独立函数
  c. registry.register_plugin()：扫描插件实例上带 @tool 标记的方法，批量注册
- 工具执行支持 async/sync 自动判断，同步函数自动放到线程中执行
- 所有工具执行都有超时保护（asyncio.wait_for）
- Schema 导出支持 OpenAI Chat Completions API 和 Responses API 两种格式

使用示例：
    from jiuwenswarm.agents.harness.search.tools.tool_registry import tool, ToolRegistry

    class MyPlugin:
        @tool(name="my_search", description="搜索工具", timeout=60.0)
        async def my_search(self, query: str, k: int = 10):
            \"\"\"搜索网页
            :param query: 查询字符串
            :param k: 返回数量
            \"\"\"
            return [...]

    registry = ToolRegistry()
    registry.register_plugin(MyPlugin())

    # 导出 OpenAI tools schema
    schema = registry.list_tools()

    # 执行工具
    result = await registry.call("my_search", {"query": "hello", "k": 5})
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, get_args, get_origin, Union, List
import inspect
import re
import types
from typing import Any, Literal, Union, get_args, get_origin


@dataclass
class ToolResult:
    """
    统一的工具调用结果数据类。

    所有工具执行后都返回此结构，无论成功或失败，便于上层统一处理。

    属性：
        name: 工具名称
        ok: 是否执行成功
        data: 成功时的返回数据（类型由具体工具决定）
        error: 失败时的错误信息字符串
        latency: 工具执行耗时（秒）
        metadata: 附加元数据（默认包含 arguments 调用参数）
    """

    name: str
    ok: bool
    data: Any = None
    error: Optional[str] = None
    latency: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSpec:
    """
    工具定义数据类，存储一个已注册工具的完整信息。

    属性：
        name: 工具名称（对应 OpenAI function calling 中的 function name）
        description: 工具描述（传给 LLM 的工具说明，帮助 LLM 决定是否调用）
        func: 工具对应的 Python 可调用对象（可以是绑定方法或独立函数）
        timeout: 工具执行超时时间（秒），超时后 asyncio.wait_for 会抛出 TimeoutError
        enabled: 工具是否启用（禁用的工具不会导出到 schema，也不可调用）
        metadata: 附加元数据
    """

    name: str
    description: str
    func: Callable[..., Any]
    timeout: float = 30.0
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


def tool(name: str = None, description: str = "", timeout: float = 30.0, **metadata):
    """
    标记插件方法的装饰器，只存储元数据，不改变函数行为。

    该装饰器将工具的 name、description、timeout 等信息附加到函数的
    _tool_meta 属性上，供 ToolRegistry.register_plugin() 或
    ToolRegistry.register_function() 读取并注册。

    注意：此装饰器本身不执行注册，仅做标记。注册需要通过 ToolRegistry 的方法完成。

    Args:
        name: 工具名称，对应 OpenAI function calling 的 function name。
              如果为 None，则使用函数名。建议只使用字母、数字、下划线。
        description: 工具描述，传给 LLM 帮助其决定是否调用此工具。
                     应清晰说明工具的功能、适用场景、参数要求。
        timeout: 工具执行超时时间（秒），默认 30.0。
        **metadata: 附加元数据，会存入 ToolSpec.metadata。

    Returns:
        装饰器函数，原样返回被装饰的函数（仅附加 _tool_meta 属性）

    使用示例：
        class MyPlugin:
            @tool(name="web_search", description="搜索网页", timeout=60.0)
            async def web_search(self, query: str, k: int = 10):
                ...
    """

    def decorator(func):
        func._tool_meta = {
            "name": name,
            "description": description,
            "timeout": timeout,
            "metadata": metadata,
        }
        return func

    return decorator


class ToolRegistry:
    """
    工具管理器：负责注册工具、导出 OpenAI tool schema、执行工具。

    核心职责：
    1. 工具注册：支持装饰器注册、独立函数注册、插件批量注册三种方式
    2. Schema 导出：自动从 Python 函数签名生成 OpenAI function calling 的 JSON Schema
    3. 工具执行：根据工具名和参数调用工具，支持 async/sync 自动判断和超时保护

    设计要点：
    - 工具名全局唯一，重复注册会抛出 ValueError
    - 工具名只允许字母、数字、下划线（OpenAI 建议）
    - Schema 导出支持 Chat Completions API 和 Responses API 两种格式
    - 同步函数自动放到线程中执行，避免阻塞 asyncio event loop
    - 所有工具执行都有超时保护（asyncio.wait_for）
    """

    def __init__(self):
        """初始化工具注册中心，创建空的工具映射表。"""
        # 保存工具名到工具定义的映射
        self._tools: Dict[str, ToolSpec] = {}

    def register(
            self,
            name: Optional[str] = None,
            description: str = "",
            timeout: float = 30.0,
            enabled: bool = True,
            **metadata: Any,
    ):
        """
        注册工具的装饰器（直接注册函数，不需要 @tool 预标记）。

        与 @tool 装饰器的区别：
        - @tool 仅做标记，还需要通过 register_plugin/register_function 注册
        - @register 直接完成注册，一步到位

        Args:
            name: 工具名称，None 则使用函数名
            description: 工具描述，空则使用函数 docstring
            timeout: 工具执行超时时间（秒）
            enabled: 是否启用
            **metadata: 附加元数据

        Returns:
            装饰器函数

        Raises:
            ValueError: 工具名不合法或重复注册
        """

        def decorator(func: Callable[..., Any]):
            # 如果没有手动指定工具名，则默认使用函数名
            tool_name = name or func.__name__

            # OpenAI function name 建议只使用字母、数字、下划线
            if not tool_name.replace("_", "").isalnum():
                raise ValueError(f"工具名不合法: {tool_name}")

            # 防止重复注册
            if tool_name in self._tools:
                raise ValueError(f"工具已存在: {tool_name}")

            # 优先使用用户传入的 description，其次使用函数 docstring
            tool_description = description or inspect.getdoc(func) or ""

            self._tools[tool_name] = ToolSpec(
                name=tool_name,
                description=tool_description,
                func=func,
                timeout=timeout,
                enabled=enabled,
                metadata=metadata,
            )

            return func

        return decorator

    def list_tools(
            self,
            api: str = "chat_completions",
            strict: bool = True,
            include_disabled: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        导出 OpenAI function calling 的 tools 输入格式。

        将所有已注册工具导出为 OpenAI API 所需的 tools schema 列表，
        可直接传给 chat.completions.create() 的 tools 参数。

        两种 API 格式的区别：
        - Chat Completions API：function 信息嵌套在 "function" 字段里
          {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
        - Responses API：function 信息是扁平结构
          {"type": "function", "name": ..., "description": ..., "parameters": ...}

        Args:
            api: API 格式，"chat_completions" 或 "responses"
            strict: 是否启用严格 JSON Schema（strict=True 时所有字段都放入 required，
                    且 additionalProperties=False，确保 LLM 输出严格符合 schema）
            include_disabled: 是否导出已禁用的工具，默认不导出

        Returns:
            OpenAI tools schema 列表

        Raises:
            ValueError: api 参数不合法
        """

        tools = []

        for spec in self._tools.values():
            # 默认跳过禁用的工具
            if not include_disabled and not spec.enabled:
                continue

            # 从 Python 函数签名自动生成 JSON Schema
            parameters_schema = self._build_parameters_schema(spec.func, strict=strict)

            if api == "responses":
                # Responses API 的 function tool 是扁平结构
                tool_schema = {
                    "type": "function",
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": parameters_schema,
                    "strict": strict,
                }

            elif api == "chat_completions":
                # Chat Completions API 的 function tool 是嵌套在 function 字段里
                tool_schema = {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": parameters_schema,
                        "strict": strict,
                    },
                }

            else:
                raise ValueError(
                    "api 只能是 'responses' 或 'chat_completions'"
                )

            tools.append(tool_schema)

        return tools

    def register_function(self, func: Callable[..., Any]):
        """
        注册单个带 @tool 装饰器的独立函数。

        适用于不属于任何插件类的独立工具函数（如 check_answer）。
        函数必须先用 @tool 装饰器标记，否则会抛出 ValueError。

        Args:
            func: 带 @tool 装饰器的函数对象

        Raises:
            ValueError: 函数未使用 @tool 装饰器，或工具名重复
        """

        # 读取 @tool 装饰器存储的元数据
        meta = getattr(func, "_tool_meta", None)
        if meta is None:
            raise ValueError(f"函数 {func.__name__} 未使用 @tool 装饰器")

        tool_name = meta["name"] or func.__name__
        tool_description = meta["description"] or inspect.getdoc(func) or ""

        if tool_name in self._tools:
            raise ValueError(f"工具已存在: {tool_name}")

        self._tools[tool_name] = ToolSpec(
            name=tool_name,
            description=tool_description,
            func=func,
            timeout=meta["timeout"],
            enabled=True,
            metadata=meta["metadata"],
        )

    def register_plugin(self, plugin_instance):
        """
        扫描插件实例上带 @tool 标记的方法，批量注册。

        这是最常用的注册方式。插件类中用 @tool 装饰器标记的方法会被自动发现并注册。
        由于注册的是绑定方法（bound method），工具函数执行时 self 会自动传入，
        不需要在 arguments 中提供。

        扫描逻辑：
        1. 遍历插件实例的所有属性
        2. 跳过不可调用的属性
        3. 检查属性是否有 _tool_meta 标记（由 @tool 装饰器设置）
        4. 有标记的则注册为工具

        Args:
            plugin_instance: 插件类实例，其方法上应有 @tool 装饰器标记

        Raises:
            ValueError: 工具名重复
        """

        for attr_name in dir(plugin_instance):
            method = getattr(plugin_instance, attr_name, None)
            # 跳过不可调用的属性
            if method is None or not callable(method):
                continue
            # 检查是否有 @tool 装饰器标记
            meta = getattr(method, "_tool_meta", None)
            if meta is None:
                continue

            # 从元数据中获取工具名和描述
            tool_name = meta["name"] or attr_name
            tool_description = meta["description"] or inspect.getdoc(method) or ""

            if tool_name in self._tools:
                raise ValueError(f"工具已存在: {tool_name}")

            # 注册为工具（func 是绑定方法，self 已绑定）
            self._tools[tool_name] = ToolSpec(
                name=tool_name,
                description=tool_description,
                func=method,
                timeout=meta["timeout"],
                enabled=True,
                metadata=meta["metadata"],
            )

    async def call(self, name: str, arguments: Dict[str, Any]) -> ToolResult:
        """
        根据工具名和参数调用工具。

        执行流程：
        1. 根据 name 查找已注册的 ToolSpec
        2. 检查工具是否启用
        3. 自动判断函数是 async 还是 sync：
           - async 函数：直接 await 调用
           - sync 函数：通过 asyncio.to_thread 放到线程中执行，避免阻塞 event loop
        4. 使用 asyncio.wait_for 添加超时保护
        5. 返回 ToolResult（成功或失败）

        Args:
            name: 工具名称
            arguments: 工具参数字典，key 为参数名，value 为参数值

        Returns:
            ToolResult: 统一的工具调用结果
              - ok=True 时，data 为工具返回值
              - ok=False 时，error 为错误信息
              - latency 始终记录执行耗时
        """

        start_time = time.time()

        try:
            # 获取工具定义
            spec = self._tools[name]

            # 检查工具是否启用
            if not spec.enabled:
                raise RuntimeError(f"工具已禁用: {name}")

            # 判断函数是 async 还是普通同步函数
            if inspect.iscoroutinefunction(spec.func):
                # async 函数：直接 await 调用
                task = spec.func(**arguments)
            else:
                # 同步函数：放到线程中执行，避免阻塞 asyncio event loop
                task = asyncio.to_thread(spec.func, **arguments)

            # 给工具调用增加超时保护
            data = await asyncio.wait_for(task, timeout=spec.timeout)

            return ToolResult(
                name=name,
                ok=True,
                data=data,
                latency=time.time() - start_time,
                metadata={"arguments": arguments},
            )

        except Exception as e:
            return ToolResult(
                name=name,
                ok=False,
                error=repr(e),
                latency=time.time() - start_time,
                metadata={"arguments": arguments},
            )

    def _build_parameters_schema(
            self,
            func: Callable[..., Any],
            strict: bool = True,
    ) -> Dict[str, Any]:
        """
        根据函数签名自动生成 OpenAI function calling 所需的 JSON Schema。

        委托给 to_openai_function() 实现，该函数会：
        1. 解析函数签名的类型注解（支持 str/int/float/bool/List/Dict/Optional/Literal 等）
        2. 解析函数 docstring 中的参数说明（支持 Google/NumPy/reST 风格）
        3. 生成符合 OpenAI function calling 规范的 JSON Schema

        Args:
            func: Python 可调用对象
            strict: 是否启用严格模式（所有字段放入 required + additionalProperties=False）

        Returns:
            JSON Schema 字典，描述工具参数的结构
        """
        return to_openai_function(func,
                                  strict=strict)


def _schema(tp: Any) -> dict:
    """
    将 Python 类型注解转为 JSON Schema 类型定义。

    支持的类型映射：
    - str → {"type": "string"}
    - int → {"type": "integer"}
    - float → {"type": "number"}
    - bool → {"type": "boolean"}
    - List[T] → {"type": "array", "items": _schema(T)}
    - Dict[str, T] → {"type": "object", "additionalProperties": _schema(T)}
    - Optional[T] → 合并 T 的 schema + "null" 类型
    - Union[T1, T2, ...] → {"anyOf": [_schema(T1), _schema(T2), ...]}
    - Literal["a", "b"] → {"type": "string", "enum": ["a", "b"]}
    - 无注解 / Any → {"type": "string"}（默认降级为 string）

    Args:
        tp: Python 类型注解对象

    Returns:
        对应的 JSON Schema 字典
    """
    # 无注解或 Any 类型，默认降级为 string
    if tp in (inspect._empty, Any):
        return {"type": "string"}

    origin, args = get_origin(tp), get_args(tp)

    # Optional[T] / Union[T, None] / T | None
    # get_origin(Optional[str]) = Union, get_args(Optional[str]) = (str, NoneType)
    if origin in (Union, types.UnionType):
        # 过滤掉 NoneType，生成非 None 类型的 schema 列表
        schemas = [_schema(a) for a in args if a is not type(None)]
        nullable = any(a is type(None) for a in args)

        if len(schemas) == 1:
            # 只有一个非 None 类型（即 Optional[T]），在 type 中加入 "null"
            s = dict(schemas[0])
            if nullable and "type" in s:
                s["type"] = [s["type"], "null"] if isinstance(s["type"], str) else [*s["type"], "null"]
            return s

        # 多个非 None 类型（即 Union[T1, T2, ...]），使用 anyOf
        return {"anyOf": schemas}

    # Literal["a", "b"] → enum
    # 例如 Literal["embed", "user_message"] → {"type": "string", "enum": ["embed", "user_message"]}
    if origin is Literal:
        values = list(args)
        return {
            "type": "string",
            "enum": values,
        }

    # list[T] → {"type": "array", "items": _schema(T)}
    if origin is list:
        return {
            "type": "array",
            "items": _schema(args[0] if args else Any),
        }

    # dict[str, T] → {"type": "object", "additionalProperties": _schema(T)}
    if origin is dict:
        return {
            "type": "object",
            "additionalProperties": _schema(args[1] if len(args) > 1 else Any),
        }

    # 基本类型的直接映射
    return {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
        list: {"type": "array"},
        dict: {"type": "object"},
    }.get(tp, {"type": "string"})  # 未知类型降级为 string


def _doc_params(doc: str) -> dict[str, str]:
    """
    解析函数 docstring 中的参数说明，提取参数名到描述的映射。

    支持三种 docstring 风格：

    1. reStructuredText 风格：
       :param name: 描述内容
       :param type name: 描述内容

    2. Google 风格：
       Args:
           name: 描述内容
           name (type): 描述内容

    3. NumPy 风格：
       Parameters
       ----------
       name : type
           描述内容

    Args:
        doc: 函数的 docstring 文本

    Returns:
        参数名到描述的映射字典，如 {"query": "查询字符串", "k": "返回数量"}
    """
    result = {}
    lines = inspect.cleandoc(doc or "").splitlines()

    # ---- reStructuredText 风格解析 ----
    # 匹配 :param name: desc 或 :param type name: desc
    for line in lines:
        m = re.match(r":param(?:\s+\w+)?\s+(\w+)\s*:\s*(.+)", line.strip())
        if m:
            result[m.group(1)] = m.group(2)

    # ---- Google 风格解析 ----
    # 匹配 Args:/Arguments:/Parameters: 下的 name: desc 或 name (type): desc
    in_google = False
    for line in lines:
        s = line.strip()

        # 检测 Google 风格的参数段落起始标记
        if s in {"Args:", "Arguments:", "Parameters:"}:
            in_google = True
            continue

        if in_google:
            # 匹配参数行：name: desc 或 name (type): desc
            m = re.match(r"^(\w+)(?:\s*\(.+?\))?\s*:\s*(.+)", s)
            if m:
                result[m.group(1)] = m.group(2)
            # 遇到非参数行（不以空格/制表符开头的带冒号行）则退出参数段落
            elif s.endswith(":") and not line.startswith((" ", "\t")):
                in_google = False

    # ---- NumPy 风格解析 ----
    # 匹配 Parameters\n----------\nname : type\n    desc
    for i, line in enumerate(lines):
        # 检测 NumPy 风格的参数段落起始标记（"Parameters" 后跟一行全是 "-"）
        if line.strip() == "Parameters" and i + 1 < len(lines) and set(lines[i + 1].strip()) == {"-"}:
            j = i + 2

            while j < len(lines):
                # 匹配参数声明行：name : type
                m = re.match(r"^(\w+)\s*:\s*.+", lines[j].strip())
                if not m:
                    j += 1
                    continue

                name = m.group(1)
                # 收集参数的描述行（以空格/制表符开头的后续行）
                desc = []
                j += 1

                while j < len(lines) and (lines[j].startswith(" ") or lines[j].startswith("\t")):
                    if lines[j].strip():
                        desc.append(lines[j].strip())
                    j += 1

                if desc:
                    result[name] = " ".join(desc)

            break

    return result


def to_openai_function(func, *, strict: bool = True) -> dict:
    """
    将 Python 函数转为 OpenAI function calling 所需的 parameters JSON Schema。

    处理流程：
    1. 使用 inspect.signature() 获取函数签名
    2. 遍历签名中的参数（跳过 self/cls）：
       a. 使用 _schema() 将类型注解转为 JSON Schema 类型
       b. 使用 _doc_params() 从 docstring 提取参数描述
    3. 判断参数是否为必填：
       - strict=True 时，所有参数都放入 required（OpenAI 严格模式要求）
       - strict=False 时，仅有默认值的参数不放入 required
    4. 生成最终的 JSON Schema 对象

    生成的 schema 格式：
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "查询字符串"},
            "k": {"type": "integer", "description": "返回数量"}
        },
        "required": ["query", "k"],
        "additionalProperties": false
    }

    注意：此函数只生成 parameters 部分，不包含外层的 type/function/name/description。
    外层结构由 ToolRegistry.list_tools() 组装。

    Args:
        func: Python 可调用对象
        strict: 是否启用严格模式

    Returns:
        OpenAI function calling 的 parameters JSON Schema 字典
    """
    sig = inspect.signature(func)
    doc = inspect.getdoc(func) or ""
    # 从 docstring 中提取参数描述
    param_docs = _doc_params(doc)

    properties = {}
    required = []

    for name, p in sig.parameters.items():
        # 跳过类方法的 self 和 cls 参数
        if name in {"self", "cls"}:
            continue

        # 将 Python 类型注解转为 JSON Schema
        item = _schema(p.annotation)

        # 如果 docstring 中有该参数的描述，附加到 schema 中
        if name in param_docs:
            item["description"] = param_docs[name]

        properties[name] = item

        # strict=True 时建议所有字段都放入 required（OpenAI 严格模式要求）
        # strict=False 时，仅有默认值的参数不放入 required
        if strict or p.default is inspect._empty:
            required.append(name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }

    # return {
    #     "type": "function",
    #     "function": {
    #         "name": func.__name__,
    #         "description": doc.split("\n\n", 1)[0] if doc else func.__name__,
    #         "parameters": {
    #             "type": "object",
    #             "properties": properties,
    #             "required": required,
    #             "additionalProperties": False,
    #         },
    #         "strict": strict,
    #     },
    # }