# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Storage Backend Registry - 非入侵式Backend注册机制.

提供混合注册机制：
- 手动注册：Registry.register("my-obs", MyObsBackend)
- 配置驱动：config.yaml 中 backend_class 字段自动加载

用户无需修改 SDK 源码，只需继承 BaseStorageBackend 并配置即可使用自定义 Backend。
"""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jiuwenclaw.storage.backend import BaseStorageBackend


class StorageBackendRegistry:
    """存储后端注册表 - 支持非入侵式扩展.

    核心功能：
    1. 手动注册：用户显式调用 register() 注册自定义 Backend
    2. 配置驱动：从 config.yaml 的 backend_class 字段自动加载 Backend 类
    3. 统一查找：通过 get() 方法查找已注册的 Backend 类
    4. SDK 内置：在 __init__.py 中自动注册内置 Backend（如 LocalStorageBackend）

    使用示例：
        # 方式1：手动注册（高级用户）
        from myapp.storage import MyObsBackend
        from jiuwenclaw.storage import StorageBackendRegistry

        StorageBackendRegistry.register("my-obs", MyObsBackend)

        # 方式2：配置驱动（推荐）
        # config.yaml:
        #   storage:
        #     type: "my-obs"
        #     backend_class: "myapp.storage.MyObsBackend"
        #     access_key: "..."
        #     secret_key: "..."

        # StorageService._create_backend() 会自动调用
        # Registry.register_from_config(config)
    """

    _registry: dict[str, type["BaseStorageBackend"]] = {}

    @classmethod
    def register(cls, name: str, backend_class: type["BaseStorageBackend"]) -> None:
        """手动注册 Backend 类.

        Args:
            name: Backend 类型名称（如 "local", "huawei-obs", "my-custom-obs")
            backend_class: Backend 类对象（必须继承 BaseStorageBackend）

        Raises:
            TypeError: 如果 backend_class 不是 BaseStorageBackend 子类

        示例：
            Registry.register("my-obs", MyObsBackend)
            Registry.register("local", LocalStorageBackend)  # SDK 内置 Backend 自动注册
        """
        from jiuwenclaw.storage.backend import BaseStorageBackend

        if not isinstance(backend_class, type) or not issubclass(backend_class, BaseStorageBackend):
            raise TypeError(
                f"backend_class 必须是 BaseStorageBackend 的子类，当前为: {backend_class}"
            )

        cls._registry[name] = backend_class

    @classmethod
    def register_from_config(cls, config: dict) -> None:
        """配置驱动自动注册 - 从 backend_class 字段动态加载 Backend 类.

        Args:
            config: 配置字典，包含 backend_class 字段（可选）

        Raises:
            ValueError: backend_class 格式错误（缺少类名部分）
            ImportError: 模块不存在或导入失败
            AttributeError: 模块中不存在指定的类

        示例：
            # config.yaml:
            #   storage:
            #     type: "my-obs"
            #     backend_class: "myapp.storage.MyObsBackend"
            #
            # Registry.register_from_config({"type": "my-obs", "backend_class": "myapp.storage.MyObsBackend"})
            # → 自动加载 myapp.storage 模块
            # → 自动注册 Registry.register("my-obs", MyObsBackend)
        """
        backend_class_path = config.get("backend_class")
        if not backend_class_path:
            # 配置未提供 backend_class 字段，不执行注册
            # 依赖 SDK 内置 Backend（已在 __init__.py 自动注册）
            return

        # 解析 backend_class 格式："module_path.ClassName"
        if "." not in backend_class_path:
            raise ValueError(
                f"backend_class 格式错误，应为 'module.ClassName'，当前为: {backend_class_path}"
            )

        module_path, class_name = backend_class_path.rsplit(".", 1)

        # 动态导入模块
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError as e:
            raise ImportError(
                f"模块不存在: {module_path}。请检查 backend_class 配置是否正确。"
            ) from e

        # 获取 Backend 类
        try:
            backend_class = getattr(module, class_name)
        except AttributeError as e:
            raise AttributeError(
                f"模块 {module_path} 中不存在类 {class_name}。请检查 backend_class 配置是否正确。"
            ) from e

        # 注册 Backend
        type_name = config.get("type")
        if type_name:
            cls.register(type_name, backend_class)

    @classmethod
    def get(cls, name: str) -> type["BaseStorageBackend"] | None:
        """查找已注册的 Backend 类.

        Args:
            name: Backend 类型名称

        Returns:
            Backend 类对象，如果未注册则返回 None

        示例：
            backend_class = Registry.get("local")
            if backend_class:
                backend = backend_class(config)
            else:
                raise ConfigError("Backend 'local' 未注册")
        """
        return cls._registry.get(name)

    @classmethod
    def list_available(cls) -> list[str]:
        """列出所有已注册的 Backend 类型名称.

        Returns:
            已注册 Backend 名称列表

        示例：
            available_backends = Registry.list_available()
            # 返回: ["local", "huawei-obs", "aliyun-oss"]
        """
        return list(cls._registry.keys())

    @classmethod
    def reset(cls) -> None:
        """清空注册表（主要用于测试环境）.

        示例：
            # 测试环境重置
            Registry.reset()
            # Registry.get("local") 返回 None
        """
        cls._registry.clear()