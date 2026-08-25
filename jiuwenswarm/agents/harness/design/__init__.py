# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""design profile 专属的 harness 组件（创意设计模式，对应 PPT/网站/文档/海报等设计场景）。

派生自 code profile：rails 通过继承 CodeAgentModeRail 复用 plan 模式的机械逻辑，
仅 system prompt builder 与 plan 提示词是 design 专属的全新内容（对齐 WorkBuddy
设计模式 7 段独有内容）。
"""
