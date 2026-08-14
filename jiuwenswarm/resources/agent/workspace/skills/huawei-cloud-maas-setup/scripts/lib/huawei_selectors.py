#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""华为云 MaaS 控制台页面选择器集中维护。

每个关键操作提供多版本选择器，按优先级尝试首个可见的。
如果所有选择器都失效，调用方应回退到 LLM 辅助模式。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from playwright.sync_api import Locator, Page

from lib.cdp_client import emit


# ---------------------------------------------------------------------------
# 选择器定义
# ---------------------------------------------------------------------------


@dataclass
class SelectorSet:
    """一个语义操作的多版本选择器集合。"""

    name: str
    selectors: list[str] = field(default_factory=list)
    # 文本匹配（fallback，使用 text= 引擎）
    text_patterns: list[str] = field(default_factory=list)

    def first_visible(self, page: Page, timeout_ms: int = 3_000) -> Optional[Locator]:
        """返回第一个可见的 Locator，全部不可见则返回 None。"""
        # 优先按 CSS 选择器
        for sel in self.selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=timeout_ms):
                    return loc
            except Exception:
                continue
        # 退化到文本选择器
        for txt in self.text_patterns:
            try:
                loc = page.locator(f"text={txt}").first
                if loc.is_visible(timeout=timeout_ms):
                    return loc
            except Exception:
                continue
        return None


# 关键 URL
MAAS_HOMEPAGE_URL = (
    "https://console.huaweicloud.com/modelarts/?region=cn-southwest-2#/model-studio/homepage"
)
MAAS_APIKEY_URL = (
    "https://console.huaweicloud.com/modelarts/?region=cn-southwest-2#/model-studio/authmanage"
)
MAAS_DEPLOYMENT_URL = (
    "https://console.huaweicloud.com/modelarts/?region=cn-southwest-2#/model-studio/deployment"
)

# 区域信息
DEFAULT_REGION = "cn-southwest-2"
DEFAULT_API_BASE = "https://api.modelarts-maas.com/openai/v1"
DEFAULT_PROVIDER = "openai"

# 要开通的模型（默认列表）
DEFAULT_MODELS = [
    ("openPangu-2.0-Pro", "openpangu-2.0-pro"),
]


# 授权相关选择器
SELECTOR_AUTH_WARNING = SelectorSet(
    name="auth_warning",
    selectors=[
        "[class*='warning']",
        "[class*='alert']",
        "[class*='notice']",
    ],
    text_patterns=[
        "已有委托缺失当前模块依赖的部分服务权限",
        "权限不足",
        "尚未授权",
        "委托授权",
        "访问授权",
    ],
)

SELECTOR_AUTH_HERE_LINK = SelectorSet(
    name="auth_here_link",
    selectors=[],
    text_patterns=["此处"],
)

SELECTOR_AUTH_DIALOG = SelectorSet(
    name="auth_dialog",
    selectors=[".el-dialog", ".ant-modal", "[role='dialog']"],
    text_patterns=["追加至已有委托", "新建委托", "委托名称"],
)

SELECTOR_AUTH_CONFIRM = SelectorSet(
    name="auth_confirm",
    selectors=[
        ".el-dialog .el-button--primary",
        ".ant-modal .ant-btn-primary",
    ],
    text_patterns=["确定", "确认"],
)

SELECTOR_AUTH_SUCCESS = SelectorSet(
    name="auth_success",
    selectors=[],
    text_patterns=["权限更新成功", "授权成功"],
)


# API Key 相关选择器
SELECTOR_CREATE_APIKEY_BTN = SelectorSet(
    name="create_apikey_btn",
    selectors=[],
    text_patterns=["创建 API Key", "创建API Key", "创建 API key", "新建 API Key"],
)

# 标签输入框（API Key 创建表单的第一个字段）
# 标签长度 1-100，支持大小写英文字母、数字、下划线、中划线
SELECTOR_APIKEY_TAG_INPUT = SelectorSet(
    name="apikey_tag_input",
    selectors=[
        "input[placeholder*='标签']",
        "input[placeholder*='名称']",
        # 弹窗中第一个 input（通常是标签）
        ".el-dialog .el-form-item:first-child input",
        ".el-dialog input:nth-of-type(1)",
    ],
)

SELECTOR_APIKEY_DESC_INPUT = SelectorSet(
    name="apikey_desc_input",
    selectors=[
        "input[placeholder*='描述']",
        "input[placeholder*='备注']",
        "textarea[placeholder*='描述']",
        "textarea[placeholder*='备注']",
        # 弹窗中的 textarea（描述通常是多行文本框）
        ".el-dialog textarea",
    ],
)

# 权限设置（通常是下拉选择或单选按钮，保持默认即可，不需要主动操作）
# 如果需要选择，这里提供备选选择器
SELECTOR_APIKEY_PERMISSION = SelectorSet(
    name="apikey_permission",
    selectors=[
        "input[placeholder*='权限']",
        ".el-dialog [class*='permission']",
        ".el-dialog [class*='scope']",
    ],
    text_patterns=["权限设置", "权限范围"],
)

SELECTOR_APIKEY_CONFIRM = SelectorSet(
    name="apikey_confirm",
    selectors=[".el-dialog .el-button--primary", ".ant-modal .ant-btn-primary"],
    text_patterns=["确定", "确认"],
)

SELECTOR_APIKEY_DIALOG = SelectorSet(
    name="apikey_dialog",
    selectors=[".el-dialog", ".ant-modal", "[role='dialog']"],
    text_patterns=["创建成功", "API Key", "密钥", "Secret", "复制"],
)

SELECTOR_APIKEY_SAVED_BTN = SelectorSet(
    name="apikey_saved_btn",
    selectors=[],
    text_patterns=["我已保存", "确认关闭", "关闭", "完成", "确定"],
)

# 欠费/余额不足错误提示（创建 Key 失败时检测）
SELECTOR_INSUFFICIENT_BALANCE = SelectorSet(
    name="insufficient_balance",
    selectors=[
        ".el-message--error",
        ".el-notification__content",
        "[class*='error']",
    ],
    text_patterns=[
        "欠费",
        "余额不足",
        "账户已欠费",
        "请先充值",
        "insufficient balance",
        "账号已冻结",
        "资源不足",
    ],
)

# 华为云充值页面 URL
RECHARGE_URL = "https://account.huaweicloud.com/usercenter/#/accountindex/balance"

# 开通模型相关选择器
SELECTOR_PRESET_TAB = SelectorSet(
    name="preset_tab",
    selectors=[
        "[role='tab']",
        ".el-tabs__item",
    ],
    text_patterns=["预置服务"],
)

SELECTOR_MODEL_OPEN_BTN = SelectorSet(
    name="model_open_btn",
    selectors=[],
    text_patterns=["开通服务", "一键开通"],
)

SELECTOR_MODEL_AGREEMENT = SelectorSet(
    name="model_agreement",
    selectors=[
        "input[type='checkbox']",
        ".el-checkbox__input",
        ".el-checkbox__inner",
    ],
    text_patterns=["我已阅读并同意", "同意", "MaaS 模型即服务声明"],
)

SELECTOR_MODEL_OPEN_CONFIRM = SelectorSet(
    name="model_open_confirm",
    selectors=[".el-message-box .el-button--primary"],
    text_patterns=["一键开通", "开通", "确定"],
)


# ---------------------------------------------------------------------------
# 高层操作函数
# ---------------------------------------------------------------------------


def click_first_visible(page: Page, selector_set: SelectorSet, timeout_ms: int = 3_000) -> bool:
    """点击第一个可见的元素。返回是否成功。"""
    loc = selector_set.first_visible(page, timeout_ms=timeout_ms)
    if loc is None:
        return False
    try:
        loc.click()
        return True
    except Exception as exc:
        emit(selector_set.name, f"click failed: {exc}")
        return False


def find_in_row(page: Page, row_text: str, selector_set: SelectorSet) -> Optional[Locator]:
    """在包含指定文本的行内查找元素。"""
    row = page.locator(f"tr:has-text('{row_text}'), div:has-text('{row_text}')").first
    try:
        if not row.is_visible(timeout=2_000):
            return None
    except Exception:
        return None
    return row.locator(selector_set.selectors[0]) if selector_set.selectors else None


def extract_api_key_from_dialog(page: Page) -> str:
    """从 API Key 展示弹窗中提取完整 Key。"""
    # 策略 1：只读 input/textarea
    for selector in (
        "input[readonly]",
        "textarea[readonly]",
        ".api-key-value",
        ".secret-value",
        "[class*='key'] input",
        "[class*='secret'] input",
    ):
        try:
            loc = page.locator(selector)
            count = loc.count()
            for i in range(min(count, 5)):
                try:
                    val = loc.nth(i).input_value(timeout=1_000)
                    if val and len(val) >= 30:
                        return val.strip()
                except Exception:
                    continue
        except Exception:
            continue

    # 策略 2：evaluate 遍历弹窗 DOM
    try:
        key = page.evaluate(
            """
            () => {
                const dialog = document.querySelector(
                    '.el-dialog, .ant-modal, [role="dialog"]'
                );
                if (!dialog) return '';
                const candidates = dialog.querySelectorAll(
                    'code, pre, span, div, input, textarea'
                );
                for (const el of candidates) {
                    let text = (el.textContent || el.value || '').trim();
                    if (
                        text.length >= 30 &&
                        text.length <= 200 &&
                        /^[A-Za-z0-9_\\-]+$/.test(text)
                    ) {
                        return text;
                    }
                }
                return '';
            }
            """
        )
        if key and len(key) >= 30:
            return key
    except Exception:
        pass

    return ""
