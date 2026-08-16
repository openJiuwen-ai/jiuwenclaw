#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""华为云 MaaS 控制台页面选择器集中维护。

每个关键操作提供多版本选择器，按优先级尝试首个可见的。
如果所有选择器都失效，调用方应回退到 LLM 辅助模式。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

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
        """返回第一个当前可见的 Locator（即时快照，不等待）。

        用于\"可选/检测\"类元素（权限字段、授权警告、欠费提示等）：不存在时应立即返回 None，
        避免因等待整个超时拖慢流程。
        timeout_ms 仅用于减小极端抖动，语义以即时快照为准。
        """
        # 优先按 CSS 选择器
        for sel in self.selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible():
                    return loc
            except Exception:
                continue
        # 退化到文本选择器
        for txt in self.text_patterns:
            try:
                loc = page.locator(f"text={txt}").first
                if loc.is_visible():
                    return loc
            except Exception:
                continue
        return None

    def wait_first(self, page: Page, timeout_ms: int = 8_000) -> Optional[Locator]:
        """等待第一个可见的 Locator 出现（进入 DOM 且可见）。

        用于\"必然出现\"的元素（创建按钮、弹窗、必填输入框等）：
        Angular SPA 在 ``domcontentloaded`` 之后仍异步渲染子视图，
        必须真正等到元素出现（进入 DOM 且可见）才算存在，避免\"误判不存在\"或\"秒失败\"。
        """
        # 优先按 CSS 选择器
        for sel in self.selectors:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state="visible", timeout=timeout_ms)
                return loc
            except Exception:
                continue
        # 退化到文本选择器
        for txt in self.text_patterns:
            try:
                loc = page.locator(f"text={txt}").first
                loc.wait_for(state="visible", timeout=timeout_ms)
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

# ---------------------------------------------------------------------------
# 预置服务列表（在线推理 -> 预置服务）选择器
# 页面为 Angular + Ti3/TinyV 组件：ti-grid > ti-table > tbody > tr 行。
# 每行列：服务名称 -> 状态 -> 类型 -> 计费方式 -> 推理定价 -> 优惠折扣 ->
#         模型限流 -> 调用统计 -> 操作（右侧含"开通服务"按钮）
# ---------------------------------------------------------------------------
# 数据行（虚拟滚动列表中的 tr）
MAAS_DEPLOYMENT_ROWS = "tr.ti3-table-detail-icon-tr"
# 服务名称（每行第一个 ti-grid 单元格内 service-name-render 的文本）
MAAS_DEPLOYMENT_ROW_NAME = ".service-name-render .name-string"
# 状态（未开通 / 已开通 / 开通中）
MAAS_DEPLOYMENT_ROW_STATUS = "grid-status-render .text"
# 类型（文本生成 / 视频生成 / 文本向量化 / 重排序...），取该单元格文本
MAAS_DEPLOYMENT_ROW_TYPE = "grid-tip-text-render"
# 行右侧"开通服务"按钮（aria-label 稳定，作用域限定在行内）
MAAS_DEPLOYMENT_ROW_OPEN_BTN = "[aria-label='开通服务']"
# 顶部按名称搜索框（筛选后可跨分页定位模型）
MAAS_DEPLOYMENT_SEARCH_BOX = (
    "input#prefabricatedServicesListViewInfo_property_search_searchbox_input, "
    "input.tp-searchbox-input"
)
# 批量开通弹窗容器（Ti3 modal，优先精确 id/组件名）
MAAS_SUBSCRIBE_MODAL = "#subScribe-model, subscribe-model-modal"

# 模型列表定义在 <skill_dir>/models.json 中，由 auto_open_model.py 和
# config_writer.py 读取，不再在此硬编码。


# 授权相关选择器
SELECTOR_AUTH_WARNING = SelectorSet(
    name="auth_warning",
    selectors=[
        "#authGlobalMessage",
        ".ti3-alert",
        "[class*='alert']",
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
    selectors=[
        ".ti3-modal",
        "[role='dialog']",
    ],
    text_patterns=["追加至已有委托", "新建委托", "委托名称"],
)

SELECTOR_AUTH_CONFIRM = SelectorSet(
    name="auth_confirm",
    selectors=[
        ".ti3-modal .ti3-btn-primary",
    ],
    text_patterns=["确定", "确认"],
)

SELECTOR_AUTH_SUCCESS = SelectorSet(
    name="auth_success",
    selectors=[],
    text_patterns=["权限更新成功", "授权成功"],
)


# API Key 相关选择器
# 华为云控制台为 Angular SPA，页面存在稳定属性 id/data-qa-id，
# 优先使用 CSS 选择器，文本匹配仅作兜底（文案/空白变化不影响）。
SELECTOR_CREATE_APIKEY_BTN = SelectorSet(
    name="create_apikey_btn",
    selectors=[
        "#authManageToolsCreateBtn",
        "[data-qa-id='authManageToolsCreateBtn']",
    ],
    text_patterns=[
        "创建 API Key",
        "创建API Key",
        "创建 API key",
        "新建 API Key",
        "创建密钥",
        "新建密钥",
    ],
)

# 标签输入框（API Key 创建表单的第一个字段）
# 标签长度 1-100，支持大小写英文字母、数字、下划线、中划线
SELECTOR_APIKEY_TAG_INPUT = SelectorSet(
    name="apikey_tag_input",
    selectors=[
        "input[placeholder*='标签']",
        "input[placeholder*='名称']",
        ".ti3-modal input[type='text']",
    ],
)

SELECTOR_APIKEY_DESC_INPUT = SelectorSet(
    name="apikey_desc_input",
    selectors=[
        "input[placeholder*='描述']",
        "input[placeholder*='备注']",
        "textarea[placeholder*='描述']",
        "textarea[placeholder*='备注']",
        ".ti3-modal textarea",
    ],
)

# 权限设置（通常是下拉选择或单选按钮，保持默认即可，不需要主动操作）
# 如果需要选择，这里提供备选选择器
SELECTOR_APIKEY_PERMISSION = SelectorSet(
    name="apikey_permission",
    selectors=[
        "input[placeholder*='权限']",
        ".ti3-modal [class*='permission']",
        ".ti3-modal [class*='scope']",
    ],
    text_patterns=["权限设置", "权限范围"],
)

SELECTOR_APIKEY_CONFIRM = SelectorSet(
    name="apikey_confirm",
    selectors=[
        ".ti3-modal .ti3-btn-primary",
    ],
    text_patterns=["确定", "确认"],
)

# 创建成功后的 Key 展示弹窗（copy-key-modal）。
# 优先匹配 copy-key-modal 专属信号（id/data-qa-id/组件名/输入框类名），
# 不再使用泛化 .ti3-modal / [class*='modal'] 兜底，避免误匹配隐藏/遗留弹窗。
SELECTOR_APIKEY_DIALOG = SelectorSet(
    name="apikey_dialog",
    selectors=[
        "#copyKeyModelCloseBtn",
        "[data-qa-id='copyKeyModelCloseBtn']",
        "copy-key-modal",
        ".copy-key-modal-form-input",
        ".copy-key-modal-form-input input",
    ],
    text_patterns=["您的API Key", "您的 API Key", "我已保存", "创建成功"],
)

SELECTOR_APIKEY_SAVED_BTN = SelectorSet(
    name="apikey_saved_btn",
    selectors=[
        "#copyKeyModelCloseBtn",
        "[data-qa-id='copyKeyModelCloseBtn']",
    ],
    text_patterns=["我已保存", "确认关闭"],
)

# 创建成功弹窗中的"复制"按钮（best-effort，点击失败不影响流程）
COPY_KEY_BUTTON_ID = "copyKeyButton"

# 欠费/余额不足错误提示（创建 Key 失败时检测）。
# 不使用 [class*='error'] 等泛化选择器，避免误匹配；主要依赖 text_patterns 关键词命中。
SELECTOR_INSUFFICIENT_BALANCE = SelectorSet(
    name="insufficient_balance",
    selectors=[
        ".ti3-message-error",
        ".ti3-alert-error",
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


# ---------------------------------------------------------------------------
# 高层操作函数
# ---------------------------------------------------------------------------


def click_first_visible(page: Page, selector_set: SelectorSet, timeout_ms: int = 3_000) -> bool:
    """点击第一个当前可见的元素（即时快照）。返回是否成功。"""
    loc = selector_set.first_visible(page, timeout_ms=timeout_ms)
    if loc is None:
        return False
    try:
        loc.click()
        return True
    except Exception as exc:
        emit(selector_set.name, f"click failed: {exc}")
        return False


def click_wait_first(page: Page, selector_set: SelectorSet, timeout_ms: int = 8_000) -> bool:
    """等待元素出现（进入 DOM 且可见）后点击。返回是否成功。

    用于\"必然出现\"的元素（创建按钮、弹窗确定、保存按钮等），
    避免 SPA 渲染未完成时误判为不存在。
    """
    loc = selector_set.wait_first(page, timeout_ms=timeout_ms)
    if loc is None:
        return False
    try:
        loc.click()
        return True
    except Exception as exc:
        emit(selector_set.name, f"click failed: {exc}")
        return False


def extract_api_key_from_dialog(page: Page) -> str:
    """从 API Key 展示弹窗中提取完整 Key。"""
    # 策略 1：只读 input/textarea（华为云 copy-key-modal 的 key 输入框）
    for selector in (
        ".copy-key-modal-form-input",
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

    # 策略 2：evaluate 遍历弹窗 DOM（Ti3 / copy-key-modal）
    try:
        key = page.evaluate(
            """
            () => {
                const dialog = document.querySelector(
                    'copy-key-modal, .copy-key-modal-form-input, '
                    + '.ti3-modal, [role="dialog"]'
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


def click_copy_key_button(page: Page) -> bool:
    """best-effort 点击 Key 展示弹窗中的复制按钮。失败不影响流程。"""
    try:
        loc = page.locator(f"#{COPY_KEY_BUTTON_ID}").first
        loc.wait_for(state="visible", timeout=3_000)
        loc.click()
        return True
    except Exception:
        return False
