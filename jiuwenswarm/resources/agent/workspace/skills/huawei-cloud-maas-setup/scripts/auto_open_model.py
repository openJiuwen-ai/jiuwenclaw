#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""自动批量开通华为云 MaaS 预置模型（V2 折中方案）。

前置条件：
- 浏览器已启动并通过 CDP 暴露
- 用户已登录华为云
- API Key 已创建（由 ``auto_create_apikey.py`` 处理）

本脚本职责：
- 接收多个 ``--model`` 参数（显示名=api名 格式）
- 导航到预置服务页
- 对每个模型：检查状态，未开通则自动开通
- 返回每个模型的开通结果

**关键约束**：
- **不抛异常给上层**：单个模型失败不影响其他模型
- **已开通自动跳过**：不重复操作
- 整个流程 ≤ 120s（取决于模型数量）

用法::

    python auto_open_model.py --cdp-url http://127.0.0.1:9333 --json \\
        --model "openPangu-2.0-Pro=openpangu-2.0-pro" \\
        --model "GLM-5.2=glm-5.2" \\
        --model "DeepSeek-V4-Flash=deepseek-v4-flash"
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from lib.cdp_client import (  # noqa: E402
    connect_page,
    emit,
    emit_progress,
    output_json,
    resolve_cdp_url,
)
from lib.flow_state import make_failure, make_success  # noqa: E402
from lib.huawei_selectors import (  # noqa: E402
    MAAS_DEPLOYMENT_URL,
    SELECTOR_MODEL_AGREEMENT,
    SELECTOR_MODEL_OPEN_CONFIRM,
    SELECTOR_PRESET_TAB,
    click_first_visible,
)


def _is_model_opened(row_text: str) -> bool:
    """判断模型行文本中是否已开通。"""
    if not row_text:
        return False
    if "未开通" in row_text or "开通中" in row_text:
        return False
    return bool(re.search(r"(^|\s)开通(\s|$)", row_text)) and "开通服务" not in row_text


def _parse_model_spec(spec: str) -> tuple[str, str]:
    """把 ``显示名=api名`` 或单独一个名字拆为 (display_name, api_name)。"""
    if "=" in spec:
        display, api = spec.split("=", 1)
        return display.strip(), api.strip()
    return spec.strip(), spec.strip().lower()


def _open_single_model(page, display_name: str, timeout_s: int = 60) -> dict:
    """开通单个模型。返回结果 dict。

    返回值：
    - ``{"ok": true, "already_opened": bool}`` 成功
    - ``{"ok": false, "error": str}`` 失败
    """
    # 定位模型行
    row = page.locator(
        f"tr:has-text('{display_name}'), div:has-text('{display_name}')"
    ).first
    try:
        row.wait_for(state="visible", timeout=10_000)
    except Exception:
        return {"ok": False, "error": f"未找到模型 {display_name}"}

    # 检查状态（已开通则跳过）
    row_text = row.inner_text(timeout=3_000) or ""
    if _is_model_opened(row_text):
        emit("model", f"{display_name} 已开通，跳过")
        return {"ok": True, "already_opened": True}

    # 点击"开通服务"按钮
    try:
        open_btn = row.locator(
            "button:has-text('开通服务'), a:has-text('开通服务')"
        ).first
        if not open_btn.is_visible(timeout=3_000):
            open_btn = row.locator("button:has-text('开通')").first
        open_btn.click()
    except Exception as exc:
        return {"ok": False, "error": f"未找到'开通服务'按钮: {exc}"}

    time.sleep(1.0)

    # 勾选同意声明
    checkbox = SELECTOR_MODEL_AGREEMENT.first_visible(page, timeout_ms=5_000)
    if checkbox is not None:
        try:
            checkbox.click()
        except Exception:
            pass
    time.sleep(0.5)

    # 点击"一键开通"
    confirm = SELECTOR_MODEL_OPEN_CONFIRM.first_visible(page, timeout_ms=3_000)
    if confirm is None:
        confirm = page.locator(
            "button:has-text('一键开通'), button:has-text('开通')"
        ).first
    try:
        confirm.click()
    except Exception as exc:
        return {"ok": False, "error": f"点击'一键开通'失败: {exc}"}

    # 等待状态变"开通"
    emit("model", f"等待 {display_name} 开通完成...")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(2.0)
        try:
            current = row.inner_text(timeout=2_000) or ""
            if _is_model_opened(current):
                emit("model", f"{display_name} 已开通")
                return {"ok": True, "already_opened": False}
        except Exception:
            pass
        # 关闭可能弹出的成功提示
        for label in ("确定", "关闭", "完成"):
            try:
                btn = page.locator(
                    f".el-message-box button:has-text('{label}')"
                ).first
                if btn.is_visible(timeout=300):
                    btn.click()
            except Exception:
                pass

    return {"ok": False, "error": f"等待开通超时（{timeout_s}s）"}


def auto_open_models(
    cdp_url: str,
    models: list[tuple[str, str]],
    timeout_s: int = 60,
) -> dict:
    """批量开通多个模型。"""
    total = len(models)
    emit_progress(0, total + 2, "连接浏览器...")
    try:
        pw, browser, page = connect_page(cdp_url, timeout_ms=15_000)
    except Exception as exc:
        return make_failure("connect_failed", f"无法连接浏览器: {exc}", cdp_url=cdp_url)

    try:
        # 1. 导航到部署页
        emit_progress(1, total + 2, f"导航到 {MAAS_DEPLOYMENT_URL}")
        try:
            page.goto(MAAS_DEPLOYMENT_URL, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:
            return make_failure("navigate_failed", f"导航失败: {exc}", cdp_url=cdp_url)

        # 2. 切换到"预置服务"页签
        emit_progress(2, total + 2, "切换到'预置服务'页签...")
        preset_tab = SELECTOR_PRESET_TAB.first_visible(page, timeout_ms=3_000)
        if preset_tab is not None:
            try:
                klass = preset_tab.get_attribute("class") or ""
                if "is-active" not in klass and "ant-tabs-tab-active" not in klass:
                    preset_tab.click()
                    time.sleep(2.0)
            except Exception:
                pass

        # 3. 逐个开通模型
        results: list[dict] = []
        opened: list[str] = []
        already_opened: list[str] = []
        failed: list[dict] = []

        for i, (display_name, api_name) in enumerate(models):
            step_num = i + 3
            emit_progress(step_num, total + 2, f"开通 {display_name}...")
            result = _open_single_model(page, display_name, timeout_s=timeout_s)
            result["display_name"] = display_name
            result["api_name"] = api_name
            results.append(result)

            if result["ok"]:
                if result.get("already_opened"):
                    already_opened.append(api_name)
                else:
                    opened.append(api_name)
            else:
                failed.append({"model": api_name, "error": result.get("error", "")})
                emit("model", f"{display_name} 开通失败: {result.get('error')}")

        all_done = len(failed) == 0
        return make_success(
            "open_models",
            cdp_url=cdp_url,
            models=results,
            opened=opened,
            already_opened=already_opened,
            failed=failed,
            all_done=all_done,
        )
    except Exception as exc:
        return make_failure("exception", f"未预期错误: {exc}", cdp_url=cdp_url)
    finally:
        try:
            pw.stop()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="自动批量开通预置模型")
    parser.add_argument("--cdp-url", default="", help="CDP endpoint")
    parser.add_argument(
        "--model",
        action="append",
        required=True,
        help="模型定义，格式 显示名=API模型名，可多次指定",
    )
    parser.add_argument("--timeout", type=int, default=60, help="单个模型开通超时秒数")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    cdp_url = (args.cdp_url or "").strip() or resolve_cdp_url()
    if not cdp_url:
        result = make_failure("init", "未找到 CDP URL")
        output_json(result)
        return 1

    models = [_parse_model_spec(s) for s in (args.model or [])]
    result = auto_open_models(cdp_url=cdp_url, models=models, timeout_s=args.timeout)
    output_json(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
