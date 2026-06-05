# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations
from typing import Any


def create_upload_file_obs() -> Any:
    from jiuwenclaw.sandbox import sandbox_routing_enabled
    if sandbox_routing_enabled():
        from jiuwenclaw.agentserver.skilldev.utils.upload_file_obs_sandbox import UploadFileByOSMS
        return UploadFileByOSMS()
    from jiuwenclaw.agentserver.skilldev.utils.upload_file_obs import UploadFileOSMS
    return UploadFileOSMS()
