# CLI Definition Usage

Translate cliDefinition entries from `<workspace>/resources/clis/available_clis.json` into `exec(command:"...")` calls in the new skill.

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
