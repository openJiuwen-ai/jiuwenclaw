# Review Result Schema

`review/result.json` 是审查数据唯一来源。`review.md` 由 `report` 根据该 JSON 渲染，禁止手改。

完整示例见 `assets/review_frontmatter_schema.json`，枚举见 `assets/review_frontmatter_enums.json`。

## 必填结论字段

- `verdict`: `PASS` 或 `FAIL`
- `gate_verdict`: `PASS` / `REWORK` / `HOLD`
- `verdict_reason`: 一句可复核结论
- `layer_alignment`: `PASS` 或 `FAIL`
- `patch_risk`: `none` / `suspected` / `confirmed`
- `risk_rating`: `Low` / `Medium` / `High` / `Unknown`

存在 Must Fix、重大安全/正确性/数据一致性风险或证据不足时，`verdict=FAIL`。仅有 Should Fix 时，是否本轮修由 Leader 分拣；不得仅因 Should Fix 把 `gate_verdict` 设为 `REWORK`，除非已升格为 Must Fix。

## Findings

每条 finding 必填：

- `id`
- `severity`
- `dimension`: `Code` / `Clean` / `Spec` / `Security` / `Performance`
- `category`: 枚举值，如 `correctness`、`security`、`data-consistency`
- `location`: `path:line` 或 `path:start-end`
- `issue`
- `risk`
- `recommendation`

禁止 `location` 为空、`unknown`、`N/A`、`多处`、`见下文`。仅无法对应具体代码行的架构/流程/文档类问题可用 `(architecture)` 或 `(documentation)`，并在 `issue` 说明原因。

## Comment Object

`comment` 可选但推荐，用于 GitCode 长行评：

- `title`
- `scenario`
- `examples`
- `impact`
- `fix`
- `verification`
- `code`

缺失时由旧字段自动派生。Must Fix 仍须有实质 `issue`、`risk/impact`、`recommendation/fix`。

## Security Review

`security_review.status`: `PASS` / `FAIL` / `not_applicable`

`security_review.items[].category` 枚举包括：

- `secrets`
- `config-hardening`
- `input-validation`
- `auth`
- `logging`
- `dependencies`
- `sandbox`

触及安全路径时不得用 `not_applicable` 敷衍；自动化扫描合并后必须通过 schema 校验。
