# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Heartbeat 模块 — 新心跳任务(线程续跑)。

旧探活(HEARTBEAT.md 驱动的全局周期探活)已迁移到 ``gateway/health_check/``(方案 §2.3),
旧 ``heartbeat.py`` shim 已删除。本目录只服务新心跳任务:
  - models.py: HeartbeatJob / Schedule / 状态机
  - store.py: heartbeat_jobs.json 读写
  - scheduler.py: HeartbeatSchedulerService(投递 CHAT_SEND 回原 session)
  - controller.py: HeartbeatController(Web/RPC + Agent Tool)
  - session_resolver.py: HeartbeatSessionResolver

新心跳任务符号通过完整子模块路径 import,本 __init__ 不再 re-export 旧探活符号。
旧调用方应改用 ``from jiuwenswarm.gateway.health_check import ...``。
"""

__all__: list[str] = []
