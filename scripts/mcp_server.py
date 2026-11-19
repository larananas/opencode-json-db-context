"""Private local MCP bridge. Never sends exported rows to the AI tool result."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db_engine import ConnectionSpec, accessible_databases, engine_version, estimate_size, export_database, list_objects, open_connection
from env_config import load_local_env, resolve_env
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Tohru OpenCode JSON DB Context")
_spec: ConnectionSpec | None = None
ROOT = Path(__file__).resolve().parents[1]
PRIVILEGED_USERS = {'root', 'sa', 'sys', 'system', 'postgres'}


def _privileged_server_guard(spec: ConnectionSpec, scope: str, approved: bool) -> dict[str, Any] | None:
    """A single explicit approval for exporting a whole server as an admin.

    User names are merely a risk heuristic; use database-enforced read-only roles.
    """
    if scope == 'server' and spec.engine != 'sqlite' and spec.user.lower() in PRIVILEGED_USERS and not approved:
        return {'status': 'confirmation_required', 'reason': 'privileged_server_export',
                'message': 'An administrative account was detected. Confirm a full server export explicitly, '
                           'or select a smaller scope / use a read-only account. No export started.',
                'next_step': 'After explicit user approval, call again with approve_privileged_server_export=true.'}
    return None


def _spec_or_fail() -> ConnectionSpec:
    if _spec is None:
        raise ValueError("Not connected. Call db_connect first, after requesting necessary connection details.")
    return _spec


@mcp.tool()
def db_capabilities() -> dict[str, Any]:
    """Show SQL engines, connection requirements, export scopes and safety restrictions."""
    return {
        "engines": ["MariaDB", "MySQL", "PostgreSQL", "Microsoft SQL Server", "Oracle Database (Thin mode)", "SQLite"],
        "connection": "db_connect(engine, host, port, user, password, database, service, path). Password is never returned or saved to disk.",
        "scopes": ["server", "databases", "database", "schema", "table", "tables"],
        "default": {"scope": "server", "sample_limit": 20, "formats": ["one indented JSON"], "exact_row_counts": True, "coherent_samples": True, "compact_large_values": False},
        "dotenv": "Paste dotenv text through db_connect_env(env_text=...), or place an env file inside .local/ and call db_connect_env(env_file=...). Bare credentials/default dotenv exports all accessible non-system databases unless an explicit narrower scope or connect_only is specified. Never echo credentials.",
        "security": "Exporter is read-only. Output remains local. MCP tools never return sample row values. "
                    "Use a read-only account. A privileged account needs one explicit confirmation for global export. "
                    "Optional sample-column redaction is best effort, not guaranteed anonymization.",
        "tls": "Optional tls_mode=auto|require|verify_ca|verify_identity for MariaDB/MySQL and PostgreSQL; "
               "SQL Server and Oracle use server-enforced TLS configuration (explicit TLS modes rejected). "
               "Place CA files only in ignored .local/.",
        "platform_notes": {
            "oracle":"server means accessible Oracle schemas within one service, not every pluggable database",
            "postgresql":"server means connectable non-template databases; each opened separately",
            "sqlite":"server means one local SQLite file",
            "sqlserver":"server means all visible online user databases",
            "mysql_mariadb":"server means all visible non-system databases",
        },
    }


@mcp.tool()
def db_connect(engine: str, host: str = "localhost", port: int = 0, user: str = "",
               password: str = "", database: str = "", service: str = "", path: str = "",
               server_label: str = "", tls_mode: str = "auto", tls_ca_file: str = "") -> dict[str, Any]:
    """Connect to a SQL engine. Requires an explicit user-supplied password or blank for passwordless auth. No credentials saved. sqlite uses path. Never echo password."""
    global _spec
    spec = ConnectionSpec(engine=engine, host=host, port=port or None, user=user, password=password,
                          database=database, service=service, path=path, server_label=server_label,
                          tls_mode=tls_mode, tls_ca_file=tls_ca_file)
    # Failed manual connection must not leave a previous server selected.
    _spec = None
    try:
        conn = open_connection(spec)
    except Exception as exc:
        return {"status": "connection_failed", "engine": spec.engine,
                "error_type": type(exc).__name__,
                "next_step": "Verify host, credentials, TLS settings and network reachability"}
    try:
        if spec.engine == "oracle":
            cur=conn.cursor()
            try:
                cur.execute("SELECT 1 FROM dual")
                cur.fetchone()
            finally:
                cur.close()
        else:
            cur=conn.cursor()
            try:
                cur.execute("SELECT 1")
                cur.fetchone()
            finally:
                cur.close()
        version = engine_version(conn,spec.engine)
    finally:
        conn.close()
    _spec=spec
    return {"status":"connected", "engine":spec.engine,"server":server_label or (path if spec.engine=="sqlite" else host),
            "database":database or None,"version":version,
            "security":"Credentials exist only in this local MCP process memory. Call db_disconnect to clear them."}


@mcp.tool()
def db_disconnect() -> dict[str, str]:
    """Forget connection credentials in this MCP process; exported files remain."""
    global _spec
    _spec=None
    return {"status":"disconnected"}


@mcp.tool()
def db_inspect(database: str = "", include_tables: bool = True) -> dict[str, Any]:
    """Discover accessible databases and optionally list table names; no sample rows or secrets returned."""
    spec=_spec_or_fail()
    errors: list[str] = []
    root=open_connection(spec)
    try:
        found=accessible_databases(root,spec,errors)
        result={"engine":spec.engine,"accessible_databases_or_schemas":found,"warnings":errors,"databases":{}}
        targets=[database] if database else found
        for db in targets:
            if db not in found and spec.engine != "sqlite":
                errors.append(f"Requested {db!r} is not in discovered list")
                continue
            conn=root if spec.engine in ("oracle","sqlite") else None
            owned=False
            if conn is None:
                try:
                    conn=open_connection(spec,db)
                    owned=True
                except Exception as exc:
                    errors.append(f"Cannot open {db}: {type(exc).__name__}: {exc}")
                    continue
            try:
                if include_tables:
                    objs=list_objects(conn,spec,db,errors)
                    result["databases"][db]={"table_count":sum(o["type"]=="table" for o in objs),
                        "view_count":sum(o["type"]=="view" for o in objs),
                        "objects":[{"schema":o["schema"],"name":o["name"],"type":o["type"]} for o in objs]}
            finally:
                if owned:
                    conn.close()
        return result
    finally:
        root.close()


@mcp.tool()
def db_preview_export(scope: str = "server", database: str = "", schema: str = "") -> dict[str, Any]:
    """Estimate scope and scale without sampling, COUNT(*) or writing files.

    Estimates can be stale or unavailable; this is a fast planning aid, not
    a replacement for exact counts in the resulting JSON.
    """
    spec = _spec_or_fail()
    errors: list[str] = []
    root = open_connection(spec)
    try:
        databases = accessible_databases(root, spec, errors)
        targets = databases if scope == 'server' else [database]
        if scope not in ('server', 'database') or not all(target in databases for target in targets):
            raise ValueError('Preview supports accessible server or single database scope only')
        per_db: dict[str, Any] = {}
        for db in targets:
            conn = root if spec.engine in ('oracle', 'sqlite') else open_connection(spec, db)
            try:
                objects = list_objects(conn, spec, db, errors)
                tables = [obj for obj in objects if obj['type'] == 'table' and
                          (not schema or obj['schema'] == schema)]
                estimates = [estimate_size(conn, spec, db, obj, errors) for obj in tables]
                per_db[db] = {'table_count': len(tables),
                              'estimated_rows_known': sum(n for n in estimates if n is not None),
                              'unknown_size_tables': sum(n is None for n in estimates)}
            finally:
                if conn is not root:
                    conn.close()
        total_tables = sum(v['table_count'] for v in per_db.values())
        return {'status': 'preview', 'scope': scope, 'databases': per_db,
                'table_count': total_tables, 'estimated_total_rows_known': sum(v['estimated_rows_known'] for v in per_db.values()),
                'expensive_export_risk': total_tables > 500 or any(
                    v['estimated_rows_known'] > 5_000_000 for v in per_db.values()),
                'privileged_account': spec.user.lower() in PRIVILEGED_USERS,
                'warnings': errors + ['Counts are estimates; actual export uses COUNT(*) and may be expensive']}
    finally:
        root.close()


@mcp.tool()
def db_export(scope: str = "server", database: str = "", schema: str = "", table: str = "",
              database_names: list[str] | None = None, table_names: list[str] | None = None,
              sample_limit: int = 20, expensive_sampling: bool = False,
              coherent_samples: bool = True, compact_large_values: bool = False,
              max_value_bytes: int = 4096, redact_sensitive_data: bool = False,
              approve_privileged_server_export: bool = False) -> dict[str, Any]:
    """Export the exact requested scope to one local JSON; no CSV or retained temp artifacts.

    Scope 'tables' selects a named list in one database (schema-qualified when needed).
    All tables get an exact COUNT(*), up to 20 random samples and structural validation.
    Relationship-aware related-parent samples are on by default (best effort, selected scope only).
    Lossily compacted large values are opt-in.
    Expensive full-table random sorts require explicit consent on large unindexed tables.
    Only output paths and counters are returned; no samples enter AI tool context.
    """
    spec = _spec_or_fail()
    guard = _privileged_server_guard(spec, scope, approve_privileged_server_export)
    if guard:
        return guard
    return export_database(spec, scope=scope, database=database, schema=schema, table=table,
                           database_names=database_names, table_names=table_names,
                           sample_limit=sample_limit, expensive_sampling=expensive_sampling,
                           coherent_samples=coherent_samples, compact_large_values=compact_large_values,
                           max_value_bytes=max_value_bytes, redact_sensitive_data=redact_sensitive_data,
                           output_dir=ROOT / "export")


@mcp.tool()
def db_connect_env(env_text: str = "", env_file: str = "", scope: str = "server",
                   connect_only: bool = False, database: str = "", schema: str = "",
                   table: str = "", database_names: list[str] | None = None,
                   table_names: list[str] | None = None, sample_limit: int = 20,
                   coherent_samples: bool = True, compact_large_values: bool = False,
                   expensive_sampling: bool = False, redact_sensitive_data: bool = False,
                   approve_privileged_server_export: bool = False) -> dict[str, Any]:
    """Parse pasted dotenv text OR a local ignored .local/*.env file and connect safely.

    By default, also export the accessible non-system server scope into ONE JSON.
    Use connect_only=True only for an explicit connection/inspection-only request.
    Explicit narrow scope takes precedence; never broaden a requested scope.
    Passwords are not returned, saved by the MCP, or passed back in model results.
    Chat-provided env text is visible to the model provider; prefer local env_file.
    """
    global _spec
    if bool(env_text) == bool(env_file):
        raise ValueError("Provide exactly one of env_text or env_file")
    resolution = resolve_env(env_text if env_text else load_local_env(env_file, root=ROOT))
    spec = resolution.spec
    # A failed connection must not leave a previous connection selected.
    _spec = None
    try:
        conn = open_connection(spec)
    except Exception as exc:
        # Do not expose driver exception text: URLs or credentials may appear in it.
        hints = list(resolution.warnings)
        hints.append("Verify database reachability, credentials and published Docker/Compose host port")
        return {"status": "connection_failed", "engine": spec.engine,
                "error_type": type(exc).__name__, "warnings": hints,
                "next_step": "Correct the connection parameters and retry; no export was created"}
    try:
        cur = conn.cursor()
        try:
            cur.execute("SELECT 1 FROM dual" if spec.engine == "oracle" else "SELECT 1")
            cur.fetchone()
        finally:
            cur.close()
        version = engine_version(conn, spec.engine)
    finally:
        conn.close()
    _spec = spec
    connection = {"status": "connected", "engine": spec.engine,
                  "server": spec.server_label or (Path(spec.path).name if spec.engine == "sqlite" else spec.host),
                  "version": version, "source": resolution.source,
                  "recognized_keys": resolution.recognized_keys, "warnings": resolution.warnings}
    if connect_only:
        return {"connection": connection, "status": "connected", "next_step": "db_inspect or db_export as explicitly requested"}
    guard = _privileged_server_guard(spec, scope, approve_privileged_server_export)
    if guard:
        return {'connection': connection, **guard}
    target_database = database or (spec.database if scope in ("database", "schema", "table", "tables") else "")
    result = export_database(spec, scope=scope, database=target_database, schema=schema, table=table,
                             database_names=database_names, table_names=table_names,
                             sample_limit=sample_limit, coherent_samples=coherent_samples,
                             compact_large_values=compact_large_values,
                             expensive_sampling=expensive_sampling,
                             redact_sensitive_data=redact_sensitive_data,
                             output_dir=ROOT / "export")
    return {"connection": connection, "export": result, "status": result["status"]}


@mcp.tool()
def db_connect_and_export(engine: str, host: str = "localhost", port: int = 0,
                          user: str = "", password: str = "", database: str = "",
                          service: str = "", path: str = "", scope: str = "server",
                          schema: str = "", table: str = "",
                          database_names: list[str] | None = None,
                          table_names: list[str] | None = None, sample_limit: int = 20,
                          coherent_samples: bool = True,
                          compact_large_values: bool = False,
                          expensive_sampling: bool = False, redact_sensitive_data: bool = False,
                          approve_privileged_server_export: bool = False,
                          tls_mode: str = "auto", tls_ca_file: str = "") -> dict[str, Any]:
    """Use bare connection credentials to connect and export server scope by default.

    For a connect-only request use db_connect, not this tool. The returned data
    never contains passwords or actual sampled rows.
    """
    connection = db_connect(engine=engine, host=host, port=port, user=user,
                            password=password, database=database, service=service, path=path,
                            tls_mode=tls_mode, tls_ca_file=tls_ca_file)
    result = db_export(scope=scope, database=database if scope in ("database", "schema", "table", "tables") else "",
                       schema=schema, table=table, database_names=database_names,
                       table_names=table_names, sample_limit=sample_limit,
                       coherent_samples=coherent_samples,
                       compact_large_values=compact_large_values,
                       expensive_sampling=expensive_sampling,
                       redact_sensitive_data=redact_sensitive_data,
                       approve_privileged_server_export=approve_privileged_server_export)
    if result.get('status') == 'confirmation_required':
        return {'connection': connection, **result}
    return {"connection": connection, "export": result, "status": result["status"]}


if __name__ == "__main__":
    mcp.run(transport="stdio")
