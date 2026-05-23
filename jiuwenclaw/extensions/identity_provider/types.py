# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""身份信息数据结构定义。"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class IdentityInfo:
    """身份信息数据结构。

    用于封装 User ID、Domain ID、Application ID 等身份信息，
    供日志系统自动携带到每条日志中。
    """

    user_id: str | None = None
    domain_id: str | None = None
    app_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，仅包含非 None 字段。

        Returns:
            dict: 包含非 None 字段的字典。
        """
        result: dict[str, Any] = {}
        if self.user_id is not None:
            result["user_id"] = self.user_id
        if self.domain_id is not None:
            result["domain_id"] = self.domain_id
        if self.app_id is not None:
            result["app_id"] = self.app_id
        if self.extra:
            result["extra"] = self.extra
        return result