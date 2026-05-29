# CLI Definition Usage

Translate cliDefinition entries from `<workspace>/resources/clis/available_clis.json` into CLI command strings in the new skill. When inputs are present, append them as CLI flags, for example `ohos-storageManager get-bundle-stats --packageName <包名>`.

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

Use the cliDefinition `name` field as the fixed command prefix. Add input parameters as flags only when the CLI has input parameters.

- Command prefix: the full `name` value, for example `ohos-storageManager get-bundle-stats`.
- Parameter flags: `--<paramName> <placeholder>` for string, number, and array values.
- Boolean flags: include `--<paramName>` only when the workflow needs the true value.

Rules:

- If `inputSchema.required` lists fields, include every required field as a flag.
- If `inputSchema` is missing, or `inputSchema.properties` is missing or empty, use only the fixed command prefix.
- Include optional fields only when the workflow needs them or the user explicitly requested them.
- Preserve parameter names exactly as defined in `inputSchema.properties`.

## Safety

- `name` supplies the fixed command prefix. Never replace that prefix with user input.
- User values belong only in flag values, never in the command prefix or flag names.
- Include all required inputs before running.
- Quote concrete string values that contain spaces.
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
    ohos-bm install --module entry
```
Add `--force` only when overwriting an existing install.

## Generating the tool-definitions entry

CLI tools sit in the skill body's **tool definitions** section. Each CLI becomes a `### CLI: <name>` sub-block.

Mapping rules:

| JSON field | Markdown field |
|------------|----------------|
| `name` | `### CLI: <name>` heading + `- **toolName**` |
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
| <command-name> --<param> <placeholder> | <one-line purpose> | <concrete example> |
```

One row per meaningful parameter combination. Use `<placeholder>` syntax in the template column and fully-substituted command values in the 样例 column. Use only the command prefix when the CLI has no input parameters.

Example — given a `ohos-bm install` CLI definition above, generate:

```markdown
### CLI: ohos-bm install
- **toolName**: ohos-bm install
- **description**: 安装应用包

| 调用 | 说明 | 样例 |
|------|------|------|
| `ohos-bm install --module <module>` | 安装指定模块 | `ohos-bm install --module entry` |
| `ohos-bm install --module <module> --force` | 强制覆盖已存在的安装 | `ohos-bm install --module entry --force` |

- **约束**: `--force` 会覆盖已存在的安装，**必须**先确认用户意图
```