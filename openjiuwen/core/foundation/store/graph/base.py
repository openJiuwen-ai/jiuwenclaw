# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Graph Store Factory

Factory class for creating and managing graph store backend instances
"""

from threading import RLock
from typing import Dict, Optional, Type

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger

from .base_graph_store import GraphStore
from .config import GraphConfig


class GraphStoreFactory:
    """Factory class to assemble graph store instances"""

    class_map: Dict[str, Type[GraphStore]] = dict()
    _thread_lock: RLock = RLock()

    def __init__(self, *args, **kwargs):
        raise build_error(
            StatusCode.STORE_GRAPH_FACTORY_NOT_INSTANTIABLE,
            class_name=self.__class__.__name__,
        )

    @classmethod
    def register_backend(cls, name: str, backend: Type[GraphStore], force: bool = False):
        """Register graph store backend for storing / representing graph.

        Args:
            name (str): Name for the new graph store backend.
            backend (Type[GraphStore]): Class for the new graph store backend.
            force (bool, optional): Whether to force register. Defaults to False.

        Raises error:
            - name is empty
            - name already registered and force=False.
            - backend did not implement the GraphStore Protocol.
        """
        with cls._thread_lock:
            if not name:
                raise build_error(
                    StatusCode.STORE_GRAPH_BACKEND_NAME_INVALID,
                    error_msg="Backend name cannot be registered as an empty value.",
                )
            if name in cls.class_map and not force:
                raise build_error(
                    StatusCode.STORE_GRAPH_BACKEND_ALREADY_EXISTS,
                    name=name,
                    existing=cls.class_map[name].__name__,
                )
            if not isinstance(backend, GraphStore):
                err_msg = f"{name} did not implement GraphStore Protocol!"
                if not force:
                    raise build_error(
                        StatusCode.STORE_GRAPH_PROTOCOL_NOT_IMPLEMENTED,
                        error_msg=err_msg,
                    )
                logger.warning(err_msg)
            cls.class_map[name] = backend
            logger.info("Graph Store registered: %s", name)

    @classmethod
    def from_config(cls, config: GraphConfig, backend_name: Optional[str] = None, **kwargs) -> GraphStore:
        """Fetch a GraphStore instance by configuration file.

        Args:
            config (GraphConfig): Database configuration.
            backend_name (Optional[str], optional): If not None, overwrites database backend choice in config. \
                Defaults to None.

        Raises error:
            - the database backend choice has not been registered in GraphStoreFactory.

        Returns:
            GraphStore: instance of graph store.
        """
        with cls._thread_lock:
            name = backend_name or config.backend
            backend_cls = cls.class_map.get(name)
            if backend_cls:
                return backend_cls.from_config(config, **kwargs)

        if name == "milvus":
            from .milvus import register_milvus_support

            register_milvus_support()
            return cls.from_config(config, backend_name=backend_name, **kwargs)
        raise build_error(StatusCode.STORE_GRAPH_BACKEND_NOT_FOUND, name=name)
