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
