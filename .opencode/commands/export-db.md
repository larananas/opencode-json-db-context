---
description: Export the requested relational SQL scope to one validated JSON file
agent: tohru-db
---

Export request: $ARGUMENTS

If the MCP tools are not available, explain how to run setup.sh on macOS/Linux or setup.ps1 on Windows and rerun OpenCode. If the user did not specify a scope, default to server: all accessible non-system databases. If the user explicitly specifies a database, schema or table selection, do not broaden it.

For dotenv input use db_connect_env; for bare credentials use db_connect_and_export. If already connected, db_export is sufficient. Use db_inspect only when required to disambiguate names. Defaults: 20 samples, coherent related-parent rows (best effort), exact COUNT(*), one JSON under export/, no CSV. `compact_large_values` is opt-in; `expensive_sampling` needs explicit approval. For an unfamiliar server, optionally use db_preview_export first (no data samples or COUNT). If confirmation_required is returned, wait for the user's explicit approval before retrying the whole-server export. Optional redact_sensitive_data=true masks common sensitive sample columns; never claim it is complete anonymization. Report the file name, summary, exact row-count completeness, sample completeness and any validation problems. Do not reproduce sample values in chat. Never modify database contents.
