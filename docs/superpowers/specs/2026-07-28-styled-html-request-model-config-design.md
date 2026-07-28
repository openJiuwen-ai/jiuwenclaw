# Styled HTML 请求级模型配置修复设计

## 背景

`deepresearch_generate_rewrite_html` 复用 `_generate_report_html` 调用
DeepSearch `stylize_report`。当前 `_build_styled_export_llm_config()` 通过
`_build_bridge_env(os.environ)` 读取模型配置，绕过 AgentServer 为当前
service/agent/request 绑定的配置 overlay。共享 AgentServer 启动时的静态配置
可能仍是空模型和 `example.com` 占位地址，因此主 Agent 使用请求级模型成功，
而 HTML 美化调用失败并执行 10、20、40 秒重试。

## 目标

- HTML 美化复用当前请求或租户已经解析好的默认模型配置。
- 不在 `deepresearch_generate_rewrite_html` 工具参数中传递模型凭证。
- 配置缺失或仍为占位地址时，在创建模型前失败并进入现有离线 HTML 兜底。
- 不改变 Markdown 改写、HTML 文件交付和 DeepResearch 子进程的既有语义。

## 设计

### 配置读取

`_build_styled_export_llm_config()` 改为调用无显式 `env` 参数的
`load_deepresearch_config()`。该路径通过 `get_local_config()` 按顺序读取：

1. 当前请求绑定的 task overlay；
2. 当前 service/agent 的 active tip；
3. namespaced process environment。

保留现有 provider 到 DeepSearch `model_type` 的映射以及
`thinking=disabled`、`verify_ssl=False` 设置。

DeepResearch 子进程继续使用 `_build_bridge_env(os_env)`，不修改其显式环境快照
和搜索凭据桥接行为。

### 快速失败与兜底

在创建 report-style LLM 之前校验 `model_name`、`api_key`、`base_url` 均非空，
且 `base_url` 不包含 `example.com`。校验失败抛出不包含凭据的配置错误。

`_generate_report_html()` 已捕获该错误并调用 `convert_md_to_html`，因此无需修改
工具协议或文件交付流程。快速失败发生在模型客户端和重试器之前，不再产生
10、20、40 秒等待。

### TLS 配置

report-style 的临时 TLS 环境同样从 overlay-aware 配置读取；缺省保持
`LLM_SSL_VERIFY=false`。子进程 TLS 逻辑不变。

## 测试

- 请求 overlay 与 `os.environ` 冲突时，styled config 必须选择 overlay 中的
  模型、地址和凭证。
- overlay provider 继续按现有规则映射为 report-style 支持的类型。
- 空模型、空地址、空凭证和 `example.com` 地址必须在模型调用前失败。
- 无效配置触发 `_generate_report_html()` 的现有离线转换，且不会初始化
  report-style LLM。
- 运行 DeepResearch stream、rewrite HTML 和 task manager 相关测试，确认子进程
  bridge 与文件交付行为不变。

## 非目标

- 不修改 OfficeClaw 到 AgentServer 的 reload 协议。
- 不新增前端或工具参数。
- 不修改用户模型选择规则。
- 不提交或记录任何明文凭据。
