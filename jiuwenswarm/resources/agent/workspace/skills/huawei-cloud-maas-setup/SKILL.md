---
name: huawei-cloud-maas-setup
description: >-
  引导用户完成华为云 MaaS 服务购买和 API 配置。脚本自动完成授权更新、API Key
  创建、模型开通；用户只需完成登录、选择要开通的模型。整个流程预计 2-3 分钟。
  适用于首次运行配置、华为云 MaaS 服务开通、API 凭证获取、模型服务配置等场景。
  当用户提到华为云、MaaS、Maas、开通模型服务、配置 API、首次配置、引导配置、
  一键配置等意图时触发。
allowed_tools:
  - bash
  - ask_user
version: 3.1.0
---

# 华为云 MaaS 一键配置

> **设计原则**：脚本做自动化（授权、Key、模型开通），用户做决策（登录、
> 选哪个模型、最终确认）。整个流程预计 **2-3 分钟**。
>
> - **脚本自动**：跳转 URL、自动检测授权、自动创建 Key 并提取、自动开通模型
> - **用户决策**：登录（涉及账号）、选择要开通的模型（业务决策）、最终确认
> - **价值**：规避 DOM 选择器脆弱性、避开 LLM ReAct 慢路径、保留用户掌控感

## 流程概览

| 步骤 | Skill 动作 | 用户动作 | 脚本 |
|------|------------|----------|------|
| 0 | 确保浏览器已启动 | - | `ensure_browser.py` |
| 1 | 跳转到控制台首页 | **登录** | `navigate.py` |
| 2 | 跳转到充值页 | **充值**（建议几元避免欠费） | `navigate.py` |
| 3 | 跳转 + 自动授权 | - | `navigate.py` + `auto_authorize.py` |
| 4 | 跳转 + 自动创建 Key | - | `navigate.py` + `auto_create_apikey.py` |
| 5 | 跳转 + **自动批量开通模型** | - | `navigate.py` + `auto_open_model.py` |
| 6 | 写入配置（直接执行） | - | `config_writer.py add` |
| 7 | 设置引导完成标志 | - | Python 命令 |
| 8 | 完成提示 | - | 自然语言 |

> 步骤 2 在登录后主动引导充值，避免后续创建 Key 时欠费失败。
> 步骤 4 如果仍检测到欠费，复用步骤 2 的充值引导。

## 公共说明

### 脚本位置

所有脚本位于 `<skill_dir>/scripts/`：

- `navigate.py` - 通用 URL 跳转
- `ensure_browser.py` - 启动浏览器
- `auto_authorize.py` - 自动完成委托授权
- `auto_create_apikey.py` - 自动创建 API Key（含欠费检测）
- `auto_open_model.py` - 自动批量开通预置模型
- `config_writer.py` - 写入配置（追加，不设默认）

### 公共参数

所有脚本支持：
- `--cdp-url <URL>` - CDP endpoint，留空自动从 profiles.json 解析
- `--json` - JSON 格式输出

### 模型列表配置

模型列表统一定义在 `<skill_dir>/models.json` 中，**修改该文件即可调整开通和写入的模型**：

```json
{
  "models": [
    {"display_name": "Qwen3-30B-A3B", "api_name": "qwen3-30b-a3b", "alias": "huawei-qwen", "description": "主力对话模型"},
    {"display_name": "GLM-5.2", "api_name": "glm-5.2", "alias": "huawei-glm", "description": "备选对话模型"},
    {"display_name": "DeepSeek-V4-Flash", "api_name": "deepseek-v4-flash", "alias": "huawei-deepseek", "description": "快速推理模型"}
  ]
}
```

| 字段 | 用途 | 用于步骤 |
|------|------|----------|
| `display_name` | 控制台显示名称，用于勾选开通 | 步骤 5 |
| `api_name` | API 调用标识，写入 config.yaml | 步骤 6 |
| `alias` | jiuwenswarm 中的模型别名 | 步骤 6 |
| `description` | 模型描述，用于完成提示 | 步骤 8 |

步骤 5 和步骤 6 默认从 `models.json` 读取，也支持 `--model` 参数逐个指定（优先级更高）。

### 失败与降级策略

- 任意脚本返回 `{"ok": false, "stage": "...", "error": "..."}` 时：
  - 通过 `ask_user` 提示用户在浏览器中手动完成对应步骤（遵循「窗口切换提示约定」）
  - 用户手动完成后，**单独重试该步骤的脚本**（不重头开始）
- 步骤 4（Key）欠费时 -> 复用步骤 2 充值引导 -> 重新执行步骤 4
- 步骤 4（Key）提取失败时，用户可手动粘贴 Key，仍走步骤 6-7
- 步骤 5（模型开通）部分失败时，跳过失败模型，仅写入成功的模型
- 任意步骤可让用户"完全手动配置"，引导至「设置 -> 模型」手动填入

### 窗口切换提示约定

当步骤需要用户在浏览器中操作时，**必须**遵循三层模式：

1. **ask_user 之前**输出加粗正文，且**与 `ask_user` 调用同轮发出**（先输出正文作为本条回复内容，
   再在同一回复内调用 `ask_user`，禁止分两轮：先发纯文本正文、等下一轮推理再调用）：
   > **浏览器已在后台打开了一个新窗口。请切换到浏览器窗口完成 XXX，完成后回到此窗口继续。**

2. **ask_user prompt** 使用统一视觉标记：
   ```
   🔔 浏览器已打开 XXX 页面，请在浏览器中完成以下操作。

   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   ▶ 切换到浏览器窗口，完成：
      1. 具体操作步骤...
      2. ...
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   ◀ 完成后回到此会话窗口，选择下方选项继续
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   请选择：
     ○ ✅ 已在浏览器完成 XXX，回到会话继续
     ○ ⚠️ 遇到问题，我先去浏览器处理
     ○ 取消配置
   ```

3. **选项文案**：已完成类加 `✅` 前缀，需处理类加 `⚠️` 前缀。

### 正文输出约定（避免把正文切碎）

> **背景**：前端会把连续的「思考+工具」折叠成一条"已完成 X 次思考，X 次工具调用"的折叠条，
> 且**每发一段 assistant 正文都会触发一次折叠**，在两段正文之间冒出一条折叠条。若每跑一个
> 脚本都夹一段进度正文，正文会被这些折叠条切成碎片。

1. **连续自动调用之间不夹进度正文**：步骤 0、3、4、5、6、7 这类纯自动脚本调用，**禁止**在每次
   调用前都输出"▶ 步骤 N/8：..."进度正文。开头说一句开场白即可，然后**连续发起所有自动脚本
   调用**（允许同轮并列多个 `bash`，或前一脚本返回后立即调用下一脚本、中间不补正文），让它们
   合并进同一个折叠段。一旦夹了进度正文，就会触发折叠，把正文打成一段段。
2. **仅在这些时机发正文**：
   - 需要 `ask_user` 用户决策时（步骤 1 登录、步骤 2 充值、步骤 4 欠费/提取失败降级）：正文与
     `ask_user` **必须同轮发出**（先写正文，再在同一回复内调用 `ask_user`，禁止分两轮）。
   - 失败降级提示时，按「窗口切换提示约定」用 `ask_user` 一并呈现（正文与 `ask_user` 同轮）。
   - 步骤 8 完成提示（用自然语言收尾）。
3. **结果摘要并入下一步，不单开一轮**：脚本结果正常时，不要为单个脚本单独发一条"✅ 结果摘要"
   正文；如有必要展示，并入下一个 `ask_user` 正文或步骤 8 的完成提示。仅当失败降级或需用户
   决策时才单独发正文（且与 `ask_user` 同轮）。

## 步骤 0：确保浏览器已启动

```bash
python <skill_dir>/scripts/ensure_browser.py --json
```

期望输出：
```json
{"ok": true, "stage": "browser_started" | "browser_ready",
 "cdp_url": "http://127.0.0.1:9333", "browser_family": "edge", "started_now": true}
```

若 `ok=false, stage=no_browser` -> 提示用户安装 Edge/Chrome/Chromium 后重试。

## 步骤 1：跳转到控制台首页 + 等待登录

```bash
python <skill_dir>/scripts/navigate.py --json --cdp-url "<CDP_URL>" \
  --url "https://console.huaweicloud.com/modelarts/?region=cn-southwest-2#/model-studio/homepage"
```

先输出加粗正文，再 `ask_user`：

> **浏览器已在后台打开了一个新窗口。请切换到浏览器窗口完成登录，完成后回到此窗口继续。**

```
🔔 浏览器已打开华为云控制台，请在浏览器中完成以下操作。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 切换到浏览器窗口，完成：
   1. 【登录】用您的华为云账号登录
      - 首次使用需先注册：https://reg.huaweicloud.com/
      - 注册后需完成实名认证
   2. 完成后页面应显示控制台首页，右上角能看到您的账号
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
◀ 完成后回到此会话窗口，选择下方选项继续
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请选择：
  ○ ✅ 已在浏览器完成登录，回到会话继续
  ○ 取消配置
```

**关键设计**：让用户自己判断是否欠费/未实名，避免脚本去检测脆弱的 DOM。

## 步骤 2：跳转到充值页 + 引导充值

用户确认登录后，**主动**跳转到充值页面，提醒用户充值几块钱避免后续欠费：

```bash
python <skill_dir>/scripts/navigate.py --json --cdp-url "<CDP_URL>" \
  --url "https://account.huaweicloud.com/usercenter/#/accountindex/balance"
```

先输出加粗正文，再 `ask_user`：

> **浏览器已打开了充值页面。请切换到浏览器窗口完成充值，完成后回到此窗口继续。**

```
🔔 充值页面已在浏览器中打开，请在浏览器中完成充值。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▶ 切换到浏览器窗口，完成：
   - 充值方式：微信 / 支付宝 / 银行转账
   - 建议金额：≥ 5 元即可
   - MaaS 按调用量计费，不使用不产生费用
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
◀ 完成后回到此会话窗口，选择下方选项继续
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请选择：
  ○ ✅ 已完成充值（或已有余额），回到会话继续
  ○ 跳过充值，稍后再说
  ○ 取消配置
```

> **设计目的**：proactive 充值引导，避免步骤 4 创建 Key 时才发现欠费。

## 步骤 3：自动完成委托授权

```bash
python <skill_dir>/scripts/navigate.py --json --cdp-url "<CDP_URL>" \
  --url "https://console.huaweicloud.com/modelarts/?region=cn-southwest-2#/model-studio/homepage"

python <skill_dir>/scripts/auto_authorize.py --json --cdp-url "<CDP_URL>"
```

**期望输出**（无警告，已授权）：
```json
{"ok": true, "stage": "authorize", "auth_done": false, "skipped_reason": "no_warning"}
```

**期望输出**（成功更新）：
```json
{"ok": true, "stage": "authorize", "auth_done": true, "skipped_reason": null}
```

**失败降级**（如选择器失效）：`ok=false` -> 提示用户手动完成委托授权（参考
[华为云 MaaS 访问授权文档](https://support.huaweicloud.com/permission-maas/maas-modelarts-0016.html)），
然后重试 `auto_authorize.py`。

## 步骤 4：自动创建 API Key（含欠费检测）

> ⚠️ **串行约束**：下面两个脚本必须按顺序串行执行——先跑 `navigate.py` 并等待其
> stdout 返回 `{"ok": true}` 后再执行 `auto_create_apikey.py`。**严禁并行调用**：
> 两者会对同一浏览器同一 page 做 `goto`（加载竞态），SPA 尚未渲染完成时按钮会被误判为
> 找不到（`create_btn_not_found`），从而错误触发"手动创建"降级。

```bash
python <skill_dir>/scripts/navigate.py --json --cdp-url "<CDP_URL>" \
  --url "https://console.huaweicloud.com/modelarts/?region=cn-southwest-2#/model-studio/authmanage"

python <skill_dir>/scripts/auto_create_apikey.py --json --cdp-url "<CDP_URL>" \
  --tag "jiuwenswarm" \
  --description "jiuwenswarm-config"
```

**期望输出**（成功）：
```json
{
  "ok": true,
  "stage": "apikey",
  "api_key": "ABh8zqX4...完整Key...rQfg",
  "tag": "jiuwenswarm",
  "description": "jiuwenswarm-config"
}
```

**欠费时输出**：
```json
{
  "ok": false,
  "stage": "insufficient_balance",
  "error": "账户余额不足，请先充值后再创建 API Key",
  "recharge_url": "https://account.huaweicloud.com/usercenter/#/accountindex/balance"
}
```

### 步骤 4 欠费处理

当返回 `stage=insufficient_balance` 时，复用**步骤 2 的充值引导**（跳转充值页 + ask_user），
用户确认充值后重新执行步骤 4。

### 步骤 4 提取失败

当 `stage=extract_failed` 时，提示用户手动在浏览器中创建 API Key 并通过 ask_user 粘贴完整值
（用户提供 Key 后仍走步骤 6-7）。

## 步骤 5：自动批量开通热门模型

```bash
python <skill_dir>/scripts/navigate.py --json --cdp-url "<CDP_URL>" \
  --url "https://console.huaweicloud.com/modelarts/?region=cn-southwest-2#/model-studio/deployment"

python <skill_dir>/scripts/auto_open_model.py --json --cdp-url "<CDP_URL>" \
  --models-file "<skill_dir>/models.json" \
  --timeout 90
```

**流程说明**：脚本会先在预置服务列表的"类型"列中确认每个目标模型是
【文本生成】，然后点击该行**右侧的"开通服务"**（逐个模型开通），
在弹窗中勾选同意声明并点击"一键开通"。已开通/开通中的模型自动跳过。

**期望输出**：
```json
{
  "ok": true,
  "stage": "open_models",
  "models": ["GLM-5.2", "Kimi-K2.6", "DeepSeek-V4-Flash"],
  "opened": ["GLM-5.2", "DeepSeek-V4-Flash"],
  "already_opened": ["Kimi-K2.6"],
  "failed": [],
  "all_done": true
}
```

> 模型名称以 `models.json` 为准，此处仅为示例。
> 仅开通**文本生成**类型的服务；可通过 `--type-filter` 参数调整类型。

**失败降级**：`failed` 列表不为空时，提示用户哪些模型开通失败（未找到、
类型不符、按钮/弹窗异常等），用户可手动在浏览器中开通，或跳过失败的模型继续后续步骤。

## 步骤 6：写入配置（直接执行，无需确认）

```bash
python <skill_dir>/scripts/config_writer.py add --json \
  --api-base "https://api.modelarts-maas.com/openai/v1" \
  --api-key "<步骤 4 提取的完整 API Key>" \
  --models-file "<skill_dir>/models.json"
```

**期望输出**：
```json
{
  "env_path": "C:\\Users\\xxx\\.jiuwenswarm\\config\\.env",
  "written_aliases": ["huawei-qwen", "huawei-glm", "huawei-deepseek"],
  "api_base": "https://api.modelarts-maas.com/openai/v1",
  "models": ["qwen3-30b-a3b", "glm-5.2", "deepseek-v4-flash"]
}
```

> `.env` 使用 `HUAWEI_MAAS_` 前缀隔离，不覆盖用户已有配置。
> `config.yaml` 按 alias 追加/更新，**不修改任何 `is_default`**。

## 步骤 7：设置引导完成标志

```bash
python -c "import yaml; from pathlib import Path; p = Path.home() / '.jiuwenswarm' / 'config' / 'config.yaml'; c = yaml.safe_load(p.read_text(encoding='utf-8')); c.setdefault('setup_guide', {})['enabled'] = False; p.write_text(yaml.dump(c, allow_unicode=True, default_flow_style=False), encoding='utf-8'); print('setup_guide.enabled = false')"
```

> 设置 `setup_guide.enabled = false`，下次启动不再弹出引导界面。

## 步骤 8：完成

向用户发送（用自然语言，不要用代码块包裹）：

```
🎉 华为云 MaaS 服务配置完成！

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 配置摘要

  ✅ API 连接：已配置
  ✅ 模型服务：已开通以下模型
     • Qwen3-30B-A3B（主力对话模型）
     • GLM-5.2（备选对话模型）
     • DeepSeek-V4-Flash（快速推理模型）
  ✅ 配置写入：已保存到本地

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️ 重要提示

1. 重启生效
   配置写入后需要重启 jiuwenswarm 才能生效。
   重启后即可在对话中选择新加入的华为云模型。

2. 原有配置保持不变
   您的原有模型配置和默认模型设置完全保留，未做任何修改。
   如需切换默认模型，可前往「设置 -> 模型」进行调整。

3. 模型服务状态
   以上模型已在华为云控制台完成开通。
   如需开通更多模型，请前往：
   华为云控制台 -> ModelArts -> 在线推理 -> 预置服务

4. 计费说明
   华为云 MaaS 按实际调用量计费，不使用不产生费用。
   建议保持账户余额充足，避免欠费导致服务中断。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🚀 下一步

请重启 jiuwenswarm，然后在对话中选择华为云模型开始使用！

如有问题，可随时询问我。
```

## 浏览器零配置

`ensure_browser.py` 内部自动检测浏览器（按 OS 优先级）：

- **Windows**: Edge（系统自带）> Chrome > Chromium
- **macOS**: Chrome > Edge > Chromium
- **Linux**: Chrome > Chromium > Edge

首次启动自动写入 `~/.jiuwenswarm/.browser/profiles.json`，
后续启动直接复用。

## 关键设计取舍

| 决策 | 选择 | 理由 |
|------|------|------|
| 授权做不做 | **脚本自动** | DOM 固定，确定性高，5-10s 可完成 |
| Key 怎么拿 | **脚本自动提取** | 多级策略保证可靠；欠费时复用充值引导 |
| 充值引导 | **登录后主动提醒** | proactive 避免后续欠费失败 |
| 开通哪些模型 | **脚本自动批量开通** | 列表定义在 models.json，可随时调整 |
| 设不设默认 | **不设默认** | 仅追加到列表，用户原有默认不变 |
| 登录 | **用户做** | 涉及账号、验证码、反爬 |
| 最终确认 | **用户做** | 写入不可逆 + 脱敏 Key 展示 |
