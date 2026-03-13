# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""PyInstaller 打包入口：根据参数分发到主应用或子命令。"""

from __future__ import annotations

import sys
import threading


def _run_app_with_web() -> None:
    """启动主应用，并在后台线程运行静态文件服务（等效 jiuwenclaw-start all）。"""
    from jiuwenclaw.app_web import main as web_main

    def run_web():
        web_main()

    t = threading.Thread(target=run_web, daemon=True)
    t.start()

    from jiuwenclaw.app import main as app_main
    app_main()


def main() -> None:
    # 子命令：初始化工作区（首次使用需运行 jiuwenclaw.exe init）
    if len(sys.argv) >= 2 and sys.argv[1].lower() == "init":
        sys.argv.pop(1)
        from jiuwenclaw.init_workspace import main as init_main
        init_main()
        return
    # 子命令：浏览器启动（供主进程 subprocess 调用）
    if "--browser-start-client" in sys.argv:
        idx = sys.argv.index("--browser-start-client")
        sys.argv.pop(idx)
        from jiuwenclaw.agentserver.tools.browser_start_client import main as browser_main
        raise SystemExit(browser_main())
    # 默认运行主应用（含静态文件服务）
    _run_app_with_web()


if __name__ == "__main__":
    main()
