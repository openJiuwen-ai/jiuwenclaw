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
  - ask_user_question
version: 3.0.0
---

# 华为云 MaaS 一键配置（V2 折中方案）

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
| 1a | 跳转到充值页 | **充值**（建议几元避免欠费） | `navigate.py` |
| 2 | 跳转 + 自动授权 | - | `navigate.py` + `auto_authorize.py` |
| 3 | 跳转 + 自动创建 Key | - | `navigate.py` + `auto_create_apikey.py` |
| 4 | 跳转 + **自动批量开通模型** | - | `navigate.py` + `auto_open_model.py` |
| 5 | 展示配置 + 最终确认 | **确认** | `ask_user_question` |
| 6 | 写入配置（追加，不设默认） | - | `config_writer.py add` |

> 步骤 1a 在登录后主动引导充值，避免后续创建 Key 时欠费失败。
> 步骤 3 如果仍检测到欠费，复用步骤 1a 的充值引导。

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
- `--cdp-url <URL>` — CDP endpoint，留空自动从 profiles.json 解析
- `--json` — JSON 格式输出

### 失败处理统一约定

- 任意脚本返回 `{"ok": false, "stage": "...", "error": "..."}` 时
  - 通过 `ask_user_question` 提示用户手动完成该步骤
  - 用户手动完成后，**单独重试该步骤的脚本**（不重头开始）
  - 步骤 3（Key）失败时，用户可手动粘贴 Key，仍走步骤 6-7

## 步骤 0：确保浏览器已启动

```bash
python <skill_dir>/scripts/ensure_browser.py --json
```

期望输出：
```json
{"ok": true, "stage": "browser_started" | "browser_ready",
 "cdp_url": "http://127.0.0.1:9333", "browser_family": "edge", "started_now": true}
```

若 `ok=false, stage=no_browser` → 提示用户安装 Edge/Chrome/Chromium 后重试。

## 步骤 1：跳转到控制台首页 + 等待登录

```bash
python <skill_dir>/scripts/navigate.py --json --cdp-url "<CDP_URL>" \
  --url "https://console.huaweicloud.com/modelarts/?region=cn-southwest-2#/model-studio/homepage"
```

`ask_user_question`：

```
华为云控制台已在浏览器中打开。请完成以下操作后点击「我已登录」：

1. 【登录】用您的华为云账号登录
   - 首次使用需先注册：https://reg.huaweicloud.com/
   - 注册后需完成实名认证
2. 【充值】如提示余额不足，请先充值
   （MaaS 按调用量计费，建议 ≥ 10 元）
3. 完成后页面应显示控制台首页，右上角能看到您的账号

请选择：
  ○ 我已登录，准备好继续
  ○ 遇到欠费/未实名，我先去处理
  ○ 取消配置
```

**关键设计**：让用户自己判断是否欠费/未实名，避免脚本去检测脆弱的 DOM。

## 步骤 1a：跳转到充值页 + 引导充值

用户确认登录后，**主动**跳转到充值页面，提醒用户充值几块钱避免后续欠费：

```bash
python <skill_dir>/scripts/navigate.py --json --cdp-url "<CDP_URL>" \
  --url "https://account.huaweicloud.com/usercenter/#/accountindex/balance"
```

`ask_user_question`：

```
华为云充值页面已在浏览器中打开。

为了避免后续创建 API Key 和开通模型时因余额不足而失败，
建议您现在充值几块钱（MaaS 按调用量计费，不使用不产生费用）。

充值方式：微信 / 支付宝 / 银行转账
建议金额：≥ 5 元即可

请选择：
  ○ 我已完成充值（或已有余额），继续
  ○ 跳过充值，稍后再说
  ○ 取消配置
```

> **设计目的**： proactive 充值引导，避免步骤 3 创建 Key 时才发现欠费。
> 步骤 3 如果仍检测到 `stage=insufficient_balance`，复用此步骤的充值引导。

## 步骤 2：自动完成委托授权

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

**失败降级**（如选择器失效）：`ok=false` → 提示用户手动完成委托授权（参考
[华为云 MaaS 访问授权文档](https://support.huaweicloud.com/permission-maas/maas-modelarts-0016.html)），
然后重试 `auto_authorize.py`。

## 步骤 3：自动创建 API Key（含欠费检测）

```bash
python <skill_dir>/scripts/navigate.py --json --cdp-url "<CDP_URL>" \
  --url "https://console.huaweicloud.com/modelarts/?region=cn-southwest-2#/model-studio/authmanage"

python <skill_dir>/scripts/auto_create_apikey.py --json --cdp-url "<CDP_URL>" \
  --tag "jiuwenswarm" \
  --description "jiuwenswarm-config"
```

脚本内部流程：
1. 导航到 API Key 管理页
2. 点击"创建 API Key"按钮
3. **填写标签**（`jiuwenswarm`，1-100 字符，支持大小写字母/数字/下划线/中划线）
4. **填写描述**（`jiuwenswarm-config`）
5. **权限设置保持默认**（不主动操作）
6. 点击"确定"提交
7. **欠费检测**：如出现"欠费"/"余额不足"提示 -> 返回 `stage=insufficient_balance`
8. 等待 Key 展示弹窗 -> 三级策略提取完整 Key

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

### 步骤 3 欠费处理（复用步骤 1a）

当 `auto_create_apikey.py` 返回 `stage=insufficient_balance` 时：

1. 跳转到充值页面（同步骤 1a）：

```bash
python <skill_dir>/scripts/navigate.py --json --cdp-url "<CDP_URL>" \
  --url "https://account.huaweicloud.com/usercenter/#/accountindex/balance"
```

2. 通过 `ask_user_question` 提醒用户充值（同步骤 1a 文案）

3. 用户确认充值完成后，重新执行步骤 3（`auto_create_apikey.py`）

**Key 提取三级策略**（脚本内部）：
1. `input[readonly]` / `textarea[readonly]`
2. `.el-dialog code` / `.el-dialog pre` 元素
3. 正则匹配弹窗内文本（`[A-Za-z0-9_\-]{30,}`）

**其他失败降级**（提取失败）：`ok=false, stage=extract_failed` -> 提示用户
手动在浏览器中创建 API Key 并通过 ask_user_question 粘贴完整值（用户提供
Key 后仍走步骤 5-6）。

## 步骤 4：自动批量开通热门模型

```bash
python <skill_dir>/scripts/navigate.py --json --cdp-url "<CDP_URL>" \
  --url "https://console.huaweicloud.com/modelarts/?region=cn-southwest-2#/model-studio/deployment"

python <skill_dir>/scripts/auto_open_model.py --json --cdp-url "<CDP_URL>" \
  --model "openPangu-2.0-Pro=openpangu-2.0-pro" \
  --model "GLM-5.2=glm-5.2" \
  --model "DeepSeek-V4-Flash=deepseek-v4-flash" \
  --timeout 60
```

**期望输出**：
```json
{
  "ok": true,
  "stage": "open_models",
  "opened": ["openpangu-2.0-pro", "glm-5.2", "deepseek-v4-flash"],
  "already_opened": [],
  "failed": [],
  "all_done": true
}
```

脚本行为：
- 导航到预置服务页
- 对每个模型：检查状态 -> 未开通则自动开通 -> 等待状态变"开通"
- 已开通的自动跳过
- 单个模型失败不影响其他模型

**失败降级**：`failed` 列表不为空时，提示用户哪些模型开通失败，
用户可手动在浏览器中开通，或跳过失败的模型继续后续步骤。

## 步骤 5：展示配置 + 最终确认

`ask_user_question`：

```
即将为您添加以下模型（仅追加，不修改当前默认模型）：

  API 地址：https://api.modelarts-maas.com/openai/v1
  API Key： ABh8****rQfg（前 4 后 4，中间已隐藏）
  新增模型：
    • openPangu-2.0-Pro（别名：huawei-pangu）
    • GLM-5.2（别名：huawei-glm）
    • DeepSeek-V4-Flash（别名：huawei-deepseek）

原有模型配置和默认模型保持不变。
您可在「设置 -> 模型」中随时切换默认模型。

请选择：
  ○ 确认写入
  ○ 取消（保留云端资源，不写入配置）
```

> **设计变更**：不再将新模型设为默认，仅追加到配置列表。
> 用户原有默认模型不受影响。

请选择：
  ○ 确认写入
  ○ 取消（保留云端资源，不写入配置）
```

## 步骤 6：写入配置

```bash
python <skill_dir>/scripts/config_writer.py add --json \
  --api-base "https://api.modelarts-maas.com/openai/v1" \
  --api-key "<步骤 3 提取的完整 API Key>" \
  --model "name=openpangu-2.0-pro,alias=huawei-pangu" \
  --model "name=glm-5.2,alias=huawei-glm" \
  --model "name=deepseek-v4-flash,alias=huawei-deepseek"
```

**期望输出**：
```json
{
  "env_path": "C:\\Users\\xxx\\.jiuwenswarm\\config\\.env",
  "written_aliases": ["huawei-pangu", "huawei-glm", "huawei-deepseek"],
  "api_base": "https://api.modelarts-maas.com/openai/v1",
  "models": ["openpangu-2.0-pro", "glm-5.2", "deepseek-v4-flash"]
}
```

`config_writer.py` 内部：
- `.env` 用 `HUAWEI_MAAS_API_BASE` / `HUAWEI_MAAS_API_KEY` 前缀（不覆盖用户已有 `API_BASE` / `API_KEY`）
- `config.yaml` 按 alias 追加/更新条目，**不修改任何 `is_default`**
- 旧模型条目和默认模型保持不变，用户可在「设置 -> 模型」中手动切换

> 写入后需重启 jiuwenswarm 或在「设置 -> 模型」中手动刷新配置生效。

## 步骤 7：完成

向用户发送（用自然语言，不要用代码块包裹）：

```
✅ 华为云 MaaS 配置完成！

已为您完成：
  • 已开通模型：openPangu-2.0-Pro、GLM-5.2、DeepSeek-V4-Flash
  • 已将以上模型追加到您的模型列表（未修改默认模型）
  • 原有模型配置和默认模型保持不变

如需切换默认模型，可前往「设置 -> 模型」。
配置写入后需重启 jiuwenswarm 生效。
```

## 降级策略（任意步骤失败时）

1. 通过 `ask_user_question` 提示用户在浏览器中手动完成对应步骤
2. 用户手动完成后，**单独重试该步骤的脚本**（不重头开始）
3. 步骤 3（Key）欠费时 -> 复用步骤 1a 充值引导 -> 重新执行步骤 3
4. 步骤 3（Key）提取失败时，用户可手动粘贴 Key，仍走步骤 5-6
5. 步骤 4（模型开通）部分失败时，跳过失败模型，仅写入成功的模型
6. 任意步骤可让用户"完全手动配置"，引导至「设置 -> 模型」手动填入

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
| Key 怎么拿 | **脚本自动提取** | 三级策略保证可靠；欠费时复用充值引导 |
| 充值引导 | **登录后主动提醒** | proactive 避免后续欠费失败 |
| 开通哪些模型 | **脚本自动批量开通** | 固定热门列表（openPangu/GLM/DeepSeek） |
| 设不设默认 | **不设默认** | 仅追加到列表，用户原有默认不变 |
| 登录 | **用户做** | 涉及账号、验证码、反爬 |
| 最终确认 | **用户做** | 写入不可逆 + 脱敏 Key 展示 |
