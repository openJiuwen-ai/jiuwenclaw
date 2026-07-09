# Review Comment Templates

## 格式

`[Must Fix|Should Fix|Nice to Have][Code|Clean|Spec|Security|Performance]` + 简述。

`result.json`：

- **`category`**（必填）：[review_frontmatter_enums.json](review_frontmatter_enums.json) 枚举；**不得**写入维度名。
- **`dimension`**（可选）：与评论标签一致。
- **`comment`**（可选但推荐）：用于生成截图式 GitCode 长行评；缺失时由 `issue` / `risk` / `recommendation` / `minimal_patch_example` 自动派生。
- **`comment.code_language`**（可选）：指定 fenced code block 语言，如 `python` / `diff` / `json` / `markdown`；不填时 diff 会自动识别为 `diff`。

### GitCode 长行评结构

所有 Must Fix / Should Fix 默认生成独立 Markdown 行评文件，并用 `pr_commenter.py --comment-file` 提交；禁止把多行 Markdown、代码块或反引号直接塞入 `--comment`。行评和讨论区都使用该 finding 自己的 `CR-xxx.md`，不得把多条 findings 合并成一个评论文件。

Must Fix 标题：

```markdown
**[严重][Must Fix][{Dimension}]** {title}
```

Should Fix 标题：

```markdown
**[建议][Should Fix][{Dimension}]** {title}
```

正文固定包含：

- `问题`：具体错误，不写泛泛质量描述
- `触发场景`：什么输入/状态/分支会触发
- `例如`：可选，列出输入输出或误判样例
- `影响`：业务、数据、稳定性、安全或维护后果
- `建议修复`：可执行修复方向
- `代码块`：可选，来自 `comment.code` 或 `minimal_patch_example`；语言来自 `comment.code_language` 或自动 diff 识别
- `验证建议`：补什么测试或如何复核

### 维度 → `category`（典型）

| 标签 | 典型 `category` |
|------|-----------------|
| `[Code]` | `correctness`, `concurrency`, `data-consistency`, `maintainability` |
| `[Clean]` | `maintainability` |
| `[Spec]` | `testing`, `maintainability` |
| `[Security]` | `security` |
| `[Performance]` | `performance` |

---

## Must Fix

**[Must Fix][{Dimension}]** 在 {file}:{line} 处 {impact}。  
指引：[../references/{ref}.md](../references/{ref}.md)（`code`/`clean`/`spec`/`security`/`performance`）  
建议：{action}。

推荐 `comment`：

```json
{
  "title": "`error` 字段对 falsy 值存在误判",
  "scenario": "当 error 为 0、[]、{}、False 等 falsy 但非 None 的值时，当前判断会把正常结果误判为错误。",
  "examples": [
    "error=0 -> str(0).strip() == \"0\" -> 误判为有错误",
    "error=[] -> str([]).strip() == \"[]\" -> 误判为有错误"
  ],
  "impact": "会影响 unknown_tool_repeat 的 streak 计数，可能导致误触发熔断器中断 Agent 执行。",
  "fix": "先排除 None，仅对非空字符串或真实异常对象判定为错误。",
  "verification": "补充 error=0、error=[]、error=False、error=None、error='none' 的参数化测试。",
  "code_language": "python",
  "code": "err = payload.get(\"error\")\nif err is not None:\n    ..."
}
```

```diff
{patch}
```

---

## Should Fix

**[Should Fix][{Dimension}]** {reason}。建议：{action}。  
Leader 本轮必改 → **`[Leader 建议升格]`**（`leader_escalate: true`）。

---

## Nice to Have

**[Nice to Have][{Dimension}]** {suggestion}。

---

## 速查

| 标签 | 场景 | 指引 |
|------|------|------|
| `[Code]` | 设计、行为、分层 | [code.md](../references/dimensions/code.md) |
| `[Clean]` | 命名、import、语言规范 | [clean.md](../references/dimensions/clean.md) |
| `[Spec]` | 需求/设计/测试计划 | [spec.md](../references/dimensions/spec.md) |
| `[Security]` | 注入、鉴权、敏感数据 | [security.md](../references/dimensions/security.md) |
| `[Performance]` | N+1、超时、资源 | [performance.md](../references/dimensions/performance.md) |
