---
name: huawei-cloud-maas-setup
description: >-
  引导用户通过浏览器访问华为云，购买并开通 MaaS（Model as a Service）服务，自动获取 API Base、API Key、Model Name 并填入 jiuwenswarm 配置。
  适用于首次运行配置、华为云 MaaS 服务开通、API 凭证获取、模型服务配置等场景。
  当用户提到华为云、MaaS、Maas、开通模型服务、配置 API、首次配置、引导配置、一键配置等意图时触发。
allowed_tools:
  - browser
  - write_file
  - ask_user_question
---

# 华为云 MaaS 服务引导配置

本技能引导超级小白用户完成华为云 MaaS 服务的购买和 API 配置，实现「零门槛」接入 jiuwenswarm。

## 核心流程

按以下阶段顺序执行，每个阶段通过 `ask_user_question` 与用户交互，通过 `task_tool` 委派 `browser_agent` 执行浏览器操作。

> **关键原则**：委派浏览器任务时，描述「做什么」，不描述「怎么点」。让 browser_agent 自主探测页面元素并决定操作方式。如果自动操作失败，立即降级为 `ask_user_question` 让用户手动完成。

### 阶段 1: 初始化检测

读取当前配置，检查是否已有有效的 API 配置：

```bash
python <skill_dir>/scripts/validate_config.py --check-only
```

- 如果已有有效配置：通过 `ask_user_question` 询问用户是否要重新配置
- 如果无有效配置：继续下一步

### 阶段 2: 拉起浏览器

1. 确保浏览器以**非 headless** 模式运行（用户需要看到浏览器进行登录操作）
2. 向用户发送消息：「正在为您打开浏览器，请在弹出的浏览器窗口中操作」
3. 通过 `task_tool` 委派 `browser_agent`：

   **任务描述**：「打开浏览器，导航到华为云控制台 https://console.huaweicloud.com/modelarts/#/model-studio/homepage ，等待页面加载完成。返回当前页面 URL 和标题。」

### 阶段 3: 等待用户登录

这是**关键交互点**：用户需要在浏览器中手动登录华为云。

通过 `ask_user_question` 提示用户：

```
请在弹出的浏览器窗口中登录您的华为云账号

登录前请确认：
1. 已注册华为账号并开通华为云
2. 已完成实名认证
3. 账号未处于欠费或冻结状态

登录完成后请点击「我已登录」
```

用户确认后，委派 `browser_agent` 验证登录状态：

**任务描述**：「导航到 https://console.huaweicloud.com/modelarts/#/model-studio/homepage ，探测页面元素，判断当前用户是否已登录华为云。检查页面上是否存在用户头像、用户名、或「退出登录」等元素。返回 JSON：{"logged_in": true/false, "evidence": "..."}」

- `logged_in=true` -> 进入阶段 4
- `logged_in=false` -> 提示用户重新登录，回到阶段 3

### 阶段 4: 检查 MaaS 访问授权

委派 `browser_agent`：

**任务描述**：「探测当前 MaaS 控制台页面内容，判断是否已配置 MaaS 访问授权（委托授权）。检查页面是否有授权提示、权限不足警告、或需要委托授权的引导信息。返回 JSON：{"authorized": true/false, "message": "..."}」

- **已授权**：直接进入阶段 5
- **需要授权**：
  - 通过 `ask_user_question` 引导用户：「检测到需要配置 MaaS 访问授权，请在页面中完成委托授权操作。参考文档：https://support.huaweicloud.com/permission-maas/maas-modelarts-0016.html」
  - 等待用户完成后确认，进入阶段 5

### 阶段 5: 获取 API Key

委派 `browser_agent`：

**任务描述**：「导航到 API Key 管理页面 https://console.huaweicloud.com/modelarts/#/model-studio/authmanage ，探测页面内容：
1. 检查是否已有 API Key（页面上是否显示已存在的 Key 列表）
2. 如果没有 API Key，点击「创建API Key」按钮
3. 等待 API Key 创建完成，提取新创建的 API Key 值
返回 JSON：{"api_key": "...", "has_existing": true/false}」

> **重要提示**：API Key 仅在创建时显示一次，必须在创建瞬间捕获。如果创建后页面已关闭或刷新，则需要用户重新创建。

- **提取成功**：保存 api_key，进入阶段 6
- **提取失败**：
  - 降级为 `ask_user_question`：「未能自动获取 API Key，请在 API Key 管理页面手动创建并复制 API Key，然后粘贴到下方」
  - 提醒用户：「API Key 创建后可能需要几分钟生效」

### 阶段 6: 开通预置模型服务

委派 `browser_agent`：

**任务描述**：「导航到在线推理页面 https://console.huaweicloud.com/modelarts/#/model-studio/deployment ，执行以下操作：
1. 确认在「预置服务」页签
2. 查找名为 openPangu-2.0-Pro 的模型服务
3. 检查其状态：如果已开通则直接返回；如果未开通则点击操作列的「开通服务」按钮
4. 在弹出框中勾选「我已阅读并同意上述说明，及《MaaS 模型即服务声明》」
5. 点击「一键开通」
6. 等待状态变为「开通」
返回 JSON：{"model_name": "openpangu-2.0-pro", "status": "opened", "error": null}」

> **注意**：该功能仅支持「西南-贵阳一」区域。

- **开通成功**：进入阶段 7
- **开通失败**：
  - 降级为 `ask_user_question`：「未能自动开通模型服务，请在浏览器中手动完成开通操作。确认区域为「西南-贵阳一」，在「预置服务」页签找到 openPangu-2.0-Pro 并点击「开通服务」」
  - 等待用户完成后确认

### 阶段 7: 确认凭证完整性

汇总已获取的凭证信息：

- `api_key`：从阶段 5 获取
- `api_base`：`https://api.modelarts-maas.com/openai/v1`（OpenAI 兼容接口，固定值）
- `model_name`：`openpangu-2.0-pro`（默认推荐模型）
- `model_provider`：`openai`（华为云 MaaS 兼容 OpenAI 接口）

向用户展示配置摘要（API Key 仅显示前后 4 位），通过 `ask_user_question` 确认：

```
已获取以下配置：
- API 地址: https://api.modelarts-maas.com/openai/v1
- API Key:  ****-****-abcd
- 模型名称:  openpangu-2.0-pro
- 接入方式:  OpenAI 兼容
确认后将自动写入 jiuwenswarm 配置
```

### 阶段 8: 写入配置

执行配置写入脚本：

```bash
python <skill_dir>/scripts/update_jiuwenswarm_config.py --api-base "https://api.modelarts-maas.com/openai/v1" --api-key "<api_key>" --model-name "openpangu-2.0-pro" --model-provider "openai"
```

脚本会更新 `~/.jiuwenswarm/config/.env` 和 `~/.jiuwenswarm/config/config.yaml`。

### 阶段 9: 验证配置

执行验证脚本（注意 API Key 可能需要几分钟生效）：

```bash
python <skill_dir>/scripts/validate_config.py
```

- **验证通过**：进入阶段 10
- **验证失败**：
  - 提醒用户：「API Key 刚创建可能需要几分钟生效，请稍后重试」
  - 通过 `ask_user_question` 提供选项：「稍后重试」或「先跳过验证」

### 阶段 10: 完成

1. 关闭浏览器
2. 向用户发送欢迎消息：「配置完成！您现在可以使用 jiuwenswarm 了」
3. 引导完成

## 降级策略

任何阶段如果自动操作失败：
1. 先尝试通过 `ask_user_question` 引导用户手动完成当前步骤
2. 如果用户也无法完成，引导用户跳转到 jiuwenswarm 配置面板手动填写：
   - API 地址: `https://api.modelarts-maas.com/openai/v1`
   - API Key: 用户手动输入
   - 模型名称: `openpangu-2.0-pro`
   - 接入方式: OpenAI 兼容

## 参考文档

详细信息请参阅以下参考文件：
- `references/huawei-cloud-purchase-flow.md` - 华为云购买流程详细步骤
- `references/credential-extraction.md` - API 凭证提取策略
- `references/config-mapping.md` - 凭证到 jiuwenswarm 配置的映射
