# CLI Definition Usage

Translate cliDefinition entries from `<workspace>/resources/clis/available_clis.json` into `exec-cli("cli_command", "cli_sub_command", params?)` calls in the new skill. The third `params` argument is optional and must be omitted when the CLI has no input parameters.

## Metadata Note

If the skill uses CLI dependencies, declare them in `SKILL.md` frontmatter so packaging can copy the source definitions:

```yaml
metadata:
  clis:
    - name: "ohos-bm install"
```

Only declare CLIs the skill actually calls. Do not add empty placeholders.

## Input shape

JSON array; only `name`, `description`, and `inputSchema` drive the translation:

```json
{
  "name": "ohos-bm install",
  "description": "...",
  "inputSchema": {
    "properties": {
      "<param>": { "type": "boolean" | "string" | "number" | "array", "default": <value> }
    },
    "required": ["<param>", ...]
  }
}
```

## Call shape

Use the cliDefinition `name` field to derive the first two arguments. Add the third `params` argument only when the CLI has input parameters.

- `cli_command`: the first token before the first space.
- `cli_sub_command`: the remaining token or sub-command string after the first space.
- `params` optional: an object whose keys and values come from `inputSchema.properties`.

Rules:

- If `inputSchema.required` lists fields, include every required field in `params`.
- If `inputSchema` is missing, or `inputSchema.properties` is missing or empty, omit the params argument entirely; do not pass `{}`.
- Include optional fields only when the workflow needs them or the user explicitly requested them.
- Preserve parameter names exactly as defined in `inputSchema.properties`.

Examples:

```text
name: "ohos-bm install"
=> exec-cli("ohos-bm", "install", {module: "entry"})

name: "xxx yyy" with no inputSchema properties
=> exec-cli("xxx", "yyy")
```

## Safety

- `name` supplies the fixed `cli_command` and `cli_sub_command` strings. Never replace those strings with user input.
- User values belong only in the params object, never in `cli_command` or `cli_sub_command`.
- Include all required inputs before running.
- Boolean params should be normal object values, for example `{force: true}`.
- Reject string values with newlines, backticks, `$(`, `;`, or `|` unless the CLI explicitly needs them.

## Example

cliDefinition:

```json
{
  "name": "ohos-bm install",
  "inputSchema": {
    "properties": {
      "module": { "type": "string" },
      "force":  { "type": "boolean", "default": false }
    },
    "required": ["module"]
  }
}
```

Generated:
Call the CLI tool to execute:
```
    exec-cli("ohos-bm", "install", {module: "entry"})
```
Add `force: true` only when overwriting an existing install.

## Generating the tool-definitions entry

CLI tools sit in the skill body's **tool definitions** section. Each CLI becomes a `### CLI: <name>（平台注册）` sub-block.

Mapping rules:

| JSON field | Markdown field |
|------------|----------------|
| `name` | `### CLI: <name>（平台注册）` heading + `- **toolName**` |
| `description` | `- **description**` |
| `inputSchema` | 调用表（见下） |
| (n/a) | `- **约束**` |

- `- **toolName**` 用 cliDefinition 的完整 `name`，例如 `ohos-bm install`。
- 用调用表替代 `- **参数**`，一行一条有意义的参数组合。
- `- **约束**` 在任意子命令不可逆时（force-stop / delete / uninstall）**必填**，并写明需要先确认哪步。

Command table format:

```markdown
| 调用 | 说明 | 样例 |
|------|------|------|
| exec-cli("<cli_command>", "<cli_sub_command>", <params?>) | <one-line purpose> | <concrete example> |
```

One row per meaningful parameter combination. Use `<placeholder>` syntax in the template column and a fully-substituted params object in the 样例 column. Omit the params argument when the CLI has no input parameters.

Example — given a `ohos-bm install` CLI definition above, generate:

```markdown
### CLI: ohos-bm install（平台注册）
- **toolName**: ohos-bm install
- **description**: 安装应用包

| 调用 | 说明 | 样例 |
|------|------|------|
| exec-cli("ohos-bm", "install", {module: "<module>"}) | 安装指定模块 | exec-cli("ohos-bm", "install", {module: "entry"}) |
| exec-cli("ohos-bm", "install", {module: "<module>", force: true}) | 强制覆盖已存在的安装 | exec-cli("ohos-bm", "install", {module: "entry", force: true}) |

- **约束**: `force: true` 会覆盖已存在的安装，**必须**先确认用户意图
```
