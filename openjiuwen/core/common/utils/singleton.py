# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import abc
import threading
singleton_lock = threading.Lock()


class Singleton(abc.ABCMeta, type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        with singleton_lock:
            if cls not in cls._instances:
                cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
            return cls._instances[cls]