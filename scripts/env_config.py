"""Parse dotenv and connection URLs without exposing credentials or running shell code.

Only a deliberately supplied text body or a repository-local .local/ file can
be parsed. Never expand environment variables or execute dotenv expressions.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import parse_qs, unquote, urlsplit

from db_engine import ConnectionSpec

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_ENV_BYTES = 128 * 1024
KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SUPPORTED_SCHEMES = {
    "mysql": "mysql", "mariadb": "mariadb", "postgres": "postgresql",
    "postgresql": "postgresql", "mssql": "sqlserver", "sqlsrv": "sqlserver", "sqlserver": "sqlserver",
    "oracle": "oracle", "sqlite": "sqlite", "sqlite3": "sqlite",
}
PORT_ENGINE = {3306: "mysql", 5432: "postgresql", 1433: "sqlserver", 1521: "oracle"}
ENGINE_VARIABLES = ("DB_CONNECTION", "DB_DRIVER", "DB_ENGINE", "DATABASE_ENGINE", "DATABASE_TYPE", "DB_TYPE")
URL_VARIABLES = ("DATABASE_URL", "DB_URL", "SQLALCHEMY_DATABASE_URI", "MYSQL_URL", "MARIADB_URL", "POSTGRES_URL", "POSTGRESQL_URL", "SQLSERVER_URL", "MSSQL_URL", "ORACLE_URL", "SQLITE_URL")


@dataclass
class EnvResolution:
    spec: ConnectionSpec
    source: str
    recognized_keys: list[str]
    warnings: list[str]


def _raw_value(source: str) -> str:
    source = source.strip()
    if not source:
        return ""
    if source[0] in ('"', "'"):
        quote = source[0]
        buf: list[str] = []
        escaped = False
        end = None
        for i, c in enumerate(source[1:], 1):
            if escaped:
                # Recognize only escaped quote/backslash. Preserve all other escapes.
                buf.append(c if c in (quote, "\\") else "\\" + c)
                escaped = False
            elif c == "\\" and quote == '"':
                escaped = True
            elif c == quote:
                end = i
                break
            else:
                buf.append(c)
        if end is None:
            raise ValueError("Unterminated quoted value in dotenv input")
        tail = source[end + 1:].strip()
        if tail and not tail.startswith("#"):
            raise ValueError("Unexpected characters after a quoted dotenv value")
        return "".join(buf)
    # An inline # only starts a comment when preceded by whitespace.
    return re.split(r"\s+#", source, maxsplit=1)[0].strip()


def parse_dotenv(content: str) -> dict[str, str]:
    if not isinstance(content, str):
        raise ValueError("Dotenv input must be text")
    if len(content.encode("utf-8")) > MAX_ENV_BYTES:
        raise ValueError("Dotenv content exceeds 128 KiB")
    found: dict[str, str] = {}
    for line_number, raw in enumerate(content.lstrip("\ufeff").splitlines(), 1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("export "):
            text = text[7:].lstrip()
        if "=" not in text:
            # Ignore unrelated text, but do not interpolate/execute it.
            continue
        name, value = text.split("=", 1)
        name = name.strip()
        if not KEY.fullmatch(name):
            # Failure never reveals supplied value.
            raise ValueError(f"Invalid dotenv variable name on line {line_number}")
        try:
            found[name] = _raw_value(value)
        except ValueError as exc:
            raise ValueError(f"Invalid dotenv value on line {line_number}: {exc}") from None
    return found


def _first(env: dict[str, str], *names: str) -> str:
    return next((env[name] for name in names if env.get(name)), "")


def _normalize_engine(value: str) -> str:
    name = value.strip().lower().split("+", 1)[0].replace("-", "_")
    if name in ("sql_server", "microsoft_sql_server"):
        name = "sqlserver"
    return SUPPORTED_SCHEMES.get(name, name)


def _parse_url(raw: str) -> tuple[dict[str, str], str]:
    try:
        parsed = urlsplit(raw)
        engine = _normalize_engine(parsed.scheme)
        if engine not in SUPPORTED_SCHEMES.values():
            raise ValueError("Unsupported database URL scheme")
        if engine == "sqlite":
            # SQLite URI paths are local files and still subject to project confinement.
            if parsed.netloc not in ("", "localhost"):
                raise ValueError("SQLite URLs must refer to a local file")
            path = unquote(parsed.path)
            if not path:
                raise ValueError("SQLite URL has no file path")
            return {"engine": engine, "path": path}, "database URL"
        # Attribute access validates host/port without returning passwords on error.
        port = parsed.port
        query = parse_qs(parsed.query)
        options = {"engine": engine, "host": parsed.hostname or "localhost",
                   "port": str(port) if port else "",
                   "user": unquote(parsed.username or ""), "password": unquote(parsed.password or ""),
                   "database": unquote(parsed.path.lstrip("/"))}
        if engine == "oracle":
            options["service"] = _first({k: v[0] for k, v in query.items() if v}, "service_name", "service", "sid") or options["database"]
        return options, "database URL"
    except (ValueError, TypeError) as exc:
        # Do not include the URI itself in errors (may embed credentials).
        raise ValueError(f"Cannot parse database URL: {type(exc).__name__}") from None


def resolve_env(content: str) -> EnvResolution:
    data = parse_dotenv(content)
    if not data:
        raise ValueError("No dotenv variables found")
    recognized: set[str] = set()
    warnings: list[str] = []
    url_pairs = [(key, data[key]) for key in URL_VARIABLES if data.get(key)]
    if url_pairs:
        if len(url_pairs) > 1 and len({v for _, v in url_pairs}) > 1:
            raise ValueError("Multiple different database URLs supplied; choose one connection")
        key, raw = url_pairs[0]
        recognized.add(key)
        fields, source = _parse_url(raw)
    else:
        explicit = _first(data, *ENGINE_VARIABLES)
        recognized.update(k for k in ENGINE_VARIABLES if data.get(k))
        engine = _normalize_engine(explicit) if explicit else ""
        if not engine:
            candidates = set()
            if any(k.startswith("POSTGRES_") or k.startswith("PG") for k in data):
                candidates.add("postgresql")
            if any(k.startswith("MYSQL_") for k in data):
                candidates.add("mysql")
            if any(k.startswith("MARIADB_") for k in data):
                candidates.add("mariadb")
            if any(k.startswith("MSSQL_") or k.startswith("SQLSERVER_") for k in data):
                candidates.add("sqlserver")
            if any(k.startswith("ORACLE_") for k in data):
                candidates.add("oracle")
            generic_port = _first(data, "DB_PORT", "DATABASE_PORT")
            if generic_port.isdecimal() and int(generic_port) in PORT_ENGINE:
                candidates.add(PORT_ENGINE[int(generic_port)])
            # MySQL and MariaDB share a connection protocol and driver.
            if candidates in ({"mysql", "mariadb"}, {"mysql"}, {"mariadb"}):
                engine = "mariadb" if "mariadb" in candidates else "mysql"
            elif len(candidates) == 1:
                engine = next(iter(candidates))
            elif not candidates and any(k in data for k in ("DB_HOST", "DB_DATABASE", "DB_USERNAME", "DB_PASSWORD")):
                engine = "mysql"
                warnings.append("Generic DB_* settings assumed MySQL-compatible protocol; specify DB_CONNECTION to override")
            else:
                raise ValueError("Cannot unambiguously identify SQL engine; add DB_CONNECTION or DATABASE_URL")
        if engine not in SUPPORTED_SCHEMES.values():
            raise ValueError("Unsupported database engine; choose one of the supported relational engines")
        if engine == "sqlite":
            fields = {"engine": engine, "path": _first(data, "DB_PATH", "DATABASE_PATH", "SQLITE_PATH", "DB_DATABASE", "DATABASE_NAME")}
            recognized.update(k for k in ("DB_PATH", "DATABASE_PATH", "SQLITE_PATH", "DB_DATABASE", "DATABASE_NAME") if k in data)
        else:
            prefixes = {"mysql": ("MYSQL",), "mariadb": ("MARIADB", "MYSQL"),
                        "postgresql": ("POSTGRES", "PG"), "sqlserver": ("MSSQL", "SQLSERVER"),
                        "oracle": ("ORACLE",)}[engine]
            def names(*suffixes: str) -> tuple[str, ...]:
                return tuple(f"{prefix}_{suffix}" for prefix in prefixes for suffix in suffixes)
            mapping = {
                "host": ("DB_HOST", "DATABASE_HOST") + names("HOST"),
                "port": ("DB_PORT", "DATABASE_PORT") + names("PORT"),
                "user": ("DB_USERNAME", "DB_USER", "DATABASE_USER", "DATABASE_USERNAME") + names("USER", "USERNAME"),
                "password": ("DB_PASSWORD", "DB_PASS", "DATABASE_PASSWORD") + names("PASSWORD", "PASS"),
                "database": ("DB_DATABASE", "DB_NAME", "DATABASE_NAME") + names("DATABASE", "DB", "NAME"),
                "service": ("DB_SERVICE", "DB_SERVICE_NAME", "ORACLE_SERVICE", "ORACLE_SERVICE_NAME"),
            }
            fields = {"engine": engine}
            for field_name, possibilities in mapping.items():
                fields[field_name] = _first(data, *possibilities)
                recognized.update(k for k in possibilities if k in data)
    for name, value in fields.items():
        if isinstance(value, str) and ("${" in value or "$(" in value or value.startswith("$")):
            raise ValueError(f"Unresolved environment variable reference in {name}; supply its resolved value")
    # Standard Docker database images often use a root-only password rather
    # than MYSQL_USER/MYSQL_PASSWORD. Use it only when no other user is set.
    if not url_pairs and fields.get("engine"):
        if fields["engine"] in ("mysql", "mariadb") and not fields.get("user"):
            root_password = _first(data, "MARIADB_ROOT_PASSWORD", "MYSQL_ROOT_PASSWORD")
            if root_password and not fields.get("password"):
                fields["user"] = "root"
                fields["password"] = root_password
                recognized.update(k for k in ("MYSQL_ROOT_PASSWORD", "MARIADB_ROOT_PASSWORD") if k in data)
                warnings.append("Root credentials detected; prefer a dedicated read-only database account")
    port_str = fields.get("port", "")
    try:
        port = int(port_str) if port_str else None
    except (ValueError, TypeError):
        raise ValueError("Database port must be an integer") from None
    spec = ConnectionSpec(engine=fields["engine"], host=fields.get("host", "localhost"), port=port,
                          user=fields.get("user", ""), password=fields.get("password", ""),
                          database=fields.get("database", ""), service=fields.get("service", ""),
                          path=fields.get("path", ""))
    if spec.engine != "sqlite" and spec.host.lower() in ("db", "database", "mysql", "mariadb", "postgres", "postgresql", "sqlserver", "mssql", "oracle"):
        warnings.append("Host appears to be a Docker/Compose service name. If OpenCode runs outside that container network, use localhost/127.0.0.1 and the published host port; container ports are not necessarily host ports")
    if spec.engine == "sqlite":
        warnings.append("SQLite files must exist inside this repository (for example .local/database.sqlite)")
    return EnvResolution(spec, source if url_pairs else "dotenv variables", sorted(recognized), warnings)


def load_local_env(filename: str, *, root: Path | None = None) -> str:
    project = (root or PROJECT_ROOT).resolve()
    allowed = (project / ".local").resolve()
    try:
        allowed.relative_to(project)
    except ValueError:
        raise ValueError("The .local/ directory must remain inside the repository") from None
    raw = Path(filename)
    if raw.is_absolute() or ".." in raw.parts:
        raise ValueError("Local dotenv path must be relative to .local/ inside this project")
    # Allow both .local/db.env and db.env.
    normalized = raw if raw.parts and raw.parts[0] == ".local" else Path(".local") / raw
    try:
        candidate = (project / normalized).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("Local dotenv file is missing or cannot be resolved") from None
    try:
        candidate.relative_to(allowed)
    except ValueError:
        raise ValueError("Dotenv file must be inside .local/; external paths and symlinks are denied") from None
    if not candidate.is_file():
        raise ValueError("Dotenv path is not a regular file")
    if not (candidate.name.startswith(".env") or candidate.suffix.lower() in (".env", ".dotenv")):
        raise ValueError("Expected a .env or *.env file inside .local/")
    if candidate.stat().st_size > MAX_ENV_BYTES:
        raise ValueError("Dotenv file exceeds 128 KiB")
    return candidate.read_text(encoding="utf-8-sig")
