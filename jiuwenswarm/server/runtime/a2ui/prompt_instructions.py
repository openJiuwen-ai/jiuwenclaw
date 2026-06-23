# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Request-scoped A2UI prompt rail instructions."""

from __future__ import annotations


def build_a2ui_autonomy_instruction(language: str = "en") -> str:
    browser_preflight_rule_en = (
        " Browser task preflight: when the user asks you to perform a browser "
        "automation task such as booking tickets, reserving hotels, buying "
        "products, filling forms, or comparing purchasable options, first check "
        "whether the task has enough user-confirmed details before calling any "
        "browser tool or browser subagent. If required details are missing and "
        "the Web A2UI channel is available, do not start browser automation yet. "
        "Instead, render an A2UI information-collection or confirmation form. "
        "Do not ask for those missing browser-task details through plain natural "
        "language, Markdown, or the ask_user tool when A2UI is available. "
        "The submit Button action name MUST be 'browser_preflight_submit'. Its "
        "action.context MUST include original_query, task_type, next_action with "
        "the value 'run_browser_agent', must_confirm_before_payment with true, "
        "and all form values using path references. After this action is "
        "submitted, combine the original request and submitted values, then use "
        "spawn_sub_agent with subagent_type 'browser_agent'. Never buy, book, "
        "pay, or place an order without a final explicit user confirmation."
    )
    hotel_booking_flow_rule_en = (
        " Hotel booking A2UI flow: after browser_agent returns candidate hotels, "
        "present the candidates as A2UI cards or a comparable list. Each hotel's "
        "selection Button action name MUST be 'hotel_option_select'. Its "
        "action.context MUST include task_type='hotel', next_action with the "
        "value 'continue_hotel_booking', original_query, selected hotel name, "
        "candidate index or id, and the confirmed city/check-in/check-out/guest "
        "and room criteria. Include a candidate/detail URL, provider, room type, "
        "price, currency, and cancellation policy when available. After the user "
        "selects a hotel, continue the existing browser state for that candidate; "
        "do not restart the broad city/date search. At the payment or order "
        "summary page, render a final A2UI confirmation whose confirm Button "
        "action name is 'hotel_payment_confirm' and whose cancel Button action "
        "name is 'hotel_payment_cancel'."
    )
    template_binding_rule_en = (
        " For repeated list/card data, use A2UI template binding correctly: "
        "Duplicate dataModelUpdate keys are invalid. Encode arrays as one "
        'collection key with indexed valueMap entries such as "0", "1", where '
        "each item contains its own nested valueMap fields. Inside template "
        "components, use item-relative paths like 'name', 'price', or "
        "'/item/name' for Text, Image, and Button.action.context values; do not "
        "use collection-absolute paths such as '/phones/name' inside templates. "
        "Do not nest templates inside template-rendered components in A2UI 0.8; "
        "flatten repeated item details into fields on the outer item, or use "
        "explicit child components that bind to those fields."
    )
    image_url_rule_en = (
        " Do not invent image URLs. If external facts or images are needed, use "
        "the available tools briefly, then converge to the final A2UI response. "
        "Use a user-provided HTTPS URL or a verified stable source URL; do not "
        "use guessed upload.wikimedia.org thumbnail paths."
    )
    icon_font_rule_en = (
        " The host app may not have the Material Symbols icon font available. "
        "Avoid A2UI Icon for semantic content such as product or status icons; "
        "use Text literalString emoji or text labels instead so ligature fallback "
        "text does not appear."
    )
    unsupported_component_rule_en = (
        " A2UI 0.8 does not support modal, dialog, popup, alert, toast, "
        "floating overlay, or closeable window components. Do not simulate these "
        "with absolute-positioned cards or fake close buttons. If the request can "
        "be approximated, use an inline status, inline card, or confirmation area "
        "inside the normal surface. If it cannot be approximated faithfully, answer "
        "in plain text that the requested component is not currently supported."
    )
    autonomy_rule_en = (
        " A2UI is optional. Use A2UI only when a generated interface improves "
        "the user's experience over plain text. Do not force A2UI for greetings, "
        "short explanations, simple factual answers, or unstructured prose. Good "
        "A2UI candidates include information collection forms, actionable "
        "confirmations, multi-result comparison, object detail views, media-rich "
        "cards, dashboards/status/inventory/task summaries, and tool-result "
        "presentations. For real-world recommendation, comparison, shopping, "
        "ranking, price, travel, restaurant, or product requests, use tools first "
        "when available, then decide whether A2UI is the best final presentation. "
        "If the user already provided complete structured data or asks for a demo, "
        "you may render directly without tools. Never write tool_call, invoke, or "
        "function-call tags as plain text."
    )
    requested_component_rule_en = (
        " You must match the requested component type: for an input box or "
        "text field request, generate TextField/Form UI; generate a card list "
        "only when the user asks for cards or a card list. Card/list UI is "
        "not a universal fallback. For a single object detail request, build a "
        "single object detail layout, not a multi-card demo. Do not substitute a "
        "fixed demo for the requested component. For any user-editable "
        "TextField, bind TextField.text to a data model path, initialize that "
        "path with dataModelUpdate, and include the submitted value in "
        "Button.action.context using a path reference. Do not emit an empty "
        "Button.action.context for form submissions."
        + template_binding_rule_en
        + image_url_rule_en
        + icon_font_rule_en
        + unsupported_component_rule_en
    )

    template_binding_rule_zh = (
        " 使用 List 或卡片列表展示重复数据时，dataModelUpdate 的 key 不能重复。"
        "请把数组编码为一个集合 key，并在 valueMap 中使用 \"0\"、\"1\" 这类索引项；"
        "每个 item 包含自己的嵌套 valueMap 字段。模板组件和 Button.action.context "
        "内使用 item-relative path，例如 name、price 或 /item/name；"
        "不要在模板内使用 /phones/name 这类集合绝对路径。A2UI 0.8 不要在模板渲染出的"
        "组件内部再嵌套 template；请把重复 item 的明细拍平成外层 item 字段，或使用显式"
        "子组件绑定这些字段。"
    )
    image_url_rule_zh = (
        " 不要编造图片 URL。如果需要外部事实或图片，可以短暂使用可用工具，"
        "随后必须收敛到最终 A2UI 响应。使用用户提供的 HTTPS URL "
        "或已验证的稳定来源 URL；不要使用猜测出来的 upload.wikimedia.org thumbnail 路径。"
    )
    unsupported_component_rule_zh = (
        " A2UI 0.8 不支持弹窗、模态框、dialog、popup、alert、toast、浮层、"
        "悬浮覆盖层或可关闭窗口组件。不要用绝对定位卡片或假的关闭按钮模拟这些组件。"
        "如果可以近似表达，请在普通 surface 内使用行内状态、行内卡片或确认区域；"
        "如果无法忠实近似，请用纯文本说明当前暂不支持该组件。"
    )
    autonomy_rule_zh = (
        " A2UI 是可选能力。只有当生成式 UI 比纯文本更能改善用户体验时才使用 A2UI。"
        "不要为寒暄、两三句话解释、简单事实回答或无结构普通文本强行生成 A2UI。"
        "适合 A2UI 的通用场景包括：信息收集表单、可操作确认、多结果比较、"
        "单对象详情、带媒体的卡片、仪表盘/状态/库存/任务摘要，以及工具结果的交互式展示。"
        "对于真实世界推荐、对比、选购、排行、价格、旅行、餐厅或产品请求，"
        "如果有可用工具，应先使用工具获取依据，再判断 A2UI 是否是最佳最终展示方式。"
        "如果用户已经提供完整结构化数据，或明确要求演示 UI，可以直接渲染而不调用工具。"
        "绝不能把 tool_call、invoke 或函数调用标签当作普通文本输出。"
    )
    requested_component_rule_zh = (
        " 必须匹配用户请求的组件类型：如果用户要求输入框或文本框，生成 TextField/Form UI；"
        "只有用户要求卡片或卡片列表时才生成 card list。Card/list 不是万能 fallback。"
        "单个对象详情请求应生成单对象详情布局，不要生成多卡片 demo。"
        "不要用固定 demo 替代用户请求的组件。对可编辑 TextField，必须把 TextField.text "
        "绑定到 data model 路径，用 dataModelUpdate 初始化该路径，并在 Button.action.context "
        "中用 path reference 包含提交值。表单提交不能输出空的 Button.action.context。"
        + template_binding_rule_zh
        + image_url_rule_zh
        + icon_font_rule_en
        + unsupported_component_rule_zh
        + autonomy_rule_zh
        + browser_preflight_rule_en
        + hotel_booking_flow_rule_en
    )

    if language in {"zh", "cn"}:
        return (
            "A2UI 是可选能力；不要强行使用 A2UI。"
            "如果富交互界面比纯文本更适合当前回答，可以输出一段很短的说明，"
            "然后输出一个合法的 <a2ui-json>...</a2ui-json> block。"
            "如果不适合 A2UI，请直接纯文本回答。不要承诺使用 A2UI 却只输出 Markdown。"
            "如果确实需要外部事实或图片，可以短暂使用可用工具，"
            "随后自行判断是否用 A2UI 呈现。"
            "block 内必须是 A2UI 0.8 server-to-client message list，"
            "并且必须先 beginRendering，再 surfaceUpdate，再按需 dataModelUpdate。"
            + requested_component_rule_zh
        )
    return (
        "A2UI is optional. Keep tools available. If a rich interactive interface "
        "is better than plain text for this answer, output a very short intro "
        "followed by one valid <a2ui-json>...</a2ui-json> block. If A2UI is not "
        "appropriate, answer in plain text. Do not promise to show the result with "
        "A2UI and then output only Markdown. If external facts or images are needed, "
        "use the available tools briefly, then decide whether A2UI is the best "
        "presentation. The block must contain an A2UI 0.8 "
        "server-to-client message list, with beginRendering before "
        "surfaceUpdate and dataModelUpdate only when needed."
        + autonomy_rule_en
        + browser_preflight_rule_en
        + hotel_booking_flow_rule_en
        + requested_component_rule_en
    )


__all__ = [
    "build_a2ui_autonomy_instruction",
]
