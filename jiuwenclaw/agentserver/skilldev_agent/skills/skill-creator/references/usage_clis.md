# CLI Definition Usage

Translate cliDefinition entries from `<workspace>/resources/clis/available_clis.json` into `exec(command:"...")` calls in the new skill.

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

## Safety

- `name` is the fixed command prefix — never inject user input into it.
- User values go inside double quotes (`--key "${value}"`), never bare.
- Include all required inputs before running.
- Append boolean flags only when true, unless the CLI explicitly defines false syntax.
- Reject values with newlines, backticks, `$(`, `;`, or `|` unless the CLI needs them.

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
Call the exec tool to execute:
```
    exec(command: `ohos-bm install --module "${module}"`)
```
Append `--force` only when overwriting an existing install.

## Generating the tool-definitions entry

CLI tools sit in the skill body's **tool definitions** section. Each CLI becomes a `### CLI: <name>（平台注册）` sub-block.

Mapping rules:

| JSON field | Markdown field |
|------------|----------------|
| `name` (command prefix) | `### CLI: <name>（平台注册）` heading + `- **toolName**` |
| `description` | `- **description**` |
| `inputSchema` + sub-commands | 命令表（见下） |
| (n/a) | `- **约束**` |

- `- **toolName**` 用裸二进制名（`ohos-aa`），不带子命令。
- 用命令表替代 `- **参数**`，一行一条有意义的子命令或参数组合。
- `- **约束**` 在任意子命令不可逆时（force-stop / delete / uninstall）**必填**，并写明需要先确认哪步。

Command table format:

```markdown
| 命令 | 说明 | 样例 |
|------|------|------|
| <name> <args template> | <one-line purpose> | <concrete example> |
```

One row per meaningful sub-command or argument combination. Use `<placeholder>` syntax in the template column and a fully-substituted string in the 样例 column.

Example — given a `ohos-bm install` CLI definition above, generate:

```markdown
### CLI: ohos-bm install（平台注册）
- **toolName**: ohos-bm install
- **description**: 安装应用包

| 命令 | 说明 | 样例 |
|------|------|------|
| ohos-bm install --module "<module>" | 安装指定模块 | ohos-bm install --module "entry" |
| ohos-bm install --module "<module>" --force | 强制覆盖已存在的安装 | ohos-bm install --module "entry" --force |

- **约束**: --force 会覆盖已存在的安装，**必须**先确认用户意图
```
