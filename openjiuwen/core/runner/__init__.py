# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

def __getattr__(name):
    if name == "Runner":
        from openjiuwen.core.runner.runner import Runner
        return Runner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
