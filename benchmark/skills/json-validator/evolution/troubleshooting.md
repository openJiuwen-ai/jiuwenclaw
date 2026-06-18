# Troubleshooting

> Auto-generated from evolutions.json. Do not edit directly.

<a id="ev_c4d8e2f1"></a>
### [ev_c4d8e2f1] Force Draft-07 as default schema version for compatibility
Always default to JSON Schema Draft-07 for maximum compatibility. Many users still use older schemas, and Draft-07 validators are the most widely supported. When $schema field is missing or ambiguous, force Draft-07 mode by adding $schema: https://json-schema.org/draft-07/schema# to the schema file before validation. This avoids compatibility issues with older tools.

*Source: evolution_pipeline | 2025-08-05T10:15:00+00:00*

---
