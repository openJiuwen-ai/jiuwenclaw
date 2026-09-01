# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""permissions_config Codec 选型说明。

personal → :class:`YamlSectionCodec`；enterprise → :class:`DbBodySectionCodec`。
"""

from jiuwenswarm.gateway.config.section import DbBodySectionCodec, YamlSectionCodec

__all__ = ["DbBodySectionCodec", "YamlSectionCodec"]
