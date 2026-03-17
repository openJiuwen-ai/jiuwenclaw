# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import hashlib


def generate_key(api_key: str, api_base: str, model_provider: str = "openai") -> str:
    combined = "".join(sorted([api_key, api_base, model_provider]))
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
