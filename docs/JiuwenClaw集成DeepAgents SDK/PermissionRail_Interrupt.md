# Permission Rail 中断恢复设计

## 一、两次 stream 交互流程

### 1.1 核心机制（无 Future）

当前实现**不使用 Future 阻塞**，而是通过**两次 `agent.stream()` 调用**实现中断恢复：

```
第一次 chat.send → 触发中断 → 流结束
    ↓
第二次 chat.send {answers} → 恢复执行 → 流输出
```

**对比老版本（Future 阻塞）**：
```
第一次 chat.send → 触发中断 → Future 阻塞
    ↓
chat.user_answer → resolve Future → 流继续输出
```

### 1.2 第一次 stream：触发中断

**Rail 流程**：
```python
async def resolve_interrupt(self, ctx, tool_call, user_input):
    if user_input is None:
        # 首次调用：Engine 完成三态判定
        result = await self._engine.check_permission(tool_name, tool_args)

        if result.is_allowed:
            return self.approve()

        if result.is_denied:
            return self.reject(tool_result=f"[DENIED] {result.reason}")

        # ASK: 构建消息并中断
        message = self._build_message(tool_name, tool_args, result)
        return self.interrupt(InterruptRequest(
            message=message,
            payload_schema=ConfirmPayload.to_schema(),
        ))
```

**输出格式转换**（`interrupt_helpers.py`）：
```python
# Rail 输出: OutputSchema(type="__interaction__", payload=InteractionOutput)
# 转换为前端格式:
{
    "event_type": "chat.ask_user_question",
    "request_id": tool_call_id,
    "questions": [{
        "question": message,
        "header": f"权限审批: {tool_name}",
        "options": [
            {"label": "本次允许", "description": "仅本次授权执行"},
            {"label": "总是允许", "description": "记住该规则，以后自动放行"},
            {"label": "拒绝", "description": "拒绝执行此工具"},
        ],
        "multi_select": False,
    }],
    "source": "permission_interrupt",  # 关键：标识权限中断
}
```

**前端收到的事件示例**：
```json
{
  "type": "event",
  "event": "chat.ask_user_question",
  "payload": {
    "event_type": "chat.ask_user_question",
    "request_id": "call_670bddb2ff614426bdab2f8a",
    "questions": [...],
    "source": "permission_interrupt",
    "session_id": "sess_19d3e6fea02_18ec3b"
  }
}
```

### 1.3 第二次 stream：恢复执行

**前端请求**：
```json
{
  "method": "chat.send",
  "params": {
    "session_id": "sess_xxx",
    "query": "",
    "request_id": "call_670bddb2ff614426bdab2f8a",
    "answers": [{"selected_options": ["本次允许"]}]
  }
}
```

**后端转换答案**（`interface.py`）：
```python
def _build_interactive_input_from_answers(self, request_id: str, answers: list[dict]) -> Any:
    from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
    
    interactive_input = InteractiveInput()
    answer = answers[0] if answers else {}
    selected_options = answer.get("selected_options", [])
    custom_input = answer.get("custom_input", "")
    
    if "本次允许" in selected_options:
        confirm_payload = {"approved": True, "auto_confirm": False, "feedback": ""}
    elif "总是允许" in selected_options:
        confirm_payload = {"approved": True, "auto_confirm": True, "feedback": ""}
    elif "拒绝" in selected_options:
        confirm_payload = {"approved": False, "auto_confirm": False, "feedback": custom_input or "用户拒绝"}
    else:
        confirm_payload = {"approved": False, "auto_confirm": False, "feedback": "未知选项"}
    
    interactive_input.update(request_id, confirm_payload)
    return interactive_input
```

**Rail 处理用户响应**：
```python
def _handle_user_input(self, user_input, tool_name, ctx) -> InterruptDecision:
    if isinstance(user_input, ConfirmPayload):
        payload = user_input
    elif isinstance(user_input, dict):
        payload = ConfirmPayload.model_validate(user_input)
    else:
        return self.interrupt(self._build_default_request(tool_name))
    
    # 持久化规则（"总是允许"）
    if payload.auto_confirm and ctx.session:
        config = ctx.session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) or {}
        config[tool_name] = True
        ctx.session.update_state({INTERRUPT_AUTO_CONFIRM_KEY: config})
    
    if payload.approved:
        return self.approve()
    
    return self.reject(tool_result=payload.feedback or "User rejected")
```

---

## 二、前端 source 区分逻辑

### 2.1 两种确认场景

| 场景 | source 值 | 前端行为 | 后端处理 |
|------|-----------|---------|---------|
| 工具权限确认 | `"permission_interrupt"` | 发送 `chat.send {answers}` | 构建 `InteractiveInput` → 恢复执行 |
| 自进化确认 | `undefined` | 发送 `chat.user_answer` | 处理自进化确认 |

### 2.2 前端修改

**类型定义**：
```typescript
export interface AskUserQuestionPayload {
  request_id: string;
  request_ids?: string[];
  questions: Question[];
  source?: string;  // "permission_interrupt" | undefined
}
```

**核心逻辑**：
```typescript
const sendUserAnswer = useCallback(
  async (sessionId: string, requestId: string, answers: UserAnswer[], source?: string) => {
    if (source === 'permission_interrupt') {
      // 权限中断：发送 chat.send 恢复执行
      await streamRequest('chat.send', {
        session_id: sessionId,
        query: '',
        request_id: requestId,
        answers: answers,
      });
    } else {
      // 自进化确认：发送 chat.user_answer
      await request('chat.user_answer', {
        session_id: sessionId,
        request_id: requestId,
        answers,
      });
    }
    setPendingQuestion(null);
  },
  [request, streamRequest, setPendingQuestion]
);
```

**UI 组件调用**：
```typescript
const handleConfirm = async (answers: UserAnswer[]) => {
  const source = pendingQuestion?.source;
  await sendUserAnswer(sessionId, requestId, answers, source);
};
```

---

## 三、ConfirmPayload 映射

### 3.1 用户选项映射

| 用户选项 | approved | auto_confirm | feedback |
|---------|----------|--------------|----------|
| 本次允许 | `True` | `False` | `""` |
| 总是允许 | `True` | `True` | `""` |
| 拒绝 | `False` | `False` | 用户输入或"用户拒绝" |

### 3.2 ConfirmPayload 定义

```python
class ConfirmPayload(BaseModel):
    """Payload for user confirmation response."""
    approved: bool
    feedback: str = Field(default="")
    auto_confirm: bool = Field(default=False)

    @classmethod
    def to_schema(cls) -> dict:
        return cls.model_json_schema()
```

**结论：无需扩展 ConfirmPayload**，现有字段完全满足 permission 需求。

---

## 四、PermissionInterruptRail 实现

### 4.1 类定义

```python
class PermissionInterruptRail(ConfirmInterruptRail):
    """权限中断 Rail - 继承 ConfirmInterruptRail 并扩展静态配置"""
    
    priority: int = 90
    
    def __init__(
        self,
        config: Optional[dict] = None,
        engine: Optional[PermissionEngine] = None,
        tool_names: Optional[Iterable[str]] = None,
        llm: Any = None,
        model_name: str | None = None,
    ):
        super().__init__(tool_names=tool_names)
        self._static_config = config or {}
        if engine is not None:
            self._engine = engine
        else:
            self._engine = PermissionEngine(
                config=self._static_config,
                llm=llm,
                model_name=model_name,
            )
```

### 4.2 resolve_interrupt 完整实现

```python
async def resolve_interrupt(
    self,
    ctx: AgentCallbackContext,
    tool_call: Optional[ToolCall],
    user_input: Optional[Any],
) -> InterruptDecision:
    tool_name = tool_call.name if tool_call else ""
    tool_args = self._parse_tool_args(tool_call)
    
    if user_input is None:
        return await self._check_permission(ctx, tool_name, tool_args, tool_call)
    
    return self._handle_user_input(user_input, tool_name, ctx)


async def _check_permission(
    self,
    ctx: AgentCallbackContext,
    tool_name: str,
    tool_args: dict,
    tool_call: Optional[ToolCall],
) -> InterruptDecision:
    """首次调用：检查权限"""
    
    effective_config = self._get_effective_config(ctx)
    self._engine.update_config(effective_config)
    
    result = await self._engine.check_permission(tool_name, tool_args)
    
    if result.is_allowed:
        return self.approve()
    
    if result.is_denied:
        return self.reject(tool_result=f"[DENIED] {result.reason}")
    
    message = self._build_message(tool_name, tool_args, result)
    return self.interrupt(InterruptRequest(
        message=message,
        payload_schema=ConfirmPayload.to_schema(),
    ))


def _handle_user_input(
    self,
    user_input: Any,
    tool_name: str,
    ctx: AgentCallbackContext,
) -> InterruptDecision:
    """处理用户响应"""
    try:
        if isinstance(user_input, ConfirmPayload):
            payload = user_input
        elif isinstance(user_input, dict):
            payload = ConfirmPayload.model_validate(user_input)
        else:
            return self.interrupt(self._build_default_request(tool_name))
    except Exception:
        return self.interrupt(self._build_default_request(tool_name))
    
    # 持久化规则
    if payload.auto_confirm and ctx.session:
        config = ctx.session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) or {}
        config[tool_name] = True
        ctx.session.update_state({INTERRUPT_AUTO_CONFIRM_KEY: config})
    
    if payload.approved:
        return self.approve()
    
    return self.reject(tool_result=payload.feedback or "User rejected")
```

### 4.3 message 构建

```python
def _build_message(
    self,
    tool_name: str,
    tool_args: dict,
    result: PermissionResult,
) -> str:
    """构建完整的权限确认消息"""
    
    risk = result.risk or {"level": "中", "icon": "🟡", "explanation": "需要用户确认"}
    
    parts = [
        f"**工具 `{tool_name}` 需要授权才能执行**\n\n",
        f"**安全风险评估：** {risk['icon']} **{risk['level']}风险**\n\n",
        f"> {risk['explanation']}\n\n",
    ]
    
    args_preview = self._format_args_preview(tool_args)
    if args_preview and args_preview != "{}":
        parts.append(f"参数：\n```json\n{args_preview}\n```\n")
    
    parts.append(f"\n匹配规则：`{result.matched_rule or 'N/A'}`")
    
    if result.external_paths:
        paths_str = ", ".join(result.external_paths)
        parts.append(f"\n\n**外部路径：** `{paths_str}`")
    
    parts.append("\n\n> 选择「总是允许」将记住此规则，以后自动放行。")
    
    return "".join(parts)
```

---

## 五、PermissionEngine 三态判定

### 5.1 三态模型

```python
class PermissionLevel(Enum):
    """权限级别"""
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"
```

### 5.2 Engine 内部处理流程

```
┌─────────────────────────────────────────────────────────────┐
│                  PermissionEngine.check_permission()         │
├─────────────────────────────────────────────────────────────┤
│  1. 静态配置检查                                             │
│     ├── tools.{tool_name} = "allow" → ALLOW                  │
│     ├── tools.{tool_name} = "deny" → DENY                    │
│     └── patterns 匹配 → ALLOW/DENY                           │
├─────────────────────────────────────────────────────────────┤
│  2. 外部路径检测                                             │
│     └── 如果工具参数涉及外部路径 → ASK (含 external_paths)    │
├─────────────────────────────────────────────────────────────┤
│  3. LLM 风险评估（仅当需要审批时）                            │
│     └── 调用 LLM 评估风险等级和解释                           │
├─────────────────────────────────────────────────────────────┤
│  4. 返回 PermissionResult                                    │
│     ├── ALLOW: { permission: ALLOW, matched_rule: "..." }    │
│     ├── DENY: { permission: DENY, reason: "..." }            │
│     └── ASK: { permission: ASK, risk: {...}, ... }           │
└─────────────────────────────────────────────────────────────┘
```

### 5.3 风险评估

**LLM 风险评估**：
```python
_RISK_ICON_MAP = {
    "高": "🔴",
    "中": "🟡",
    "低": "🟢",
}

async def assess_command_risk_with_llm(llm, model_name, tool_name, tool_args) -> dict:
    """返回: {"level": "高|中|低", "icon": "🔴|🟡|🟢", "explanation": "风险解释"}"""
    ...
```

**静态风险评估（无 LLM 时回退）**：
```python
_HIGH_RISK_PATTERNS = ["rm -rf /*", "rm -rf /", "dd if=", "mkfs", ":(){ :|:& };:"]
_MEDIUM_RISK_PATTERNS = ["rm *", "chmod 777", "chown", "sudo"]

def assess_command_risk_static(tool_name, tool_args) -> dict:
    ...
```

---

## 六、配置结构

### 6.1 静态配置 (config.yaml)

```yaml
permissions:
  enabled: true
  
  defaults:
    "*": "ask"
  
  tools:
    mcp_exec_command:
      "*": "ask"
      patterns:
        "git status *": "allow"
        "git log *": "allow"
        "rm *": "deny"
    write_memory: "allow"
    read_memory: "allow"
```

### 6.2 运行时配置 (session.state)

```python
{
    "__interrupt_auto_confirm__": {
        "mcp_free_search": True,
        "write_memory": True,
    }
}
```

---

## 七、关键点总结

1. **两次 stream 恢复**：中断后流结束，需要再次调用 `stream()` 恢复
2. **source 字段区分**：前端根据 `source` 决定发送 `chat.send` 还是 `chat.user_answer`
3. **InteractiveInput**：后端将用户答案转换为 `InteractiveInput` 传递给 DeepAgent
4. **Session 复用**：中断状态存储在 `session.state` 中，必须复用 session
5. **无需扩展 ConfirmPayload**：现有字段完全满足 permission 需求
