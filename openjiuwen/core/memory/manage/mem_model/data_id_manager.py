# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import struct
import secrets
import time


class DataIdManager:

    async def generate_next_id(self, user_id: str) -> str:
        t = int(time.time() * 1000) & 0xFFFFFFFFFFFF
        r = secrets.token_bytes(3)
        h = hash(user_id) & 0xFFFFFF
        t_bytes = struct.pack(">Q", t)[2:]
        h_bytes = struct.pack(">I", h)[1:]
        raw = t_bytes + r + h_bytes
        return raw.hex()
