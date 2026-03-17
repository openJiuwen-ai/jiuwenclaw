# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from pydantic import ValidationError


class ExceptionUtils:
    @staticmethod
    def format_validation_error(e: ValidationError) -> str:
        return "\n".join([f"{'.'.join(map(str, err.get('loc', [])))}: {err.get('msg', 'Unknown error')}"
                          for err in e.errors()
                          ])
