"""Read-only, cross-dialect database metadata and sampled-data exporter.

All database operations in this module are SELECT/SHOW/PRAGMA or session setup.
It never runs DML or DDL against a user database.
"""
from __future__ import annotations

import base64
import datetime as dt
from decimal import Decimal
import json
import math
import hashlib
import secrets
import os
from pathlib import Path
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import UUID

SYSTEM_DBS = {
    "mysql": {"mysql", "information_schema", "performance_schema", "sys"},
    "mariadb": {"mysql", "information_schema", "performance_schema", "sys"},
    "postgresql": {"template0", "template1", "postgres"},
    "sqlserver": {"master", "model", "msdb", "tempdb"},
}
ENGINES = {"mysql", "mariadb", "postgresql", "sqlserver", "oracle", "sqlite"}


@dataclass
class ConnectionSpec:
    engine: str
    host: str = "localhost"
    port: int | None = None
    user: str = ""
    password: str = field(default="", repr=False)
    database: str = ""
    service: str = ""
    path: str = ""
    timeout: int = 15
    server_label: str = ""

    def __post_init__(self) -> None:
        aliases = {"postgres": "postgresql", "postgresql": "postgresql", "mssql": "sqlserver", "sql_server": "sqlserver", "sqlite3": "sqlite"}
        self.engine = aliases.get(self.engine.lower().strip(), self.engine.lower().strip())
        if self.engine not in ENGINES:
            raise ValueError(f"Unsupported engine: {self.engine}. Supported: {sorted(ENGINES)}")
        if self.port is not None and not 1 <= self.port <= 65535:
            raise ValueError("Port must be in 1..65535")
        if not self.path and self.engine == "sqlite":
            raise ValueError("SQLite requires an existing file path")


def slug(value: str) -> str:
    output = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip()).strip("_- ")
    return output[:70] or "unnamed"


def json_value(value: Any, *, compact_large_values: bool = False, max_value_bytes: int = 4096) -> Any:
    """JSON-safe, lossless by default. Optional large-value summaries are explicitly lossy."""
    if compact_large_values and isinstance(value, (str, bytes, bytearray, memoryview)):
        raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        if len(raw) > max_value_bytes:
            result = {"$type": "omitted_large_text" if isinstance(value, str) else "omitted_large_binary",
                      "byte_length": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            if isinstance(value, str):
                result["preview"] = value[:100]
            return result
    if isinstance(value, float) and not math.isfinite(value):
        return {"$type":"nonfinite_float", "value":str(value)}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return {"$type": "decimal", "value": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$type": "base64", "value": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return {"$type": type(value).__name__, "value": value.isoformat()}
    if isinstance(value, UUID):
        return {"$type": "uuid", "value": str(value)}
    if hasattr(value, "read") and callable(value.read):  # Oracle LOB
        return json_value(value.read(), compact_large_values=compact_large_values, max_value_bytes=max_value_bytes)
    if isinstance(value, (list, tuple)):
        return [json_value(v, compact_large_values=compact_large_values, max_value_bytes=max_value_bytes) for v in value]
    if isinstance(value, dict):
        return {str(k): json_value(v, compact_large_values=compact_large_values, max_value_bytes=max_value_bytes) for k, v in value.items()}
    return {"$type": type(value).__name__, "value": str(value)}


def quote_ident(engine: str, identifier: str) -> str:
    if engine in ("mysql", "mariadb"):
        return "`" + identifier.replace("`", "``") + "`"
    if engine == "sqlserver":
        return "[" + identifier.replace("]", "]]") + "]"
    return '"' + identifier.replace('"', '""') + '"'


def lit(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def execute_rows(conn: Any, query: str) -> list[dict[str, Any]]:
    cur = conn.cursor()
    try:
        cur.execute(query)
        if cur.description is None:
            return []
        names = [x[0] for x in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]
    finally:
        cur.close()


def query_safe(conn: Any, query: str, errors: list[str], context: str) -> list[dict[str, Any]]:
    try:
        return execute_rows(conn, query)
    except Exception as exc:
        errors.append(f"{context}: {type(exc).__name__}: {exc}")
        return []


def open_connection(spec: ConnectionSpec, database: str | None = None) -> Any:
    engine = spec.engine
    db = database if database is not None else spec.database
    if engine == "sqlite":
        file = Path(spec.path).expanduser().resolve(strict=True)
        project_root = Path(__file__).resolve().parents[1]
        try:
            file.relative_to(project_root)
        except ValueError as exc:
            raise ValueError("SQLite files must be inside the project. Copy the database into .local/ first.") from exc
        if not file.is_file():
            raise ValueError("SQLite path must refer to an existing database file")
        # SQLite read-only URI; no accidental file creation or external paths.
        return sqlite3.connect(file.as_uri() + "?mode=ro", uri=True, timeout=spec.timeout)
    if engine in ("mysql", "mariadb"):
        import pymysql
        options = {"host": spec.host, "port": spec.port or 3306, "user": spec.user, "password": spec.password,
                   "connect_timeout": spec.timeout, "read_timeout": 180, "autocommit": True, "charset": "utf8mb4"}
        if db:
            options["database"] = db
        conn = pymysql.connect(**options)
        # Read-only transaction is not used because multiple metadata reads are required.
        return conn
    if engine == "postgresql":
        import pg8000.dbapi
        conn = pg8000.dbapi.connect(user=spec.user, password=spec.password, host=spec.host,
                                   port=spec.port or 5432, database=db or "postgres", timeout=spec.timeout)
        conn.autocommit = True
        return conn
    if engine == "sqlserver":
        import pytds
        return pytds.connect(server=spec.host, port=spec.port or 1433, database=db or "master",
                             user=spec.user, password=spec.password, login_timeout=spec.timeout, timeout=180,
                             autocommit=True)
    if engine == "oracle":
        import oracledb
        service = spec.service or spec.database
        if not service:
            raise ValueError("Oracle requires a service name")
        return oracledb.connect(user=spec.user, password=spec.password,
                                dsn=f"{spec.host}:{spec.port or 1521}/{service}", tcp_connect_timeout=spec.timeout)
    raise ValueError(engine)


def engine_version(conn: Any, engine: str) -> str:
    q = {"mysql": "SELECT VERSION() AS version", "mariadb": "SELECT VERSION() AS version",
         "postgresql": "SELECT version() AS version", "sqlserver": "SELECT @@VERSION AS version",
         "oracle": "SELECT banner AS version FROM v$version WHERE ROWNUM = 1",
         "sqlite": "SELECT sqlite_version() AS version"}[engine]
    try:
        rows = execute_rows(conn, q)
        return str(rows[0]["version"]) if rows else "unknown"
    except Exception:
        return "unknown"


def accessible_databases(conn: Any, spec: ConnectionSpec, errors: list[str]) -> list[str]:
    engine = spec.engine
    if engine in ("mysql", "mariadb"):
        rows = query_safe(conn, "SHOW DATABASES", errors, "list databases")
        return [str(next(iter(row.values()))) for row in rows if str(next(iter(row.values()))).lower() not in SYSTEM_DBS[engine]]
    if engine == "postgresql":
        rows = query_safe(conn, "SELECT datname FROM pg_database WHERE datallowconn AND NOT datistemplate ORDER BY datname", errors, "list databases")
        return [r["datname"] for r in rows if r["datname"].lower() not in SYSTEM_DBS[engine]]
    if engine == "sqlserver":
        rows = query_safe(conn, "SELECT name FROM sys.databases WHERE state_desc = 'ONLINE' AND database_id > 4 ORDER BY name", errors, "list databases")
        return [r["name"] for r in rows]
    if engine == "oracle":
        # Oracle services are databases; schemas are the discoverable logical scope.
        rows = query_safe(conn, "SELECT DISTINCT owner FROM all_tables WHERE owner NOT IN ('SYS','SYSTEM','XDB','OUTLN','CTXSYS','MDSYS','ORDSYS','DBSNMP') ORDER BY owner", errors, "list Oracle schemas")
        return [r["OWNER"] for r in rows]
    return [Path(spec.path).name]


def list_objects(conn: Any, spec: ConnectionSpec, database: str, errors: list[str]) -> list[dict[str, str]]:
    eng = spec.engine
    if eng in ("mysql", "mariadb"):
        q = ("SELECT TABLE_NAME AS table_name, TABLE_TYPE AS table_type FROM information_schema.TABLES "
             f"WHERE TABLE_SCHEMA={lit(database)} ORDER BY TABLE_NAME")
    elif eng == "postgresql":
        q = """SELECT table_schema AS schema_name, table_name, table_type
FROM information_schema.tables
WHERE table_schema NOT IN ('information_schema','pg_catalog')
  AND table_schema NOT LIKE 'pg_toast%' ORDER BY table_schema, table_name"""
    elif eng == "sqlserver":
        q = """SELECT s.name AS schema_name, o.name AS table_name,
CASE WHEN o.type = 'V' THEN 'VIEW' ELSE 'BASE TABLE' END AS table_type
FROM sys.objects o JOIN sys.schemas s ON o.schema_id=s.schema_id
WHERE o.type IN ('U','V') AND o.is_ms_shipped = 0 ORDER BY s.name,o.name"""
    elif eng == "oracle":
        q = (f"SELECT owner AS schema_name, table_name, 'BASE TABLE' AS table_type FROM all_tables WHERE owner={lit(database)} "
             f"UNION ALL SELECT owner AS schema_name, view_name AS table_name, 'VIEW' AS table_type FROM all_views WHERE owner={lit(database)} ORDER BY 2")
    else:
        q = "SELECT name AS table_name, type AS table_type, sql AS definition FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
    rows = query_safe(conn, q, errors, f"list objects {database}")
    result: list[dict[str, str]] = []
    for r in rows:
        result.append({"schema": str(r.get("schema_name") or (database if eng in ("mysql", "mariadb") else "main")),
                       "name": str(r["table_name"]),
                       "type": "view" if str(r["table_type"]).upper() in ("VIEW", "V") else "table",
                       "definition": r.get("definition")})
    return result


def table_metadata(conn: Any, spec: ConnectionSpec, database: str, obj: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    eng, schema, name = spec.engine, obj["schema"], obj["name"]
    ref = f"{database}.{schema}.{name}"
    ident = quote_ident
    result: dict[str, Any] = {"name": name, "schema": schema, "type": obj["type"],
       "metadata": {}, "create_sql": obj.get("definition"), "columns": [], "primary_key": [],
       "foreign_keys": [], "indexes": [], "unique_constraints": [], "check_constraints": [],
       "sample_count": 0, "sample_rows": [], "sample_status": "not_applicable" if obj["type"] == "view" else "pending"}
    if eng in ("mysql", "mariadb", "postgresql", "sqlserver"):
        schema_name = schema if eng not in ("mysql", "mariadb") else database
        cs = lit(schema_name)
        tn = lit(name)
        columns_q = f"SELECT * FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA={cs} AND TABLE_NAME={tn} ORDER BY ORDINAL_POSITION"
        result["columns"] = [json_value(row) for row in query_safe(conn, columns_q, errors, ref+" columns")]
        if eng == "postgresql":
            # Supplement portable INFORMATION_SCHEMA with PostgreSQL's full native column attributes.
            extras = query_safe(conn,
                "SELECT a.attname AS column_name,a.attidentity AS identity_kind,a.attgenerated AS generated_kind, "
                "a.attnotnull AS native_not_null,col_description(a.attrelid,a.attnum) AS column_comment, "
                "pg_get_expr(d.adbin,d.adrelid) AS expression "
                "FROM pg_attribute a JOIN pg_class t ON t.oid=a.attrelid "
                "JOIN pg_namespace n ON n.oid=t.relnamespace "
                "LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
                f"WHERE n.nspname={cs} AND t.relname={tn} AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum",
                errors,ref+" PostgreSQL native columns")
            by_name={r["column_name"]:r for r in extras}
            for column in result["columns"]:
                column["native_attributes"]=json_value(by_name.get(column.get("column_name"),{}))
        elif eng == "sqlserver":
            extras=query_safe(conn,
                "SELECT c.name AS column_name,c.column_id,c.is_identity,c.is_computed,c.is_sparse, "
                "c.is_rowguidcol,c.is_masked,c.generated_always_type_desc, "
                "dc.definition AS native_default, cc.definition AS computed_expression, "
                "CAST(ep.value AS nvarchar(4000)) AS column_comment "
                "FROM sys.columns c JOIN sys.objects o ON o.object_id=c.object_id "
                "JOIN sys.schemas s ON s.schema_id=o.schema_id "
                "LEFT JOIN sys.default_constraints dc ON dc.object_id=c.default_object_id "
                "LEFT JOIN sys.computed_columns cc ON cc.object_id=c.object_id AND cc.column_id=c.column_id "
                "LEFT JOIN sys.extended_properties ep ON ep.major_id=c.object_id AND ep.minor_id=c.column_id AND ep.name='MS_Description' "
                f"WHERE s.name={cs} AND o.name={tn} ORDER BY c.column_id",
                errors,ref+" SQL Server native columns")
            by_name={r["column_name"]:r for r in extras}
            for column in result["columns"]:
                column["native_attributes"]=json_value(by_name.get(column.get("COLUMN_NAME",column.get("column_name")),{}))
        if eng in ("mysql", "mariadb"):
            table_rows = query_safe(conn, f"SELECT * FROM information_schema.TABLES WHERE TABLE_SCHEMA={cs} AND TABLE_NAME={tn}", errors, ref+" table metadata")
            result["metadata"] = json_value(table_rows[0]) if table_rows else {}
            create_rows = query_safe(conn, f"SHOW CREATE {'VIEW' if obj['type'] == 'view' else 'TABLE'} {ident(eng, database)}.{ident(eng, name)}", errors, ref+" DDL")
            if create_rows:
                result["create_sql"] = next((str(v) for k, v in create_rows[0].items() if "Create " in k), result["create_sql"])
            if obj["type"] == "table":
                result["indexes"] = [json_value(r) for r in query_safe(conn, f"SHOW INDEX FROM {ident(eng, database)}.{ident(eng,name)}", errors, ref+" indexes")]
        elif eng == "postgresql":
            result["metadata"] = {"schema": schema_name}
            result["indexes"] = [json_value(r) for r in query_safe(conn,
                f"SELECT indexname, indexdef FROM pg_indexes WHERE schemaname={cs} AND tablename={tn} ORDER BY indexname",
                errors, ref+" indexes")]
        else:
            result["metadata"] = {"schema": schema_name}
            result["indexes"] = [json_value(r) for r in query_safe(conn,
                f"SELECT i.name AS index_name,i.is_unique,i.is_primary_key,i.type_desc,ic.key_ordinal,c.name AS column_name "
                f"FROM sys.indexes i JOIN sys.objects o ON i.object_id=o.object_id "
                f"JOIN sys.schemas s ON o.schema_id=s.schema_id "
                f"JOIN sys.index_columns ic ON i.object_id=ic.object_id AND i.index_id=ic.index_id "
                f"JOIN sys.columns c ON c.object_id=i.object_id AND c.column_id=ic.column_id "
                f"WHERE s.name={cs} AND o.name={tn} ORDER BY i.name,ic.key_ordinal",errors,ref+" indexes")]
        constraint_q = ("SELECT tc.CONSTRAINT_NAME, tc.CONSTRAINT_TYPE, kcu.COLUMN_NAME, kcu.ORDINAL_POSITION "
                        "FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc LEFT JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu "
                        "ON tc.CONSTRAINT_CATALOG=kcu.CONSTRAINT_CATALOG AND tc.CONSTRAINT_SCHEMA=kcu.CONSTRAINT_SCHEMA "
                        "AND tc.CONSTRAINT_NAME=kcu.CONSTRAINT_NAME AND tc.TABLE_SCHEMA=kcu.TABLE_SCHEMA AND tc.TABLE_NAME=kcu.TABLE_NAME "
                        f"WHERE tc.TABLE_SCHEMA={cs} AND tc.TABLE_NAME={tn} ORDER BY tc.CONSTRAINT_NAME,kcu.ORDINAL_POSITION")
        constraints = query_safe(conn, constraint_q, errors, ref+" constraints")
        constraints = [{k.lower():v for k,v in r.items()} for r in constraints]
        pk = [r for r in constraints if r["constraint_type"] == "PRIMARY KEY"]
        result["primary_key"] = [r["column_name"] for r in sorted(pk,key=lambda r:r.get("ordinal_position") or 0) if r.get("column_name")]
        result["unique_constraints"] = [json_value(r) for r in constraints if r["constraint_type"] == "UNIQUE"]
        result["check_constraints"] = [json_value(r) for r in constraints if r["constraint_type"] == "CHECK"]
        if eng == "postgresql":
            result["check_constraints"] = [json_value(r) for r in query_safe(conn,
                "SELECT con.conname, pg_get_constraintdef(con.oid) AS definition FROM pg_constraint con "
                "JOIN pg_class t ON t.oid=con.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
                f"WHERE con.contype='c' AND n.nspname={cs} AND t.relname={tn}", errors,ref+" checks")]
            if obj["type"] == "view":
                vrows=query_safe(conn,f"SELECT definition FROM pg_views WHERE schemaname={cs} AND viewname={tn}",errors,ref+" view DDL")
                if vrows: result["create_sql"] = vrows[0]["definition"]
        elif eng == "sqlserver":
            result["check_constraints"] = [json_value(r) for r in query_safe(conn,
                "SELECT c.name AS constraint_name,c.definition FROM sys.check_constraints c "
                "JOIN sys.objects o ON o.object_id=c.parent_object_id JOIN sys.schemas s ON s.schema_id=o.schema_id "
                f"WHERE s.name={cs} AND o.name={tn}",errors,ref+" checks")]
            drows=query_safe(conn,
                "SELECT m.definition FROM sys.sql_modules m JOIN sys.objects o ON o.object_id=m.object_id "
                "JOIN sys.schemas s ON s.schema_id=o.schema_id "
                f"WHERE s.name={cs} AND o.name={tn}",errors,ref+" DDL")
            if drows: result["create_sql"] = drows[0]["definition"]
        elif eng in ("mysql", "mariadb"):
            result["check_constraints"] = [json_value(r) for r in query_safe(conn,
                "SELECT cc.CONSTRAINT_NAME,cc.CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS cc "
                "JOIN information_schema.TABLE_CONSTRAINTS tc "
                "ON tc.CONSTRAINT_SCHEMA=cc.CONSTRAINT_SCHEMA AND tc.CONSTRAINT_NAME=cc.CONSTRAINT_NAME "
                f"WHERE tc.TABLE_SCHEMA={cs} AND tc.TABLE_NAME={tn}",errors,ref+" checks")]
        # FK metadata differs for PostgreSQL and SQL Server. Match by constraint and column ordinal.
        if eng in ("mysql", "mariadb"):
            fk_q = ("SELECT kcu.CONSTRAINT_NAME AS constraint_name, kcu.COLUMN_NAME AS source_column, kcu.ORDINAL_POSITION AS column_position, "
                    "kcu.REFERENCED_TABLE_SCHEMA AS target_database, kcu.REFERENCED_TABLE_NAME AS target_table, "
                    "kcu.REFERENCED_COLUMN_NAME AS target_column, rc.UPDATE_RULE AS update_rule, rc.DELETE_RULE AS delete_rule "
                    "FROM information_schema.KEY_COLUMN_USAGE kcu LEFT JOIN information_schema.REFERENTIAL_CONSTRAINTS rc "
                    "ON rc.CONSTRAINT_SCHEMA=kcu.CONSTRAINT_SCHEMA AND rc.CONSTRAINT_NAME=kcu.CONSTRAINT_NAME AND rc.TABLE_NAME=kcu.TABLE_NAME "
                    f"WHERE kcu.TABLE_SCHEMA={cs} AND kcu.TABLE_NAME={tn} AND kcu.REFERENCED_TABLE_NAME IS NOT NULL "
                    "ORDER BY kcu.CONSTRAINT_NAME,kcu.ORDINAL_POSITION")
        elif eng == "postgresql":
            fk_q = ("SELECT con.conname AS constraint_name, src.attname AS source_column, k.ord AS column_position, "
                    "tnsp.nspname AS target_schema, tt.relname AS target_table, dst.attname AS target_column, "
                    "CASE con.confupdtype WHEN 'a' THEN 'NO ACTION' WHEN 'r' THEN 'RESTRICT' WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL' WHEN 'd' THEN 'SET DEFAULT' END AS update_rule, "
                    "CASE con.confdeltype WHEN 'a' THEN 'NO ACTION' WHEN 'r' THEN 'RESTRICT' WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL' WHEN 'd' THEN 'SET DEFAULT' END AS delete_rule "
                    "FROM pg_constraint con JOIN pg_class st ON st.oid=con.conrelid JOIN pg_namespace snsp ON snsp.oid=st.relnamespace "
                    "JOIN pg_class tt ON tt.oid=con.confrelid JOIN pg_namespace tnsp ON tnsp.oid=tt.relnamespace "
                    "JOIN LATERAL unnest(con.conkey,con.confkey) WITH ORDINALITY AS k(srcatt,dstatt,ord) ON true "
                    "JOIN pg_attribute src ON src.attrelid=st.oid AND src.attnum=k.srcatt "
                    "JOIN pg_attribute dst ON dst.attrelid=tt.oid AND dst.attnum=k.dstatt "
                    f"WHERE con.contype='f' AND snsp.nspname={cs} AND st.relname={tn} ORDER BY con.conname,k.ord")
        else:
            fk_q = ("SELECT fk.name AS constraint_name, sc.name AS source_column, fkc.constraint_column_id AS column_position, "
                    "ts.name AS target_schema, tr.name AS target_table, tc.name AS target_column, "
                    "fk.update_referential_action_desc AS update_rule, fk.delete_referential_action_desc AS delete_rule "
                    "FROM sys.foreign_keys fk JOIN sys.foreign_key_columns fkc ON fk.object_id=fkc.constraint_object_id "
                    "JOIN sys.objects so ON so.object_id=fk.parent_object_id JOIN sys.schemas ss ON ss.schema_id=so.schema_id "
                    "JOIN sys.columns sc ON sc.object_id=so.object_id AND sc.column_id=fkc.parent_column_id "
                    "JOIN sys.objects tr ON tr.object_id=fk.referenced_object_id JOIN sys.schemas ts ON ts.schema_id=tr.schema_id "
                    "JOIN sys.columns tc ON tc.object_id=tr.object_id AND tc.column_id=fkc.referenced_column_id "
                    f"WHERE ss.name={cs} AND so.name={tn} ORDER BY fk.name,fkc.constraint_column_id")
        fk_rows = query_safe(conn, fk_q, errors, ref+" foreign keys")
        grouped: dict[str, dict[str, Any]] = {}
        for r in fk_rows:
            key = str(r["constraint_name"])
            item = grouped.setdefault(key, {"name":key,"source_database":database,"source_schema":schema,"source_table":name,
                "source_columns": [],"target_database":str(r.get("target_database") or database),
                "target_schema":str(r.get("target_schema") or (r.get("target_database") or schema)),
                "target_table":r["target_table"], "target_columns":[], "update_rule":r.get("update_rule"),"delete_rule":r.get("delete_rule")})
            item["source_columns"].append(r["source_column"])
            item["target_columns"].append(r["target_column"])
        result["foreign_keys"] = list(grouped.values())
    elif eng == "oracle":
        owner, t = lit(schema),lit(name)
        result["columns"] = [json_value(r) for r in query_safe(conn, f"SELECT * FROM ALL_TAB_COLUMNS WHERE OWNER={owner} AND TABLE_NAME={t} ORDER BY COLUMN_ID", errors, ref+" columns")]
        if obj["type"] == "table":
            rows = query_safe(conn, f"SELECT * FROM ALL_TABLES WHERE OWNER={owner} AND TABLE_NAME={t}", errors, ref+" metadata")
            result["metadata"] = json_value(rows[0]) if rows else {}
        else:
            rows = query_safe(conn, f"SELECT TEXT FROM ALL_VIEWS WHERE OWNER={owner} AND VIEW_NAME={t}",errors,ref+" view definition")
            if rows:
                result["create_sql"] = json_value(rows[0].get("TEXT"))
        cons = query_safe(conn, f"SELECT c.CONSTRAINT_NAME,c.CONSTRAINT_TYPE,c.DELETE_RULE,cc.COLUMN_NAME,cc.POSITION "
            f"FROM ALL_CONSTRAINTS c JOIN ALL_CONS_COLUMNS cc ON cc.OWNER=c.OWNER AND cc.CONSTRAINT_NAME=c.CONSTRAINT_NAME "
            f"WHERE c.OWNER={owner} AND c.TABLE_NAME={t} ORDER BY c.CONSTRAINT_NAME,cc.POSITION",errors,ref+" constraints")
        result["primary_key"] = [r["COLUMN_NAME"] for r in cons if r["CONSTRAINT_TYPE"]=="P"]
        result["unique_constraints"] = [json_value(r) for r in cons if r["CONSTRAINT_TYPE"]=="U"]
        result["check_constraints"] = [json_value(r) for r in query_safe(conn,
            f"SELECT CONSTRAINT_NAME,SEARCH_CONDITION_VC FROM ALL_CONSTRAINTS WHERE OWNER={owner} AND TABLE_NAME={t} AND CONSTRAINT_TYPE='C'", errors, ref+" checks")]
        result["indexes"] = [json_value(r) for r in query_safe(conn,
            f"SELECT i.INDEX_NAME,i.UNIQUENESS,i.INDEX_TYPE,c.COLUMN_NAME,c.COLUMN_POSITION "
            f"FROM ALL_INDEXES i JOIN ALL_IND_COLUMNS c ON i.OWNER=c.INDEX_OWNER AND i.INDEX_NAME=c.INDEX_NAME "
            f"WHERE i.TABLE_OWNER={owner} AND i.TABLE_NAME={t} ORDER BY i.INDEX_NAME,c.COLUMN_POSITION", errors,ref+" indexes")]
        fk_rows = query_safe(conn, f"SELECT c.CONSTRAINT_NAME,cc.COLUMN_NAME AS SOURCE_COLUMN,cc.POSITION AS COLUMN_POSITION, "
            f"r.OWNER AS TARGET_SCHEMA,r.TABLE_NAME AS TARGET_TABLE,rc.COLUMN_NAME AS TARGET_COLUMN,c.DELETE_RULE AS DELETE_RULE "
            f"FROM ALL_CONSTRAINTS c JOIN ALL_CONS_COLUMNS cc ON cc.OWNER=c.OWNER AND cc.CONSTRAINT_NAME=c.CONSTRAINT_NAME "
            f"JOIN ALL_CONSTRAINTS r ON r.OWNER=c.R_OWNER AND r.CONSTRAINT_NAME=c.R_CONSTRAINT_NAME "
            f"JOIN ALL_CONS_COLUMNS rc ON rc.OWNER=r.OWNER AND rc.CONSTRAINT_NAME=r.CONSTRAINT_NAME AND rc.POSITION=cc.POSITION "
            f"WHERE c.OWNER={owner} AND c.TABLE_NAME={t} AND c.CONSTRAINT_TYPE='R' ORDER BY c.CONSTRAINT_NAME,cc.POSITION",errors,ref+" foreign keys")
        grouped = {}
        for r in fk_rows:
            key = r["CONSTRAINT_NAME"]
            f = grouped.setdefault(key,{"name":key,"source_database":spec.service or spec.database,"source_schema":schema,
                "source_table":name,"source_columns":[],"target_database":spec.service or spec.database,
                "target_schema":r["TARGET_SCHEMA"],"target_table":r["TARGET_TABLE"],"target_columns":[],"delete_rule":r["DELETE_RULE"]})
            f["source_columns"].append(r["SOURCE_COLUMN"])
            f["target_columns"].append(r["TARGET_COLUMN"])
        result["foreign_keys"] = list(grouped.values())
    else:
        identname = ident(eng,name)
        # table_xinfo exposes generated/hidden columns unavailable in table_info.
        result["columns"] = [json_value(r) for r in query_safe(conn,f"PRAGMA table_xinfo({identname})",errors,ref+" columns")]
        rows = query_safe(conn, f"SELECT sql FROM sqlite_master WHERE name={lit(name)} AND type={lit(obj['type'])}",errors,ref+" DDL")
        result["create_sql"] = rows[0]["sql"] if rows else obj.get("definition")
        result["primary_key"] = [r["name"] for r in sorted(result["columns"],key=lambda x:x.get("pk") or 0) if r.get("pk")]
        idxs = query_safe(conn,f"PRAGMA index_list({identname})",errors,ref+" indexes")
        for r in idxs:
            detail = query_safe(conn,f"PRAGMA index_xinfo({ident(eng,r['name'])})",errors,ref+" index {r['name']}")
            result["indexes"].append({"metadata":json_value(r),"columns":json_value(detail)})
        result["unique_constraints"] = [i for i in result["indexes"] if i["metadata"].get("unique")]
        fk_rows = query_safe(conn,f"PRAGMA foreign_key_list({identname})",errors,ref+" foreign keys")
        grouped = {}
        for r in fk_rows:
            key = str(r["id"])
            f = grouped.setdefault(key,{"name":f"sqlite_fk_{name}_{key}","source_database":database,"source_schema":"main",
                "source_table":name,"source_columns":[],"target_database":database,"target_schema":"main",
                "target_table":r["table"],"target_columns":[],"update_rule":r["on_update"],"delete_rule":r["on_delete"],"match":r.get("match")})
            f["source_columns"].append(r["from"])
            target_column = r["to"]
            if target_column is None:
                # SQLite permits REFERENCES parent without naming PK columns.
                # PRAGMA foreign_key_list then emits NULL for the target column.
                referenced = query_safe(conn, f"PRAGMA table_xinfo({ident(eng,r['table'])})",
                                        errors, ref+" implicit referenced PK")
                primary_cols = [c["name"] for c in sorted(referenced, key=lambda c: c.get("pk") or 0)
                                if c.get("pk")]
                ordinal = int(r.get("seq") or 0)
                if ordinal < len(primary_cols):
                    target_column = primary_cols[ordinal]
                else:
                    errors.append(f"Could not resolve implicit SQLite FK target {ref} -> {r['table']}")
            f["target_columns"].append(target_column)
        result["foreign_keys"] = list(grouped.values())
        result["metadata"] = {"sqlite_sql":result["create_sql"], "note":"CHECK constraints are preserved in CREATE SQL; SQLite PRAGMA does not expose a structured CHECK catalog."}
    return result


def db_extra_metadata(conn: Any, spec: ConnectionSpec, database: str, errors: list[str]) -> dict[str, Any]:
    eng = spec.engine
    if eng in ("mysql","mariadb"):
        result = {
            "database": query_safe(conn,f"SELECT * FROM information_schema.SCHEMATA WHERE SCHEMA_NAME={lit(database)}",errors,"database metadata"),
            "triggers": query_safe(conn,f"SELECT * FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA={lit(database)}",errors,"triggers"),
            "routines": query_safe(conn,f"SELECT * FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA={lit(database)}",errors,"routines"),
            "events": query_safe(conn,f"SELECT * FROM information_schema.EVENTS WHERE EVENT_SCHEMA={lit(database)}",errors,"events"),
        }
    elif eng == "postgresql":
        result = {"database": query_safe(conn,"SELECT current_database() AS database, current_setting('server_version') AS version",errors,"db info"),
          "triggers": query_safe(conn,"SELECT * FROM information_schema.triggers WHERE trigger_schema NOT LIKE 'pg_%' AND trigger_schema <> 'information_schema'",errors,"triggers"),
          "routines": query_safe(conn,"SELECT * FROM information_schema.routines WHERE specific_schema NOT LIKE 'pg_%' AND specific_schema <> 'information_schema'",errors,"routines"),
          "events": [],"note":"PostgreSQL has no native scheduled SQL events; external schedulers are outside this connection."}
    elif eng == "sqlserver":
        result = {"database":query_safe(conn,"SELECT DB_NAME() AS database_name",errors,"db info"),
          "triggers":query_safe(conn,"SELECT s.name AS schema_name, o.name AS table_name, tr.name, tr.is_disabled, OBJECT_DEFINITION(tr.object_id) AS definition FROM sys.triggers tr JOIN sys.objects o ON o.object_id=tr.parent_id JOIN sys.schemas s ON s.schema_id=o.schema_id",errors,"triggers"),
          "routines":query_safe(conn,"SELECT s.name AS schema_name,o.name,o.type_desc,OBJECT_DEFINITION(o.object_id) AS definition FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id WHERE o.type IN ('P','FN','IF','TF')",errors,"routines"),
          "events":[],"note":"SQL Server Agent jobs are server-level objects, not equivalent to database-local scheduled events."}
    elif eng == "oracle":
        owner = lit(database)
        result = {"database":{"service":spec.service or spec.database,"owner":database},
          "triggers":query_safe(conn,f"SELECT * FROM ALL_TRIGGERS WHERE OWNER={owner}",errors,"triggers"),
          "routines":query_safe(conn,f"SELECT OBJECT_NAME,OBJECT_TYPE,STATUS FROM ALL_OBJECTS WHERE OWNER={owner} AND OBJECT_TYPE IN ('PROCEDURE','FUNCTION','PACKAGE','PACKAGE BODY')",errors,"routines"),
          "events":[],"note":"Oracle jobs require separate scheduler privileges and are not included by default."}
    else:
        result = {"database":{"file":str(Path(spec.path).expanduser()),"schema":"main"},
           "triggers":query_safe(conn,"SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name",errors,"triggers"),
           "routines":[],"events":[]}
    return json_value(result)


def estimate_size(conn: Any, spec: ConnectionSpec, database: str, obj: dict[str, str], errors: list[str]) -> int | None:
    eng, schema, table = spec.engine,obj["schema"],obj["name"]
    if eng in ("mysql","mariadb"):
        query = f"SELECT TABLE_ROWS AS estimate FROM information_schema.TABLES WHERE TABLE_SCHEMA={lit(database)} AND TABLE_NAME={lit(table)}"
    elif eng == "postgresql":
        query = ("SELECT GREATEST(c.reltuples,0)::bigint AS estimate FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                 f"WHERE n.nspname={lit(schema)} AND c.relname={lit(table)}")
    elif eng == "sqlserver":
        query = ("SELECT SUM(p.rows) AS estimate FROM sys.partitions p JOIN sys.objects o ON p.object_id=o.object_id "
                 "JOIN sys.schemas s ON s.schema_id=o.schema_id "
                 f"WHERE p.index_id IN (0,1) AND s.name={lit(schema)} AND o.name={lit(table)}")
    elif eng == "oracle":
        query = f"SELECT NUM_ROWS AS estimate FROM ALL_TABLES WHERE OWNER={lit(schema)} AND TABLE_NAME={lit(table)}"
    else:
        return None
    try:
        rows=execute_rows(conn,query)
        value=rows[0].get("estimate") if rows else None
        return int(value) if value is not None else None
    except Exception:
        return None


def exact_row_count(conn: Any, spec: ConnectionSpec, database: str, obj: dict[str, str],
                    errors: list[str]) -> int | None:
    """COUNT(*) is exact at query time. Errors are never confused with zero rows."""
    eng = spec.engine
    q = lambda name: quote_ident(eng, name)
    ref = f"{database}.{obj['schema']}.{obj['name']}"
    table_ref = (f"{q(database)}.{q(obj['name'])}" if eng in ("mysql", "mariadb") else
                 f"{q(obj['schema'])}.{q(obj['name'])}" if eng != "sqlite" else q(obj["name"]))
    try:
        result = execute_rows(conn, f"SELECT COUNT(*) AS total_rows FROM {table_ref}")
        count = next(iter(result[0].values()))
        if count is None or int(count) < 0:
            raise ValueError("Invalid exact COUNT(*) result")
        return int(count)
    except Exception as exc:
        errors.append(f"Exact row count failed {ref}: {type(exc).__name__}: {exc}")
        return None


def qualified_table(spec: ConnectionSpec, database: str, obj: dict[str, str]) -> str:
    q = lambda name: quote_ident(spec.engine, name)
    if spec.engine in ("mariadb", "mysql"):
        return f"{q(database)}.{q(obj['name'])}"
    if spec.engine == "sqlite":
        return q(obj["name"])
    return f"{q(obj['schema'])}.{q(obj['name'])}"


def _integer_pk(metadata: dict[str, Any], engine: str) -> str | None:
    """Only single integer primary keys support efficient random index pivots."""
    pk = metadata["primary_key"]
    if len(pk) != 1:
        return None
    name = pk[0]
    for col in metadata["columns"]:
        norm = {str(k).lower(): v for k, v in col.items()}
        col_name = norm.get("column_name", norm.get("name"))
        if col_name != name:
            continue
        dtype = str(norm.get("data_type", norm.get("type", ""))).lower().strip()
        if dtype in {"integer", "int", "bigint", "smallint", "tinyint", "mediumint", "int2", "int4", "int8"}:
            return name
        if engine == "oracle" and dtype == "number" and norm.get("data_scale", norm.get("scale")) in (None, 0):
            return name
    return None


def random_pk_pivot_sample(conn: Any, spec: ConnectionSpec, database: str, obj: dict[str, str],
                           metadata: dict[str, Any], limit: int, errors: list[str]) -> list[dict[str, Any]] | None:
    """Index-friendly approximate random sampling. NOT uniform when key gaps exist."""
    pk = _integer_pk(metadata, spec.engine)
    if not pk:
        return None
    table_ref = qualified_table(spec, database, obj)
    qpk = quote_ident(spec.engine, pk)
    try:
        bounds = execute_rows(conn, f"SELECT MIN({qpk}) AS min_id, MAX({qpk}) AS max_id FROM {table_ref}")
        if not bounds or bounds[0]["min_id"] is None:
            return []
        lo, hi = int(bounds[0]["min_id"]), int(bounds[0]["max_id"])
        selected: dict[int, dict[str, Any]] = {}
        attempts = max(100, limit * 30)
        for _ in range(attempts):
            if len(selected) >= limit:
                break
            pivot = lo + secrets.randbelow(hi - lo + 1)
            if spec.engine == "sqlserver":
                query = f"SELECT TOP (1) * FROM {table_ref} WHERE {qpk} >= {pivot} ORDER BY {qpk}"
            elif spec.engine == "oracle":
                query = f"SELECT * FROM {table_ref} WHERE {qpk} >= {pivot} ORDER BY {qpk} FETCH FIRST 1 ROWS ONLY"
            else:
                query = f"SELECT * FROM {table_ref} WHERE {qpk} >= {pivot} ORDER BY {qpk} LIMIT 1"
            candidate = execute_rows(conn, query)
            if candidate:
                selected[int(candidate[0][pk])] = candidate[0]
        # Shuffle insertion order, not SQL key order. Duplicates are eliminated by PK.
        result = list(selected.values())
        secrets.SystemRandom().shuffle(result)
        return result
    except Exception as exc:
        errors.append(f"Index-pivot sample failed {database}.{obj['schema']}.{obj['name']}: {type(exc).__name__}: {exc}")
        return None


def sample_table(conn: Any, spec: ConnectionSpec, database: str, obj: dict[str, str],
                 metadata: dict[str, Any], limit: int, expensive: bool, threshold: int,
                 exact_count: int | None, errors: list[str], warnings: list[str],
                 compact_large_values: bool = False, max_value_bytes: int = 4096
                 ) -> tuple[list[dict[str, Any]], str, str]:
    """Sample without expensive random sorts on large indexed tables by default.

    Large-table index pivot samples are non-uniform if key values have gaps;
    the method is explicitly recorded, never presented as uniformly random.
    """
    eng = spec.engine
    if obj["type"] != "table":
        return [], "not_applicable", "none"
    if limit == 0:
        return [], "disabled", "none"
    estimate = exact_count if exact_count is not None else estimate_size(conn, spec, database, obj, errors)
    if exact_count == 0:
        return [], "ok", "all_rows"
    if estimate is not None and estimate > threshold and not expensive:
        result = random_pk_pivot_sample(conn, spec, database, obj, metadata, limit, errors)
        if result is not None:
            status = "ok" if len(result) == limit else "partial"
            if status == "partial":
                warnings.append(f"Index-pivot sample returned {len(result)}/{limit} rows for {database}.{obj['name']}")
            warnings.append(f"Approximate non-uniform index-pivot sample used for {database}.{obj['name']}; key gaps can bias selection")
            return ([json_value(r, compact_large_values=compact_large_values, max_value_bytes=max_value_bytes) for r in result],
                    status, "approximate_pk_pivot")
        warnings.append(f"Large table {database}.{obj['name']} ({estimate} rows), no usable integer PK: randomized full sort not run without expensive_sampling=true")
        return [], "skipped_large_table", "none"
    tbl = qualified_table(spec, database, obj)
    if eng in ("mysql", "mariadb"):
        sql = f"SELECT * FROM {tbl} ORDER BY RAND() LIMIT {limit}"
    elif eng == "sqlserver":
        sql = f"SELECT TOP ({limit}) * FROM {tbl} ORDER BY NEWID()"
    elif eng == "oracle":
        sql = f"SELECT * FROM {tbl} ORDER BY DBMS_RANDOM.VALUE FETCH FIRST {limit} ROWS ONLY"
    else:
        sql = f"SELECT * FROM {tbl} ORDER BY random() LIMIT {limit}"
    try:
        rows = execute_rows(conn, sql)
        return ([json_value(r, compact_large_values=compact_large_values, max_value_bytes=max_value_bytes) for r in rows],
                "ok", "random_sort")
    except Exception as exc:
        errors.append(f"Sample failed {database}.{obj['schema']}.{obj['name']}: {type(exc).__name__}: {exc}")
        return [], "error", "random_sort"


def _restore_sample_value(value: Any) -> Any:
    if not isinstance(value, dict) or "$type" not in value:
        return value
    typ = value["$type"]
    if typ == "decimal":
        return Decimal(value["value"])
    if typ == "uuid":
        return str(value["value"])
    if typ == "base64":
        return base64.b64decode(value["value"])
    if typ in ("datetime", "date", "time"):
        return {"datetime": dt.datetime.fromisoformat, "date": dt.date.fromisoformat,
                "time": dt.time.fromisoformat}[typ](value["value"])
    return None  # lossy compact values or unsupported types cannot form FK bindings


def _lookup_parent(conn: Any, spec: ConnectionSpec, database: str, obj: dict[str, str],
                   columns: list[str], values: tuple[Any, ...]) -> dict[str, Any] | None:
    if any(value is None or isinstance(value, (dict, list)) for value in values):
        return None
    eng = spec.engine
    placeholder = ":{}" if eng == "oracle" else "?" if eng == "sqlite" else "%s"
    where = " AND ".join(f"{quote_ident(eng, name)} = {placeholder.format(i + 1)}" for i, name in enumerate(columns))
    table_ref = qualified_table(spec, database, obj)
    if eng == "sqlserver":
        query = f"SELECT TOP (1) * FROM {table_ref} WHERE {where}"
    elif eng == "oracle":
        query = f"SELECT * FROM {table_ref} WHERE {where} FETCH FIRST 1 ROWS ONLY"
    else:
        query = f"SELECT * FROM {table_ref} WHERE {where} LIMIT 1"
    cursor = conn.cursor()
    try:
        cursor.execute(query, values)
        row = cursor.fetchone()
        return dict(zip((desc[0] for desc in cursor.description), row)) if row is not None else None
    finally:
        cursor.close()


def enrich_related_samples(conn: Any, spec: ConnectionSpec, database: str, rec: dict[str, Any],
                           limit: int, warnings: list[str], compact_large_values: bool,
                           max_value_bytes: int) -> dict[str, int]:
    """Optionally replace random parent rows with parents of sampled child rows.

    Never pulls in tables outside the selected export scope, never exceeds limit,
    never mutates SQL data. Coherence cannot be complete if more parent keys are
    needed than the sample limit; this is surfaced in the report.
    """
    stats = {"related_parent_rows_added": 0, "unresolved_links": 0, "outside_scope_links": 0}
    index = {(sch, table): obj for sch, group in rec["schemas"].items() for table, obj in group["tables"].items()}
    protected: dict[tuple[str, str], set[str]] = {key: set() for key in index}
    if limit == 0:
        return stats
    # Revisit newly included parent samples for deeper relationship chains, bounded by table count.
    for _ in range(max(1, len(index))):
        changed = False
        for (source_schema, source_name), source in index.items():
            for fk in source["foreign_keys"]:
                if spec.engine != "oracle" and fk.get("target_database") not in (database, None):
                    continue
                target_key = (fk.get("target_schema") or source_schema, fk["target_table"])
                parent = index.get(target_key)
                if parent is None:
                    continue
                source_cols, target_cols = fk["source_columns"], fk["target_columns"]
                for child in list(source["sample_rows"]):
                    values = tuple(_restore_sample_value(child.get(c)) for c in source_cols)
                    if any(v is None for v in values):
                        continue
                    def key_for(row: dict[str, Any]) -> tuple[Any, ...]:
                        return tuple(_restore_sample_value(row.get(c)) for c in target_cols)
                    existing_match = next((existing for existing in parent["sample_rows"]
                                           if key_for(existing) == values), None)
                    if existing_match is not None:
                        protected[target_key].add(json.dumps(key_for(existing_match), sort_keys=True, default=str))
                        continue
                    if len(protected[target_key]) >= limit:
                        stats["unresolved_links"] += 1
                        continue
                    try:
                        actual = _lookup_parent(conn, spec, database,
                            {"schema": target_key[0], "name": target_key[1]}, target_cols, values)
                    except Exception as exc:
                        warnings.append(f"Related-parent lookup failed {database}.{target_key[1]}: {type(exc).__name__}: {exc}")
                        stats["unresolved_links"] += 1
                        continue
                    if actual is None:
                        stats["unresolved_links"] += 1
                        continue
                    converted = json_value(actual, compact_large_values=compact_large_values,
                                           max_value_bytes=max_value_bytes)
                    fingerprint = json.dumps(key_for(converted), sort_keys=True, default=str)
                    if len(parent["sample_rows"]) >= limit:
                        evict_index = next((i for i, row in enumerate(parent["sample_rows"])
                                            if json.dumps(key_for(row), sort_keys=True, default=str) not in protected[target_key]), None)
                        if evict_index is None:
                            stats["unresolved_links"] += 1
                            continue
                        del parent["sample_rows"][evict_index]
                    parent["sample_rows"].append(converted)
                    protected[target_key].add(fingerprint)
                    parent["sample_count"] = len(parent["sample_rows"])
                    parent["sample_method"] = "mixed_random_related"
                    stats["related_parent_rows_added"] += 1
                    changed = True
        if not changed:
            break
    # Recalculate unresolved links against the final output, never count the
    # same unresolved relationship repeatedly across propagation passes.
    final_unresolved: set[str] = set()
    outside: set[str] = set()
    for (source_schema, source_name), source in index.items():
        for fk in source["foreign_keys"]:
            target_key = (fk.get("target_schema") or source_schema, fk["target_table"])
            if (spec.engine != "oracle" and fk.get("target_database") not in (database, None)) or target_key not in index:
                outside.add(f"{source_schema}.{source_name}:{fk['name']}")
                continue
            parent = index[target_key]
            for child in source["sample_rows"]:
                values = tuple(_restore_sample_value(child.get(c)) for c in fk["source_columns"])
                if any(v is None for v in values):
                    continue
                if not any(tuple(_restore_sample_value(row.get(c)) for c in fk["target_columns"]) == values
                           for row in parent["sample_rows"]):
                    final_unresolved.add(json.dumps([source_schema, source_name, fk["name"], values], default=str))
    stats["unresolved_links"] = len(final_unresolved)
    stats["outside_scope_links"] = len(outside)
    if stats["unresolved_links"]:
        warnings.append(f"Related-sample coherence incomplete: {stats['unresolved_links']} missing parent samples")
    return stats


def validate_document(out: dict[str, Any], selected: dict[str, list[dict[str, str]]], limit: int,
                      issues: list[str]) -> dict[str, Any]:
    """Independent checks over JSON-bound structure. Does not claim server snapshot consistency."""
    val = out["validation"]
    dbs = out["databases"]
    if set(dbs) != set(selected):
        issues.append(f"Exported databases {list(dbs)} do not match targeted databases {list(selected)}")
    total_tables = 0
    total_samples = 0
    total_fk = 0
    collected_relationships: list[dict[str, Any]] = []
    all_exact = True
    per_db: dict[str, Any] = {}
    for name, objects in selected.items():
        rec = dbs.get(name)
        if rec is None:
            all_exact = False
            continue
        expected = {(o["schema"], o["name"], o["type"]) for o in objects}
        actual = {(sch, obj_name, "table" if kind == "tables" else "view")
                  for sch, sc in rec["schemas"].items() for kind in ("tables", "views") for obj_name in sc[kind]}
        if expected != actual:
            issues.append(f"Object mismatch {name}: missing={sorted(expected-actual)}, unexpected={sorted(actual-expected)}")
        table_count = 0
        sample_count = 0
        foreign_key_count = 0
        known_rows = 0
        unknown = 0
        for sch, sc in rec["schemas"].items():
            for table_name, obj in sc["tables"].items():
                table_count += 1
                samples = obj["sample_rows"]
                sample_count += len(samples)
                foreign_key_count += len(obj["foreign_keys"])
                collected_relationships.extend(obj["foreign_keys"])
                if obj["sample_count"] != len(samples) or len(samples) > limit:
                    issues.append(f"Invalid sample count {name}.{sch}.{table_name}")
                col_names = {str(c.get("COLUMN_NAME", c.get("column_name", c.get("name", "")))) for c in obj["columns"]}
                if any(set(row) != col_names for row in samples):
                    issues.append(f"Row/column shape mismatch {name}.{sch}.{table_name}")
                if not col_names:
                    issues.append(f"Missing columns {name}.{sch}.{table_name}")
                for fk in obj["foreign_keys"]:
                    if len(fk["source_columns"]) != len(fk["target_columns"]):
                        issues.append(f"Mismatched FK column pairs {name}.{sch}.{table_name}")
                    if not set(fk["source_columns"]).issubset(col_names):
                        issues.append(f"FK source columns missing {name}.{sch}.{table_name}")
                    target_db = fk.get("target_database")
                    target_sch = fk.get("target_schema")
                    target_tab = fk.get("target_table")
                    target_rec = dbs.get(target_db) if target_db else None
                    target_obj = (target_rec["schemas"].get(target_sch, {}).get("tables", {}).get(target_tab)
                                  if target_rec else None)
                    if target_obj is not None:
                        target_names = {str(c.get("COLUMN_NAME", c.get("column_name", c.get("name", ""))))
                                        for c in target_obj["columns"]}
                        if not set(fk["target_columns"]).issubset(target_names):
                            issues.append(f"FK target columns missing {name}.{sch}.{table_name}")
                n = obj.get("total_rows")
                if n is None:
                    unknown += 1
                else:
                    known_rows += n
                    if n < len(samples):
                        issues.append(f"Samples exceed exact count {name}.{sch}.{table_name}")
                    if obj["sample_status"] == "ok" and len(samples) != min(n, limit):
                        issues.append(f"Sample count differs from exact row count {name}.{sch}.{table_name}")
        if table_count != rec["table_count"]:
            issues.append(f"Declared table count mismatch {name}")
        if sum(len(sc["views"]) for sc in rec["schemas"].values()) != rec["view_count"]:
            issues.append(f"Declared view count mismatch {name}")
        total_tables += table_count
        total_samples += sample_count
        total_fk += foreign_key_count
        all_exact &= unknown == 0
        per_db[name] = {"tables": table_count, "views": rec["view_count"],
                        "columns": sum(len(o["columns"]) for sc in rec["schemas"].values()
                                       for kind in ("tables", "views") for o in sc[kind].values()),
                        "foreign_keys": foreign_key_count, "sampled_rows": sample_count,
                        "known_total_rows": known_rows, "total_rows_exact": known_rows if unknown == 0 else None,
                        "unknown_row_count_tables": unknown}
    if val["table_count"] != total_tables or val["sampled_rows"] != total_samples or val["foreign_key_count"] != total_fk:
        issues.append("Validation counter mismatch")
    if len(out["relationships"]) != total_fk or out["relationships"] != collected_relationships:
        issues.append("Consolidated FK relationships mismatch")
    val["per_database"] = per_db
    val["row_counts_complete"] = all_exact and not any("count failed" in x.lower() for x in val["errors"])
    val["sampled_rows"] = total_samples
    val["table_count"] = total_tables
    val["foreign_key_count"] = total_fk
    return {"database_count": len(dbs), "table_count": total_tables,
            "view_count": sum(r["view_count"] for r in dbs.values()),
            "column_count": sum(s["columns"] for s in per_db.values()),
            "foreign_key_count": total_fk, "sampled_rows": total_samples,
            "known_total_rows": sum(s["known_total_rows"] for s in per_db.values()),
            "total_rows_exact": sum(s["known_total_rows"] for s in per_db.values()) if all_exact else None,
            "unknown_row_count_tables": sum(s["unknown_row_count_tables"] for s in per_db.values()),
            "per_database": per_db}


def _select_objects(objects: list[dict[str, str]], scope: str, database: str,
                    schema: str, table: str, table_names: list[str] | None) -> list[dict[str, str]]:
    if scope == "schema":
        selected = [o for o in objects if o["schema"] == schema]
        if not selected:
            raise ValueError(f"Schema {schema!r} contains no discoverable objects in {database!r}")
        return selected
    if scope not in ("table", "tables"):
        return objects
    requests = [table] if scope == "table" else list(table_names or [])
    chosen: list[dict[str, str]] = []
    for target in requests:
        # First prefer exact names (a name may literally contain a period).
        matches = [o for o in objects if o["name"] == target and (not schema or o["schema"] == schema)]
        if not matches:
            matches = [o for o in objects if f"{o['schema']}.{o['name']}" == target and (not schema or o["schema"] == schema)]
        if len(matches) != 1:
            raise ValueError(f"Object {target!r} not found uniquely in {database!r}; specify schema or qualified schema.table")
        if any(o["schema"] == matches[0]["schema"] and o["name"] == matches[0]["name"] for o in chosen):
            raise ValueError(f"Duplicate requested object: {target!r}")
        chosen.append(matches[0])
    return chosen


def _filter_extra_metadata(metadata: dict[str, Any], scope: str,
                           objects: list[dict[str, str]]) -> dict[str, Any]:
    if scope in ("server", "databases", "database"):
        return metadata
    targets = {(o["schema"], o["name"]) for o in objects}
    selected_names = {o["name"] for o in objects}
    filtered = []
    for trigger in metadata.get("triggers", []):
        norm = {str(key).lower(): value for key, value in trigger.items()}
        tab = norm.get("event_object_table") or norm.get("tbl_name") or norm.get("table_name")
        sch = norm.get("trigger_schema") or norm.get("event_object_schema") or norm.get("schema_name") or norm.get("owner")
        if tab in selected_names and (sch is None or (str(sch), str(tab)) in targets):
            filtered.append(trigger)
    metadata["triggers"] = filtered
    metadata["routines"] = []  # Not scoped to a particular table; avoid unrelated definitions.
    metadata["events"] = []
    metadata["scope_filtered"] = True
    return metadata


def _cleanup_export_directory(path: Path) -> None:
    """Dedicated export/ contains completed JSON documents only.

    Never remove earlier JSON exports or any subdirectories. Remove transient
    files and legacy CSVs only after a new JSON has been published successfully.
    """
    for item in path.iterdir():
        if item.is_file() and item.suffix.lower() != ".json":
            item.unlink()


def export_database(spec: ConnectionSpec, scope: str, database: str = "", schema: str = "", table: str = "",
                    database_names: list[str] | None = None, table_names: list[str] | None = None,
                    sample_limit: int = 20, expensive_sampling: bool = False, sample_threshold: int = 250000,
                    coherent_samples: bool = True, compact_large_values: bool = False,
                    max_value_bytes: int = 4096, output_dir: str | Path = "export",
                    now: dt.datetime | None = None, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Write exactly one complete JSON artifact for the requested scope.

    Older JSON exports are preserved; no CSV, logs, or other file is retained in
    the dedicated output directory. SQL data is never changed.
    """
    scopes = ("server", "databases", "database", "schema", "table", "tables")
    if scope not in scopes:
        raise ValueError(f"scope must be one of {', '.join(scopes)}")
    if scope == "databases" and (not database_names or not all(isinstance(d, str) and d.strip() for d in database_names)):
        raise ValueError("scope databases requires a nonempty list of exact database names")
    if scope == "databases" and len(database_names or []) != len(set(database_names or [])):
        raise ValueError("Duplicate database names supplied")
    if scope == "tables" and (not table_names or not all(isinstance(t, str) and t.strip() for t in table_names)):
        raise ValueError("scope tables requires a nonempty list of exact table names")
    if scope == "tables" and len(table_names or []) != len(set(table_names or [])):
        raise ValueError("Duplicate table names supplied")
    if not 0 <= sample_limit <= 20:
        raise ValueError("sample_limit must be between 0 and 20")
    if not 100 <= sample_threshold <= 1_000_000_000:
        raise ValueError("sample_threshold out of supported range")
    if not 256 <= max_value_bytes <= 100_000_000:
        raise ValueError("max_value_bytes must be in 256..100000000")
    if scope in ("database", "schema", "table", "tables") and not database and spec.engine != "sqlite":
        raise ValueError("Exact database is required for database/schema/table/tables scope")
    if scope == "schema" and not schema:
        raise ValueError("Exact schema is required for schema scope")
    if scope == "table" and not table:
        raise ValueError("Exact table is required for table scope")
    now = now or dt.datetime.now().astimezone()
    errors: list[str] = []
    warnings: list[str] = []
    issues: list[str] = []
    out: dict[str, Any] = {
        "export_metadata": {"created_at": now.isoformat(), "engine": spec.engine,
            "server": spec.server_label or (Path(spec.path).stem if spec.engine == "sqlite" else spec.host),
            "scope": scope, "requested_database": database or None, "requested_databases": database_names or None,
            "requested_schema": schema or None, "requested_table": table or None,
            "requested_tables": table_names or None, "sample_limit_per_table": sample_limit,
            "sampling_strategy": "random_sort_small_approximate_integer_pk_large",
            "coherent_samples": coherent_samples, "compact_large_values": compact_large_values,
            "max_value_bytes": max_value_bytes if compact_large_values else None,
            "row_count_method": "COUNT(*) per table, nontransactional per-query snapshot",
            "row_encoding": {"decimal": "tagged lossless decimal string", "binary": "base64 tagged object",
                             "date_time": "ISO 8601 tagged object"},
            "security": "local JSON only; contains potentially sensitive real database rows"},
        "databases": {}, "relationships": [],
        "validation": {"errors": errors, "warnings": warnings, "issues": issues,
            "object_counts": {}, "sampled_rows": 0, "table_count": 0,
            "foreign_key_count": 0, "schema_complete": False, "sampling_complete": False,
            "row_counts_complete": False}}
    connect_db = database if scope in ("database", "schema", "table", "tables") and spec.engine not in ("oracle", "sqlite") else None
    root = open_connection(spec, connect_db)
    selected: dict[str, list[dict[str, str]]] = {}
    sample_states: list[str] = []
    try:
        out["export_metadata"]["version"] = engine_version(root, spec.engine)
        found = accessible_databases(root, spec, errors)
        if scope == "server":
            targets = found
        elif scope == "databases":
            targets = list(database_names or [])
            missing = [name for name in targets if name not in found]
            if missing:
                raise ValueError(f"Requested databases not accessible: {missing}. Available: {found}")
        else:
            targets = [database or Path(spec.path).name]
            if spec.engine == "oracle" and targets[0] not in found:
                warnings.append(f"Oracle schema {targets[0]!r} not listed among visible owners; direct query attempted")
            elif spec.engine not in ("sqlite", "oracle") and targets[0] not in found:
                raise ValueError(f"Database {targets[0]!r} not accessible. Available: {found}")
        if not targets:
            raise ValueError("No application databases/schemas accessible; no export created")
        out["export_metadata"]["database_count"] = len(targets)
        for db in targets:
            selected[db] = []
            if progress:
                progress(f"Inspecting {db}")
            current = root if spec.engine in ("sqlite", "oracle") or (spec.database == db and scope != "server") else None
            owned = False
            if current is None:
                try:
                    current = open_connection(spec, db)
                    owned = True
                except Exception as exc:
                    errors.append(f"Database {db}: connection failed: {type(exc).__name__}: {exc}")
                    continue
            try:
                objects = list_objects(current, spec, db, errors)
                objects = _select_objects(objects, scope, db, schema, table, table_names)
                selected[db] = objects
                meta = db_extra_metadata(current, spec, db, errors)
                meta = _filter_extra_metadata(meta, scope, objects)
                rec: dict[str, Any] = {"metadata": meta, "schemas": {}, "table_count": 0, "view_count": 0}
                for item in objects:
                    sch = rec["schemas"].setdefault(item["schema"], {"tables": {}, "views": {}})
                    table_obj = table_metadata(current, spec, db, item, errors)
                    if item["type"] == "table":
                        table_obj["estimated_rows"] = estimate_size(current, spec, db, item, errors)
                        table_obj["total_rows"] = exact_row_count(current, spec, db, item, errors)
                        table_obj["row_count_status"] = "exact" if table_obj["total_rows"] is not None else "error"
                        rows, status, method = sample_table(current, spec, db, item, table_obj,
                            sample_limit, expensive_sampling, sample_threshold,
                            table_obj["total_rows"], errors, warnings, compact_large_values, max_value_bytes)
                        table_obj["sample_rows"] = rows
                        table_obj["sample_count"] = len(rows)
                        table_obj["sample_status"] = status
                        table_obj["sample_method"] = method
                        sample_states.append(status)
                        rec["table_count"] += 1
                        out["validation"]["sampled_rows"] += len(rows)
                        out["validation"]["table_count"] += 1
                        out["validation"]["foreign_key_count"] += len(table_obj["foreign_keys"])
                        out["relationships"].extend(table_obj["foreign_keys"])
                    else:
                        rec["view_count"] += 1
                    sch["tables" if item["type"] == "table" else "views"][item["name"]] = table_obj
                if coherent_samples:
                    rec["coherent_sampling"] = enrich_related_samples(current, spec, db, rec,
                        sample_limit, warnings, compact_large_values, max_value_bytes)
                    # It may replace / add parent samples, recompute the authoritative count.
                    out["validation"]["sampled_rows"] = sum(
                        len(o["sample_rows"]) for r in list(out["databases"].values()) + [rec]
                        for sc in r["schemas"].values() for o in sc["tables"].values())
                out["databases"][db] = rec
                out["validation"]["object_counts"][db] = {"tables": rec["table_count"], "views": rec["view_count"]}
            finally:
                if owned:
                    current.close()
    finally:
        root.close()
    out["summary"] = validate_document(out, selected, sample_limit, issues)
    # Differentiate missing/failed metadata from independent COUNT and SAMPLE failures.
    metadata_errors = [e for e in errors if not e.startswith(("Exact row count failed", "Sample failed", "Index-pivot sample failed"))]
    sample_issues = [i for i in issues if i.startswith(("Invalid sample count", "Row/column shape mismatch",
                    "Samples exceed exact count", "Sample count differs"))]
    schema_issues = [i for i in issues if i not in sample_issues]
    out["validation"]["schema_complete"] = not metadata_errors and not schema_issues
    out["validation"]["sampling_complete"] = (not any(x not in ("ok", "disabled") for x in sample_states)
        and not any(e.startswith(("Sample failed", "Index-pivot sample failed")) for e in errors)
        and not sample_issues)
    if any(rec.get("coherent_sampling", {}).get("unresolved_links", 0) for rec in out["databases"].values()):
        out["validation"]["sampling_complete"] = False
    out["validation"]["integrity_verified"] = not issues
    # Only emit a success when all requested aspects are complete; report partial otherwise.
    status = "ok" if (out["validation"]["schema_complete"] and out["validation"]["sampling_complete"]
                       and out["validation"]["row_counts_complete"]) else "partial"
    out_path = Path(output_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y-%m-%d_%H-%M")
    parts = [stamp, slug(out["export_metadata"]["server"])]
    if scope in ("database", "schema", "table", "tables"):
        parts.append(slug(database or Path(spec.path).stem))
    elif scope == "databases":
        parts.extend(slug(name) for name in targets[:3])
        if len(targets) > 3:
            parts.append(f"and_{len(targets)-3}_more")
    if scope == "schema":
        parts.append(slug(schema))
    if scope == "table":
        if schema and schema not in (database, "main"):
            parts.append(slug(schema))
        parts.append(slug(table))
    if scope == "tables":
        parts.append("selected_tables")
    stem = "_".join(parts)
    dest = out_path / f"{stem}.json"
    counter = 2
    while dest.exists():
        dest = out_path / f"{stem}_{counter}.json"
        counter += 1
    tmp = out_path / ("." + dest.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(out, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        # Re-parse the actual bytes, and independently inspect the on-disk structure.
        with tmp.open("r", encoding="utf-8") as handle:
            parsed = json.load(handle)
        disk_issues: list[str] = []
        validate_document(parsed, selected, sample_limit, disk_issues)
        if disk_issues or parsed["summary"] != out["summary"]:
            raise RuntimeError(f"Serialized JSON validation failed: {disk_issues}")
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    _cleanup_export_directory(out_path)
    return {"status": status, "json": str(dest), "statistics": out["validation"],
            "summary": out["summary"], "database_names": list(out["databases"]),
            "note": "One validated JSON file published. Prior JSON exports retained. No sample values sent to model."}
