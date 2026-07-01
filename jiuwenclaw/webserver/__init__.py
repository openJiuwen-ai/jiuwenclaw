# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""FastAPI 化的 Web 后端公共包。

把原 ``app_web.py`` / ``app_enterprise_web.py`` 里重叠与各自的逻辑按职责拆分：

- ``common``：日志、SSL、dist 解析、目标地址规范化、路径越权防护、静态+SPA、/api 反代。
- ``file_api``：``/file-api/*`` 全部路由（``UploadFile`` 取代已废弃的 ``cgi``）。
- ``ws_proxy``：简单版 ``/ws`` 应用层 WebSocket 反代（透传 query/子协议 + 业务帧日志）。
- ``enterprise_broker``：企业版有状态 WS broker（``/ws`` 浏览器 + ``/gateway`` 网关 uplink）。
- ``app``：按形态组装 FastAPI app。

``jiuwenclaw/app_web.py`` 与 ``jiuwenclaw/app_enterprise_web.py`` 退化为薄入口：
解析既有 CLI → 组装 app → ``uvicorn`` 启动；对外命令行/端口/协议契约保持不变。
"""
