#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""自动批量开通华为云 MaaS 预置模型。

前置条件：
- 浏览器已启动并通过 CDP 暴露
- 用户已登录华为云
- API Key 已创建（由 ``auto_create_apikey.py`` 处理）

本脚本职责：
- 导航到预置服务页
- 点击任意服务的"开通服务"按钮触发批量开通弹窗
- 在弹窗中勾选指定的模型（支持滚动查找）
- 勾选同意声明并点击"一键开通"
- 返回开通结果

**关键约束**：
- **不抛异常给上层**：失败时返回 ``{ok:false, stage:..., error:...}``
- 整个流程 ≤ 90s

用法::

    python auto_open_model.py --cdp-url http://127.0.0.1:9333 --json \\
        --model "openPangu-2.0-Pro" \\
        --model "GLM-5.2" \\
        --model "DeepSeek-V4-Flash"
"""
from __future__ import annotations

import argparse
import json
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
from lib.huawei_selectors import MAAS_DEPLOYMENT_URL  # noqa: E402

_DEFAULT_MODELS_FILE = str(Path(__file__).resolve().parent.parent / "models.json")

# 弹窗容器候选：华为云使用 Ti3/TinyV 组件（ti3-modal / subscribe-model-modal），
# 也可能是 el-dialog / ant-modal / 自定义 dialog / drawer
_DIALOG_CSS = (
    "subscribe-model-modal, .el-dialog, .ant-modal, .modal, .ant-drawer, "
    "[role='dialog'], [class*='modal'], [class*='drawer']"
)
# 滚动定位时使用的容器选择器
_DIALOG_SELECTOR_JS = (
    "subscribe-model-modal, .el-dialog, .ant-modal, [role='dialog'], "
    "[class*='modal'], [class*='drawer']"
)
# 弹窗内容信号：检测到这些文本即视为弹窗已渲染（容器类名兜底）
_DIALOG_SIGNAL_TEXTS = ("一键开通", "我已阅读并同意", "开通预置模型服务")


def _wait_for_dialog(page, timeout_s: float = 12.0, poll_s: float = 0.8):
    """等待批量开通弹窗出现。

    1. 按多种容器类名匹配可见弹窗；
    2. 若类名均未命中，用内容信号（"一键开通"/"我已阅读并同意" 文本）兜底，
       返回以整页为操作范围的 locator；
    3. 均未命中时返回 None。
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            cand = page.locator(_DIALOG_CSS).first
            if cand.is_visible(timeout=int(poll_s * 1000)):
                return cand
        except Exception:
            pass
        try:
            signal = page.locator(
                ", ".join(f"text={t}" for t in _DIALOG_SIGNAL_TEXTS)
            ).first
            if signal.is_visible(timeout=int(poll_s * 1000)):
                emit("model", "检测到弹窗内容信号（已渲染弹窗内容）")
                return page.locator("body").first
        except Exception:
            pass
        time.sleep(poll_s)
    return None


def _load_display_names(models_file: str) -> list[str]:
    """从 models.json 读取模型 display_name 列表。"""
    p = Path(models_file)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    return [
        m["display_name"]
        for m in models
        if isinstance(m, dict) and m.get("display_name")
    ]


def _is_checked(page, input_selector: str) -> bool:
    """查询原生 checkbox 是否勾选。"""
    try:
        return bool(page.locator(input_selector).first.is_checked())
    except Exception:
        return False


def _ensure_agreement_checked(page) -> bool:
    """勾选'我已阅读并同意…《MaaS 模型即服务声明》'（input#agreement）。

    华为云 Ti3 结构：``input#agreement`` + ``label#agreement_checkbox``（for=agreement），
    真实文案在 ``label.agreement-label`` 中。优先点 label，失败再点 input。
    返回最终是否勾选。
    """
    try:
        if _is_checked(page, "#agreement"):
            return True
        label = page.locator("#agreement_checkbox").first
        if label.count() > 0 and label.is_visible():
            label.click(timeout=3_000)
        else:
            page.locator("#agreement").first.click(timeout=3_000)
        time.sleep(0.3)
        return _is_checked(page, "#agreement")
    except Exception as exc:
        emit("model", f"勾选同意声明失败: {exc}")
        return _is_checked(page, "#agreement")


def _page_text_preview(page, max_chars: int = 1500) -> str:
    """截取当前页面可读文本，用于失败时返回 DOM 线索以便定位。"""
    try:
        text = page.locator("body").inner_text(timeout=2_000) or ""
    except Exception:
        return ""
    return " ".join(text.split())[:max_chars]


def _scroll_dialog_to_find_model(page, dialog_locator, model_name: str,
                                  max_scrolls: int = 10) -> bool:
    """在弹窗内容区滚动查找目标模型并勾选，返回是否成功勾选。"""
    for i in range(max_scrolls):
        try:
            # Ti3 结构: span.ti3-checkbox-group-item > input + label.ti3-checkbox
            #   label.ti3-checkbox > ... > label.service-list(模型名)
            # 点击 label 本身即可切换 checkbox（for 指向 input）
            checkbox = dialog_locator.locator(
                f".ti3-checkbox:has-text('{model_name}'), "
                f".el-checkbox:has-text('{model_name}'), "
                f"label:has-text('{model_name}'):has(input[type='checkbox'])"
            ).first
            if checkbox.is_visible(timeout=1_500):
                checkbox.click()
                time.sleep(0.3)
                return True
        except Exception:
            pass

        # 未找到，向下滚动弹窗内容区
        try:
            page.evaluate(
                """(dialogSelector) => {
                    const dialog = document.querySelector(dialogSelector);
                    if (!dialog) return false;
                    const scrollable = dialog.querySelector(
                        '[class*="scroll"], [class*="content"], [class*="body"], '
                        + '.el-dialog__body, .ant-modal-body, .ti3-modal-body, '
                        + 'ti-modal-body'
                    ) || dialog;
                    scrollable.scrollTop += 300;
                    return true;
                }""",
                _DIALOG_SELECTOR_JS,
            )
            time.sleep(0.5)
        except Exception:
            pass

    return False


def auto_open_models(
    cdp_url: str,
    models: list[str],
    timeout_s: int = 90,
) -> dict:
    """批量开通多个模型（通过批量勾选弹窗）。"""
    total = len(models)
    emit_progress(0, total + 5, "连接浏览器...")
    try:
        pw, browser, page = connect_page(cdp_url, timeout_ms=15_000)
    except Exception as exc:
        return make_failure("connect_failed", f"无法连接浏览器: {exc}",
                            cdp_url=cdp_url)

    try:
        # 1. 导航到部署页
        emit_progress(1, total + 5, f"导航到 {MAAS_DEPLOYMENT_URL}")
        try:
            page.goto(MAAS_DEPLOYMENT_URL, wait_until="domcontentloaded",
                      timeout=30_000)
            time.sleep(3.0)  # 等待 SPA 异步渲染完成
        except Exception as exc:
            return make_failure("navigate_failed", f"导航失败: {exc}",
                                cdp_url=cdp_url)

        # 2. 点击任意一行的"开通服务"按钮触发批量弹窗
        emit_progress(2, total + 5, "点击'开通服务'触发批量弹窗...")
        try:
            # 放宽标签限制：button/a/[role=button]/.el-button 均可
            open_btn = page.locator(
                "button:has-text('开通服务'), "
                "a:has-text('开通服务'), "
                "[role='button']:has-text('开通服务'), "
                ".el-button:has-text('开通服务')"
            ).first
            if not open_btn.is_visible(timeout=8_000):
                # 退化到纯文本匹配
                open_btn = page.locator("text='开通服务'").first
                if not open_btn.is_visible(timeout=3_000):
                    return make_failure(
                        "no_open_btn",
                        "未找到'开通服务'按钮，请确认页面已加载且未登录",
                        cdp_url=cdp_url,
                    )
            open_btn.click()
            time.sleep(2.0)  # 等待弹窗出现
        except Exception as exc:
            return make_failure(
                "click_open_failed",
                f"点击'开通服务'失败: {exc}",
                cdp_url=cdp_url,
            )

        # 3. 等待批量开通弹窗出现（多容器类名 + 内容信号 + 新标签页兜底）
        emit_progress(3, total + 5, "等待批量开通弹窗...")
        dialog = _wait_for_dialog(page)
        if dialog is None:
            # "开通服务"可能在新标签页打开开通页而非弹窗
            try:
                ctx = browser.contexts[0] if browser.contexts else None
                if ctx is not None:
                    for p in ctx.pages:
                        if p != page and "deployment" in (p.url or ""):
                            page = p
                            emit("model", f"切换到新标签页: {p.url}")
                            break
            except Exception:
                pass
            dialog = _wait_for_dialog(page)
        if dialog is None:
            return make_failure(
                "dialog_timeout",
                "等待批量开通弹窗超时（未检测到弹窗或'一键开通'内容）",
                cdp_url=cdp_url,
            )
        time.sleep(1.0)

        # 4. 在弹窗中勾选指定的模型（支持滚动查找）
        emit_progress(4, total + 5, f"在弹窗中勾选 {total} 个模型...")
        opened_models = []
        failed_models = []
        for model_name in models:
            emit("model", f"查找并勾选: {model_name}")
            try:
                found = _scroll_dialog_to_find_model(
                    page, dialog, model_name, max_scrolls=15
                )
                if found:
                    opened_models.append(model_name)
                    emit("model", f"已勾选: {model_name}")
                else:
                    failed_models.append({
                        "model": model_name,
                        "error": "在弹窗中未找到该模型（可能需要滚动更多或模型名不匹配）",
                    })
                    emit("model", f"未找到模型: {model_name}")
            except Exception as exc:
                failed_models.append({
                    "model": model_name,
                    "error": str(exc),
                })
                emit("model", f"勾选 {model_name} 失败: {exc}")

        if not opened_models:
            return make_failure(
                "no_models_selected",
                "未成功勾选任何模型",
                cdp_url=cdp_url,
                failed=failed_models,
            )

        # 5. 勾选同意声明（input#agreement + label#agreement_checkbox）
        emit("model", "勾选同意声明...")
        if _ensure_agreement_checked(page):
            emit("model", "同意声明已勾选")
        else:
            emit("model", "警告: 未能勾选同意声明，'一键开通'可能保持禁用")

        # 6. 点击"一键开通"按钮（#subscribe-button，勾选同意声明后才启用）
        emit_progress(5, total + 5, "点击'一键开通'...")
        try:
            confirm_btn = page.locator(
                "#subscribe-button, [data-qa-id='subscribe-button']"
            ).first
            confirm_btn.wait_for(state="visible", timeout=8_000)
            # 未勾选协议时按钮 disabled：等待状态刷新为可用
            if confirm_btn.is_disabled():
                emit("model", "'一键开通'仍为禁用态，等待启用...")
                deadline_wait = time.time() + 8
                while time.time() < deadline_wait and confirm_btn.is_disabled():
                    time.sleep(0.5)
            if confirm_btn.is_disabled():
                preview = _page_text_preview(page)
                return make_failure(
                    "confirm_disabled",
                    "'一键开通'按钮禁用：模型未勾选或同意声明未勾选",
                    cdp_url=cdp_url,
                    page_text_preview=preview,
                )
            confirm_btn.click()
            emit("model", "已点击'一键开通'")
        except Exception as exc:
            return make_failure(
                "click_confirm_failed",
                f"点击'一键开通'失败: {exc}",
                cdp_url=cdp_url,
            )

        # 7. 等待开通完成（弹窗关闭或出现成功提示）
        emit_progress(6, total + 5, "等待开通完成...")
        deadline = time.time() + timeout_s
        success = False
        while time.time() < deadline:
            time.sleep(2.0)
            # 弹窗关闭：一键开通按钮消失（或 modal 不可见）
            try:
                if page.locator("#subscribe-button").count() == 0:
                    emit("model", "弹窗已关闭，开通完成")
                    success = True
                    break
            except Exception:
                pass
            try:
                if not dialog.is_visible(timeout=1_000):
                    emit("model", "弹窗已关闭，开通完成")
                    success = True
                    break
            except Exception:
                emit("model", "弹窗已关闭，开通完成")
                success = True
                break
            # 失败提示
            try:
                fail_msg = page.locator("text=开通失败, text=失败").first
                if fail_msg.is_visible(timeout=800):
                    emit("model", "检测到开通失败提示")
            except Exception:
                pass
            # 成功提示
            try:
                success_msg = page.locator(
                    ".el-message--success, .ant-message-success, "
                    "text=开通成功, text=开通完成"
                ).first
                if success_msg.is_visible(timeout=800):
                    emit("model", "检测到成功提示")
                    success = True
                    break
            except Exception:
                pass

        if not success:
            emit("model", "警告: 超时未检测到开通完成，但已提交开通请求")

        return make_success(
            "open_models",
            cdp_url=cdp_url,
            models=models,
            opened=opened_models,
            already_opened=[],
            failed=failed_models,
            all_done=len(failed_models) == 0,
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
        help="模型名称，可多次指定（优先于 --models-file）",
    )
    parser.add_argument(
        "--models-file",
        default=_DEFAULT_MODELS_FILE,
        help="模型列表 JSON 文件路径（当未指定 --model 时使用）",
    )
    parser.add_argument("--timeout", type=int, default=90,
                        help="开通超时秒数")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    cdp_url = (args.cdp_url or "").strip() or resolve_cdp_url()
    if not cdp_url:
        result = make_failure("init", "未找到 CDP URL")
        output_json(result)
        return 1

    if args.model:
        models = [s.strip() for s in args.model]
    else:
        models = _load_display_names(args.models_file)
        if not models:
            result = make_failure("init", f"未从 {args.models_file} 加载到模型")
            output_json(result)
            return 1

    result = auto_open_models(cdp_url=cdp_url, models=models,
                              timeout_s=args.timeout)
    output_json(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
