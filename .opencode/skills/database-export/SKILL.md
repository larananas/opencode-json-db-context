---
name: database-export
description: Export a requested relational SQL scope to one validated JSON including counts, metadata, foreign keys, and samples
---

# Relational database context extraction

1. Confirm that `db_capabilities` is available. If not, instruct the user to run the correct platform setup script, diagnose with `--check` (POSIX) or `-Check` (PowerShell), and reopen OpenCode. Never silently install packages.
2. For dotenv input use `db_connect_env` (pasted text or `.local/` file) and for bare credentials use `db_connect_and_export`; use `db_connect` and `db_inspect` when the request is connection/discovery only. Supported scopes: `table`, `tables`, `schema`, `database`, `databases`, `server`. Preserve exactly the user's explicit selection; otherwise export the entire accessible non-system server scope by default.
3. Use `db_export` or the connect-and-export tool with default 20 random samples, best-effort related-parent coherence, and exact per-table `COUNT(*)`. Include every available column/constraint/FK, views and relevant metadata. Only the final formatted JSON should remain in `export/`; older JSON files are preserved.
4. `coherent_samples=true` is the default and enriches selected parent samples with referenced rows **within** scope; it can be explicitly disabled. `compact_large_values=true` abbreviates large values lossily. Require explicit request. `expensive_sampling=true` requires explicit acknowledgement of database load.
5. Report the export path, totals, sample counts, completeness flags, issues, warnings and failures. Do not claim success from generated SQL alone. No real row contents in model output.
6. SQL databases only: MariaDB, MySQL, PostgreSQL, SQL Server, Oracle Thin and SQLite. MongoDB is unsupported. SQLite file paths are restricted to the project; use `.local/`.

The bridge provides no write SQL operations; do not bypass tool permissions. Source code and generated technical artifacts are English; respond in the user's language.
