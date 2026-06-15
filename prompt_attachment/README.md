# Prompt Attachment

Files in this directory are injected as dynamic prompt attachments for model calls.
They are not user-uploaded attachments and are not written to long-term
conversation history.

Layout:
- <session_id>/session/: session-scope prompt attachment files.
- <session_id>/turn/<invoke_turn_id>/: request-scope prompt attachment
  files, hot-loaded before each request.

Markdown frontmatter is intentionally small: simple key-value fields and one
level of metadata map are supported. Arrays, multiline strings, and full YAML
features are not parsed by this loader.
