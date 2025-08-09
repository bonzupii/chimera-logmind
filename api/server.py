#!/usr/bin/env python3
import os
import json
import datetime as dt
import socket
import signal
import sys
import threading
import logging
import logging.handlers

from typing import Optional

# --- Logging Setup ---
LOG_FILE = os.environ.get("CHIMERA_LOG_FILE", "/var/log/chimera/api.log")
LOG_LEVEL = os.environ.get("CHIMERA_LOG_LEVEL", "DEBUG").upper()
LOG_LEVEL = getattr(logging, LOG_LEVEL, logging.DEBUG)

# Configure logger
logger = logging.getLogger("chimera")
logger.setLevel(LOG_LEVEL)

formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Console handler (for systemd journal)
console_handler = logging.StreamHandler(sys.stderr)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# File handler (best effort)
try:
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    file_handler.setLevel(LOG_LEVEL)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
except Exception as _log_exc:
    # Fall back to console-only logging
    logger.warning(f"File logging disabled: {_log_exc}")
# --- End Logging Setup ---

try:
    from .db import get_connection, initialize_schema, clear_table
    from .ingest import ingest_journal_into_duckdb
    from .config import ChimeraConfig
    from .ingest_framework import IngestionFramework
    from .embeddings import SemanticSearchEngine, AnomalyDetector, RAGChatEngine
    from .system_health import SystemHealthMonitor, SystemMetricsCollector
except Exception as e:
    logger.error(f"Failed to import modules: {e}")
    # Fallback to relative imports when executed directly
    from db import get_connection, initialize_schema
    from ingest import ingest_journal_into_duckdb
    from config import ChimeraConfig
    from ingest_framework import IngestionFramework
    from embeddings import SemanticSearchEngine, AnomalyDetector, RAGChatEngine
    from system_health import SystemHealthMonitor, SystemMetricsCollector
    logger.warning("Using fallback relative imports.")


# Load configuration
config = ChimeraConfig.load()
DEFAULT_SOCKET_PATH = os.environ.get("CHIMERA_API_SOCKET", config.socket_path)
DEFAULT_DB_PATH = os.environ.get("CHIMERA_DB_PATH", config.db_path)
logger.info(f"Configuration loaded. Socket: {DEFAULT_SOCKET_PATH}, DB: {DEFAULT_DB_PATH}")


def cleanup_socket(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def ensure_dir(path: str) -> None:
    directory = os.path.dirname(path)
    try:
        os.makedirs(directory, mode=0o750, exist_ok=True)
    except PermissionError:
        # Fallback to per-user runtime dir if system dir is not writable
        user_run = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/chimera_{os.getuid()}"
        fallback_dir = os.path.join(user_run, "chimera")
        os.makedirs(fallback_dir, mode=0o750, exist_ok=True)
        global DEFAULT_SOCKET_PATH
        DEFAULT_SOCKET_PATH = os.path.join(fallback_dir, os.path.basename(path))


def set_permissions(path: str) -> None:
    """Set secure permissions on socket file - only owner can read/write"""
    os.chmod(path, 0o660)


APP_VERSION = "0.1.0"


def validate_integer_param(value: str, param_name: str, min_val: int = 0, max_val: Optional[int] = None) -> int:
    """Validate and sanitize integer parameters"""
    try:
        int_val = int(value)
        if int_val < min_val:
            raise ValueError(f"{param_name} must be >= {min_val}")
        if max_val is not None and int_val > max_val:
            raise ValueError(f"{param_name} must be <= {max_val}")
        return int_val
    except ValueError as e:
        raise ValueError(f"Invalid {param_name}: {e}")


def validate_string_param(value: str, param_name: str, max_length: int = 1000, allowed_chars: Optional[str] = None) -> str:
    """Validate and sanitize string parameters"""
    if len(value) > max_length:
        raise ValueError(f"{param_name} exceeds maximum length of {max_length}")

    if allowed_chars:
        import re
        if not re.match(f"^[{re.escape(allowed_chars)}]*$", value):
            raise ValueError(f"{param_name} contains invalid characters")

    return value.strip()


def validate_path_param(path: str, param_name: str) -> str:
    """Validate file path parameters to prevent path traversal"""
    import os

    # Normalize the path
    normalized = os.path.normpath(path)

    # Prevent path traversal
    if ".." in normalized or normalized.startswith("/"):
        raise ValueError(f"Invalid {param_name}: path traversal not allowed")

    return normalized


def _handle_ping(conn: socket.socket) -> None:
    """Handle PING command"""
    conn.sendall(b"PONG\n")


def _handle_health(conn: socket.socket) -> None:
    """Handle HEALTH command"""
    conn.sendall(b"OK\n")


def _handle_version(conn: socket.socket) -> None:
    """Handle VERSION command"""
    conn.sendall((APP_VERSION + "\n").encode())


def _handle_ingest_journal(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle INGEST_JOURNAL command"""
    try:
        db_conn = get_connection(db_path)
        initialize_schema(db_conn)
    except Exception:
        conn.sendall(b"ERR db-not-initialized\n")
    else:
        # Optional args: seconds limit
        seconds = 3600
        limit = None
        try:
            if len(tokens) >= 2:
                seconds = validate_integer_param(tokens[1], "seconds", min_val=1, max_val=86400*30)
            if len(tokens) >= 3:
                limit = validate_integer_param(tokens[2], "limit", min_val=1, max_val=100000)
        except ValueError as e:
            conn.sendall(f"ERR {e}\n".encode())
            return
        try:
            inserted, total = ingest_journal_into_duckdb(db_conn, last_seconds=seconds, limit=limit)
            conn.sendall(f"OK inserted={inserted} total={total}\n".encode())
        except Exception as exc:
            conn.sendall(f"ERR {exc}\n".encode())
        finally:
            try:
                db_conn.close()
            except Exception:
                pass


def _parse_query_logs_params(tokens: list):
    """Parse and validate QUERY_LOGS parameters"""
    # Parse key=value pairs following the command
    args = {}
    for tok in tokens[1:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            try:
                from urllib.parse import unquote
                v = unquote(v)
            except Exception:
                pass
            args[k.lower()] = v

    # Validate parameters
    since_seconds = validate_integer_param(str(args.get("since", "3600")), "since", min_val=1, max_val=86400*365) if str(args.get("since", "")).strip() != "" else 3600
    limit = validate_integer_param(str(args.get("limit", "100")), "limit", min_val=1, max_val=10000) if str(args.get("limit", "")).strip() != "" else 100

    order = validate_string_param(str(args.get("order", "desc")).lower(), "order", max_length=10)
    if order not in ("asc", "desc"):
        order = "desc"

    min_sev = validate_string_param(args.get("min_severity", ""), "min_severity", max_length=20) if args.get("min_severity") else None
    source = validate_string_param(args.get("source", ""), "source", max_length=100) if args.get("source") else None
    unit = validate_string_param(args.get("unit", ""), "unit", max_length=100) if args.get("unit") else None
    hostname = validate_string_param(args.get("hostname", ""), "hostname", max_length=255) if args.get("hostname") else None
    contains = validate_string_param(args.get("contains", ""), "contains", max_length=500) if args.get("contains") else None

    return since_seconds, limit, order, min_sev, source, unit, hostname, contains


def _handle_query_logs(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle QUERY_LOGS command"""
    try:
        db_conn = get_connection(db_path)
        initialize_schema(db_conn)
    except Exception:
        conn.sendall(b"ERR db-not-initialized\n")
    else:
        now = dt.datetime.now(dt.timezone.utc)

        try:
            since_seconds, limit, order, min_sev, source, unit, hostname, contains = _parse_query_logs_params(tokens)
        except ValueError as e:
            conn.sendall(f"ERR {e}\n".encode())
            return

        since_ts = now - dt.timedelta(seconds=since_seconds)

        where_clauses = ["ts >= ?"]
        params: list = [since_ts]

        if min_sev:
            sev_map = {
                "emerg": 0,
                "alert": 1,
                "crit": 2,
                "err": 3,
                "warning": 4,
                "notice": 5,
                "info": 6,
                "debug": 7,
            }
            threshold = sev_map.get(min_sev.lower())
            if threshold is not None:
                where_clauses.append(
                    "(CASE severity WHEN 'emerg' THEN 0 WHEN 'alert' THEN 1 WHEN 'crit' THEN 2 WHEN 'err' THEN 3 WHEN 'warning' THEN 4 WHEN 'notice' THEN 5 WHEN 'info' THEN 6 WHEN 'debug' THEN 7 ELSE 99 END) <= ?"
                )
                params.append(threshold)

        if source:
            where_clauses.append("source = ?")
            params.append(source)
        if unit:
            where_clauses.append("unit = ?")
            params.append(unit)
        if hostname:
            where_clauses.append("hostname = ?")
            params.append(hostname)
        if contains:
            where_clauses.append("message ILIKE ?")
            params.append(f"%{contains}%")

        where_sql = " AND ".join(where_clauses)
        sql = (
            "SELECT ts, hostname, source, unit, severity, pid, message "
            "FROM logs WHERE "
            + where_sql
            + f" ORDER BY ts {order} LIMIT ?"
        )
        params.append(limit)

        try:
            cur = db_conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            # Stream JSONL back to client
            for r in rows:
                ts, host, src, u, sev, pid, msg = r
                item = {
                    "ts": ts.isoformat(sep=" "),
                    "hostname": host,
                    "source": src,
                    "unit": u,
                    "severity": sev,
                    "pid": pid,
                    "message": msg,
                }
                conn.sendall((json.dumps(item) + "\n").encode())
        except Exception as exc:
            logger.error(f"Database error in QUERY_LOGS command: {exc}")
            conn.sendall(b"ERR database-error\n")
        finally:
            try:
                db_conn.close()
            except Exception:
                pass


def _get_discover_column(kind: str) -> Optional[str]:
    """Get the database column for discover command"""
    if kind in ("units", "unit"):
        return "unit"
    elif kind in ("hostnames", "hostname"):
        return "hostname"
    elif kind in ("sources", "source"):
        return "source"
    elif kind in ("severities", "severity"):
        return "severity"

    return None


def _handle_discover(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle DISCOVER command"""
    # Usage: DISCOVER UNITS|HOSTNAMES|SOURCES|SEVERITIES [since=SECONDS] [limit=N]
    try:
        db_conn = get_connection(db_path)
        initialize_schema(db_conn)
    except Exception:
        conn.sendall(b"ERR db-not-initialized\n")
    else:
        kind = tokens[1].lower() if len(tokens) >= 2 else None
        args = {}
        for tok in tokens[2:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                args[k.lower()] = v
        now = dt.datetime.now(dt.timezone.utc)

        try:
            since_seconds = validate_integer_param(str(args.get("since", "86400")), "since", min_val=1, max_val=86400*365) if str(args.get("since", "")).strip() != "" else 86400
            limit = validate_integer_param(str(args.get("limit", "100")), "limit", min_val=1, max_val=10000) if str(args.get("limit", "")).strip() != "" else 100
        except ValueError as e:
            conn.sendall(f"ERR {e}\n".encode())
            return

        since_ts = now - dt.timedelta(seconds=since_seconds)
        col = _get_discover_column(kind) if kind else None

        if not col:
            conn.sendall(b"ERR discover-kind-required\n")
        else:
            try:
                cur = db_conn.cursor()
                # Use parameterized column name from whitelist
                sql = (
                    f"SELECT {col} AS value, COUNT(*) AS count FROM logs WHERE ts >= ? "
                    f"GROUP BY {col} ORDER BY count DESC NULLS LAST, value NULLS LAST LIMIT ?"
                )
                cur.execute(sql, [since_ts, limit])
                rows = cur.fetchall()
                for value, count in rows:
                    item = {"value": value, "count": count}
                    conn.sendall((json.dumps(item) + "\n").encode())
            except Exception as exc:
                logger.error(f"Database error in DISCOVER command: {exc}")
                conn.sendall(b"ERR database-error\n")
            finally:
                try:
                    db_conn.close()
                except Exception:
                    pass


def _handle_config_get(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle CONFIG GET subcommand"""
    conn.sendall((json.dumps(config.to_dict()) + "\n").encode())


def _handle_config_list(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle CONFIG LIST subcommand"""
    sources = []
    for source in config.log_sources:
        sources.append({
            "name": source.name,
            "type": source.type,
            "enabled": source.enabled,
            "config": source.config
        })
    conn.sendall((json.dumps({"sources": sources}) + "\n").encode())


def _handle_config_add_source(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle CONFIG ADD_SOURCE subcommand"""
    if len(tokens) < 3:
        conn.sendall(b"ERR source-params-required\n")
        return

    args = {}
    for tok in tokens[2:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            if k == "config":
                try:
                    args[k] = json.loads(v)
                except json.JSONDecodeError:
                    conn.sendall(b"ERR invalid-config-json\n")
                    return
            elif k == "enabled":
                args[k] = v.lower() == "true"
            else:
                args[k] = v

    if "name" not in args or "type" not in args:
        conn.sendall(b"ERR name-and-type-required\n")
        return

    from config import LogSource
    new_source = LogSource(**args)
    config.add_source(new_source)
    config.save()
    conn.sendall(b"OK source-added\n")


def _handle_config_remove_source(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle CONFIG REMOVE_SOURCE subcommand"""
    if len(tokens) < 3:
        conn.sendall(b"ERR source-name-required\n")
        return

    name = tokens[2].split("=", 1)[1] if "=" in tokens[2] else tokens[2]
    if config.remove_source(name):
        config.save()
        conn.sendall(b"OK source-removed\n")
    else:
        conn.sendall(b"ERR source-not-found\n")


def _handle_config_update_source(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle CONFIG UPDATE_SOURCE subcommand"""
    if len(tokens) < 3:
        conn.sendall(b"ERR source-name-required\n")
        return

    args = {}
    name = None
    for tok in tokens[2:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            if k == "name":
                name = v
            elif k == "config":
                try:
                    args[k] = json.loads(v)
                except json.JSONDecodeError:
                    conn.sendall(b"ERR invalid-config-json\n")
                    return
            elif k == "enabled":
                args[k] = v.lower() == "true"
            else:
                args[k] = v

    if not name:
        conn.sendall(b"ERR source-name-required\n")
        return

    if config.update_source(name, **args):
        config.save()
        conn.sendall(b"OK source-updated\n")
    else:
        conn.sendall(b"ERR source-not-found\n")


# Config subcommand dispatcher
CONFIG_HANDLERS = {
    "get": _handle_config_get,
    "list": _handle_config_list,
    "add_source": _handle_config_add_source,
    "remove_source": _handle_config_remove_source,
    "update_source": _handle_config_update_source,
}


def _handle_config(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle CONFIG command"""
    # Usage: CONFIG GET|SET|LIST|ADD_SOURCE|REMOVE_SOURCE|UPDATE_SOURCE
    try:
        if len(tokens) < 2:
            conn.sendall(b"ERR config-subcommand-required\n")
            return

        subcmd = tokens[1].lower()
        handler = CONFIG_HANDLERS.get(subcmd)

        if handler:
            handler(conn, db_path, tokens)
        else:
            conn.sendall(b"ERR unknown-config-subcommand\n")

    except Exception as exc:
        conn.sendall(f"ERR {exc}\n".encode())


def _handle_ingest_all(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle INGEST_ALL command"""
    # Ingest from all enabled sources
    try:
        framework = IngestionFramework(db_path)
        total_inserted = 0
        total_sources = 0

        for source in config.get_enabled_sources():
            try:
                inserted, _ = framework.ingest_source(source, last_seconds=3600, limit=1000)
                total_inserted += inserted
                total_sources += 1
            except Exception as exc:
                # Log error but continue with other sources
                print(f"Error ingesting {source.name}: {exc}", file=sys.stderr)

        conn.sendall(f"OK inserted={total_inserted} sources={total_sources}\n".encode())

    except Exception as exc:
        conn.sendall(f"ERR {exc}\n".encode())


def _handle_search(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle SEARCH command"""
    # Usage: SEARCH query="text" [n_results=N] [since=SECONDS] [source=SOURCE] [unit=UNIT] [severity=SEVERITY]
    try:
        if len(tokens) < 2:
            conn.sendall(b"ERR search-query-required\n")
            return

        # Parse arguments
        args = {}
        query = None
        for tok in tokens[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                if k == "query":
                    try:
                        from urllib.parse import unquote
                        query = unquote(v)
                    except Exception:
                        query = v
                else:
                    args[k.lower()] = v

        if not query:
            conn.sendall(b"ERR search-query-required\n")
            return

        try:
            query = validate_string_param(query, "query", max_length=2000)
            n_results = validate_integer_param(str(args.get("n_results", "10")), "n_results", min_val=1, max_val=100)
            since_seconds = validate_integer_param(str(args.get("since", "86400")), "since", min_val=1, max_val=86400*365) if args.get("since") else None
            source = validate_string_param(args.get("source", ""), "source", max_length=100) if args.get("source") else None
            unit = validate_string_param(args.get("unit", ""), "unit", max_length=100) if args.get("unit") else None
            severity = validate_string_param(args.get("severity", ""), "severity", max_length=20) if args.get("severity") else None
        except ValueError as e:
            conn.sendall(f"ERR {e}\n".encode())
            return

        search_engine = SemanticSearchEngine(db_path)
        results = search_engine.search_logs(
            query=query,
            n_results=n_results,
            since_seconds=since_seconds,
            source=source,
            unit=unit,
            severity=severity
        )

        # Stream results as JSONL
        for result in results:
            conn.sendall((json.dumps(result) + "\n").encode())

    except Exception as exc:
        conn.sendall(f"ERR {exc}\n".encode())


def _handle_index(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle INDEX command"""
    # Usage: INDEX [since=SECONDS] [limit=N]
    try:
        args = {}
        for tok in tokens[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                args[k.lower()] = v

        try:
            since_seconds = validate_integer_param(str(args.get("since", "86400")), "since", min_val=1, max_val=86400*365)
        except ValueError as e:
            conn.sendall(f"ERR {e}\n".encode())
            return

        search_engine = SemanticSearchEngine(db_path)
        indexed, total = search_engine.index_logs(since_seconds=since_seconds)

        conn.sendall(f"OK indexed={indexed} total={total}\n".encode())

    except Exception as exc:
        conn.sendall(f"ERR {exc}\n".encode())


def _handle_anomalies(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle ANOMALIES command"""
    # Usage: ANOMALIES [since=SECONDS]
    try:
        args = {}
        for tok in tokens[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                args[k.lower()] = v

        try:
            since_seconds = validate_integer_param(str(args.get("since", "3600")), "since", min_val=1, max_val=86400*30)
        except ValueError as e:
            conn.sendall(f"ERR {e}\n".encode())
            return

        detector = AnomalyDetector(db_path)
        anomalies = detector.detect_anomalies(since_seconds=since_seconds)

        # Stream anomalies as JSONL
        for anomaly in anomalies:
            conn.sendall((json.dumps(anomaly) + "\n").encode())

    except Exception as exc:
        conn.sendall(f"ERR {exc}\n".encode())


def _handle_metrics(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle METRICS command"""
    # Usage: METRICS [type=TYPE] [since=SECONDS] [limit=N]
    try:
        args = {}
        for tok in tokens[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                args[k.lower()] = v

        try:
            metric_type = validate_string_param(args.get("type", ""), "type", max_length=50) if args.get("type") else None
            since_seconds = validate_integer_param(str(args.get("since", "3600")), "since", min_val=1, max_val=86400*30)
            limit = validate_integer_param(str(args.get("limit", "1000")), "limit", min_val=1, max_val=100000)
        except ValueError as e:
            conn.sendall(f"ERR {e}\n".encode())
            return

        monitor = SystemHealthMonitor(db_path)
        metrics = monitor.get_metrics(
            metric_type=metric_type,
            since_seconds=since_seconds,
            limit=limit
        )

        # Stream metrics as JSONL
        for metric in metrics:
            conn.sendall((json.dumps(metric) + "\n").encode())

    except Exception as exc:
        conn.sendall(f"ERR {exc}\n".encode())


def _handle_collect_metrics(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle COLLECT_METRICS command"""
    # Usage: COLLECT_METRICS
    try:
        from .system_health import SystemMetricsCollector
        collector = SystemMetricsCollector(db_path)
        metrics = collector.collect_all_metrics()
        stored = collector.store_metrics(metrics)
        conn.sendall(f"OK collected={stored}\n".encode())

    except Exception as exc:
        conn.sendall(f"ERR {exc}\n".encode())


def _handle_alerts(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle ALERTS command"""
    # Usage: ALERTS [since=SECONDS] [severity=SEVERITY] [acknowledged=BOOL]
    try:
        args = {}
        for tok in tokens[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                if k == "acknowledged":
                    args[k] = v.lower() == "true"
                else:
                    args[k] = v

        try:
            since_seconds = validate_integer_param(str(args.get("since", "86400")), "since", min_val=1, max_val=86400*365)
            severity = validate_string_param(args.get("severity", ""), "severity", max_length=20) if args.get("severity") else None
            acknowledged = args.get("acknowledged")  # Boolean validation handled separately
        except ValueError as e:
            conn.sendall(f"ERR {e}\n".encode())
            return

        monitor = SystemHealthMonitor(db_path)
        alerts = monitor.get_alerts(
            since_seconds=since_seconds,
            severity=severity,
            acknowledged=acknowledged
        )

        # Stream alerts as JSONL
        for alert in alerts:
            conn.sendall((json.dumps(alert) + "\n").encode())

    except Exception as exc:
        conn.sendall(f"ERR {exc}\n".encode())

def _handle_chat(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle CHAT command"""
    # Usage: CHAT [query=QUERY] [context_size=N] [since=SECONDS] or CHAT message=MESSAGE
    try:
        args = {}
        for tok in tokens[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                args[k] = v

        # Check if this is a simple message chat or RAG query
        if "message" in args:
            # Simple message chat (legacy)
            message = args["message"]
            # RAGChatEngine already imported at module level
            chat_engine = RAGChatEngine(db_path)
            response = chat_engine.chat(message, context_size=5, since_seconds=3600)
            conn.sendall((json.dumps({"response": response}) + "\n").encode())
        else:
            # RAG query mode
            try:
                query = validate_string_param(args.get("query", ""), "query", max_length=2000) if args.get("query") else None
                context_size = validate_integer_param(str(args.get("context_size", "10")), "context_size", min_val=1, max_val=50)
                since_seconds = validate_integer_param(str(args.get("since", "3600")), "since", min_val=1, max_val=86400*30)
            except ValueError as e:
                conn.sendall(f"ERR {e}\n".encode())
                return

            # RAGChatEngine already imported at module level
            chat_engine = RAGChatEngine(db_path)

            if query:
                # Single query mode
                response = chat_engine.chat(query, context_size=context_size, since_seconds=since_seconds)
                conn.sendall((json.dumps({"response": response}) + "\n").encode())
            else:
                # Interactive mode - send session info
                session_info = chat_engine.start_session(context_size=context_size, since_seconds=since_seconds)
                conn.sendall((json.dumps({"session": session_info}) + "\n").encode())

    except Exception as exc:
        conn.sendall(f"ERR {exc}\n".encode())


def _handle_chat_history(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle CHAT_HISTORY command"""
    # Usage: CHAT_HISTORY
    try:
        # RAGChatEngine already imported at module level
        chat_engine = RAGChatEngine(db_path)
        history = chat_engine.get_chat_history()
        conn.sendall((json.dumps(history) + "\n").encode())
    except Exception as exc:
        conn.sendall(f"ERR {exc}\n".encode())


def _handle_chat_clear(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle CHAT_CLEAR command"""
    # Usage: CHAT_CLEAR
    try:
        # RAGChatEngine already imported at module level
        chat_engine = RAGChatEngine(db_path)
        chat_engine.clear_chat_history()
        conn.sendall(b"OK history-cleared\n")
    except Exception as exc:
        conn.sendall(f"ERR {exc}\n".encode())


def _handle_chat_stats(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle CHAT_STATS command"""
    # Usage: CHAT_STATS
    try:
        # RAGChatEngine already imported at module level
        chat_engine = RAGChatEngine(db_path)
        stats = chat_engine.get_chat_stats()
        conn.sendall((json.dumps(stats) + "\n").encode())
    except Exception as exc:
        conn.sendall(f"ERR {exc}\n".encode())

def _handle_report_generate(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle REPORT GENERATE subcommand"""
    # Usage: REPORT GENERATE [since=SECONDS] [format=FORMAT] [output=PATH]
    args = {}
    for tok in tokens[2:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            args[k] = v

    try:
        since_seconds = validate_integer_param(str(args.get("since", "86400")), "since", min_val=1, max_val=86400*365)
        format_type = validate_string_param(args.get("format", "text"), "format", max_length=10)
        if format_type not in ["text", "html", "json"]:
            raise ValueError("format must be text, html, or json")
        output_val = args.get("output")
        output_path = validate_path_param(output_val, "output_path") if output_val else None
    except ValueError as e:
        conn.sendall(f"ERR {e}\n".encode())
        return

    try:
        from .reporting import ReportGenerator, ReportDelivery
    except ImportError:
        from reporting import ReportGenerator, ReportDelivery
    generator = ReportGenerator(db_path)
    delivery = ReportDelivery()

    # Generate report
    report = generator.generate_daily_report(since_seconds)

    if format_type == "json":
        result = json.dumps(report, indent=2)
    elif format_type == "html":
        result = generator.format_report_as_html(report)
    else:  # text
        result = generator.format_report_as_text(report)

    # Save to file if requested
    if output_path:
        if format_type == "html":
            delivery.save_report_to_file("", result, output_path)
        else:
            delivery.save_report_to_file(result, "", output_path)
        conn.sendall(f"OK saved to {output_path}\n".encode())
    else:
        conn.sendall(result.encode())


def _handle_report_send(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle REPORT SEND subcommand"""
    # Usage: REPORT SEND [to=EMAIL] [since=SECONDS] [subject=SUBJECT]
    args = {}
    for tok in tokens[2:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            args[k] = v

    try:
        to_email = validate_string_param(args.get("to", ""), "to_email", max_length=254) if args.get("to") else None
        since_seconds = validate_integer_param(str(args.get("since", "86400")), "since", min_val=1, max_val=86400*365)
        subject = validate_string_param(args.get("subject", "Chimera LogMind Daily Report"), "subject", max_length=200)
    except ValueError as e:
        conn.sendall(f"ERR {e}\n".encode())
        return

    if not to_email:
        conn.sendall(b"ERR missing recipient email\n")
        return

    try:
        from .reporting import ReportGenerator, ReportDelivery
    except ImportError:
        from reporting import ReportGenerator, ReportDelivery
    generator = ReportGenerator(db_path)
    delivery = ReportDelivery()

    # Generate report
    report = generator.generate_daily_report(since_seconds)
    report_text = generator.format_report_as_text(report)
    report_html = generator.format_report_as_html(report)

    # Send email
    success = delivery.send_report_email(report_text, report_html, to_email, subject)
    if success:
        conn.sendall(f"OK report sent to {to_email}\n".encode())
    else:
        conn.sendall(b"ERR failed to send email\n")


def _handle_report_list(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle REPORT LIST subcommand"""
    # Usage: REPORT LIST [limit=N]
    args = {}
    for tok in tokens[2:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            args[k] = v

    try:
        limit = validate_integer_param(str(args.get("limit", "10")), "limit", min_val=1, max_val=1000)
    except ValueError as e:
        conn.sendall(f"ERR {e}\n".encode())
        return

    # List saved reports
    from pathlib import Path

    reports_dir = Path("/var/lib/chimera/reports")
    if reports_dir.exists():
        report_files = []
        for file in reports_dir.glob("report_*.txt"):
            report_files.append({
                "filename": file.name,
                "size": file.stat().st_size,
                "modified": dt.datetime.fromtimestamp(file.stat().st_mtime).isoformat()
            })

        # Sort by modification time (newest first)
        report_files.sort(key=lambda x: x["modified"], reverse=True)
        report_files = report_files[:limit]

        for report_file in report_files:
            conn.sendall((json.dumps(report_file) + "\n").encode())
    else:
        conn.sendall(b"ERR reports directory not found\n")


# Report action dispatcher
REPORT_HANDLERS = {
    "GENERATE": _handle_report_generate,
    "SEND": _handle_report_send,
    "LIST": _handle_report_list,
}


def _handle_report(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle REPORT command"""
    # Usage: REPORT GENERATE|SEND|LIST [args...]
    try:
        if len(tokens) < 2:
            conn.sendall(b"ERR missing report action\n")
            return

        report_action = tokens[1].upper()
        handler = REPORT_HANDLERS.get(report_action)

        if handler:
            handler(conn, db_path, tokens)
        else:
            conn.sendall(b"ERR unknown report action\n")
    except Exception as exc:
        conn.sendall(f"ERR {exc}\n".encode())


def _handle_audit_full(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle AUDIT FULL subcommand"""
    try:
        from .security_audit import SecurityAuditor
    except ImportError:
        from security_audit import SecurityAuditor
    auditor = SecurityAuditor(db_path)

    # Run full security audit
    results = auditor.run_full_audit()
    conn.sendall((json.dumps(results, indent=2) + "\n").encode())


def _handle_audit_tool(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle AUDIT TOOL subcommand"""
    # Usage: AUDIT TOOL [tool=TOOL_NAME]
    args = {}
    for tok in tokens[2:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            args[k] = v

    tool = args.get("tool")
    if not tool:
        conn.sendall(b"ERR missing tool name\n")
        return

    try:
        from .security_audit import SecurityAuditor
    except ImportError:
        from security_audit import SecurityAuditor
    auditor = SecurityAuditor(db_path)

    # Run specific tool
    tool_functions = {
        "auditd": auditor.run_auditd_check,
        "aide": auditor.run_aide_check,
        "rkhunter": auditor.run_rkhunter_check,
        "chkrootkit": auditor.run_chkrootkit_check,
        "clamav": auditor.run_clamav_check,
        "openscap": auditor.run_openscap_check,
        "lynis": auditor.run_lynis_check
    }

    if tool in tool_functions:
        result = tool_functions[tool]()
        conn.sendall((json.dumps(result, indent=2) + "\n").encode())
    else:
        conn.sendall(f"ERR unknown tool: {tool}\n".encode())


def _handle_audit_history(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle AUDIT HISTORY subcommand"""
    # Usage: AUDIT HISTORY [tool=TOOL_NAME] [limit=N]
    args = {}
    for tok in tokens[2:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            args[k] = v

    try:
        tool = validate_string_param(args.get("tool", ""), "tool", max_length=50) if args.get("tool") else None
        limit = validate_integer_param(str(args.get("limit", "50")), "limit", min_val=1, max_val=1000)
    except ValueError as e:
        conn.sendall(f"ERR {e}\n".encode())
        return

    try:
        from .security_audit import SecurityAuditor
    except ImportError:
        from security_audit import SecurityAuditor
    auditor = SecurityAuditor(db_path)

    history = auditor.get_audit_history(tool, limit)
    for entry in history:
        conn.sendall((json.dumps(entry) + "\n").encode())


def _handle_audit_details(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle AUDIT DETAILS subcommand"""
    # Usage: AUDIT DETAILS [id=ID]
    args = {}
    for tok in tokens[2:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            args[k] = v

    audit_id_str = args.get("id")
    if not audit_id_str:
        conn.sendall(b"ERR missing audit ID\n")
        return

    try:
        audit_id = int(audit_id_str)
    except (ValueError, TypeError):
        conn.sendall(b"ERR invalid audit ID\n")
        return

    try:
        from .security_audit import SecurityAuditor
    except ImportError:
        from security_audit import SecurityAuditor
    auditor = SecurityAuditor(db_path)

    details = auditor.get_audit_details(audit_id)
    if details:
        conn.sendall((json.dumps(details, indent=2) + "\n").encode())
    else:
        conn.sendall(b"ERR audit not found\n")


# Audit action dispatcher
AUDIT_HANDLERS = {
    "FULL": _handle_audit_full,
    "TOOL": _handle_audit_tool,
    "HISTORY": _handle_audit_history,
    "DETAILS": _handle_audit_details,
}


def _handle_audit(conn: socket.socket, db_path: Optional[str], tokens: list) -> None:
    """Handle AUDIT command"""
    # Usage: AUDIT FULL|TOOL|HISTORY|DETAILS [args...]
    try:
        if len(tokens) < 2:
            conn.sendall(b"ERR missing audit action\n")
            return

        audit_action = tokens[1].upper()
        handler = AUDIT_HANDLERS.get(audit_action)

        if handler:
            handler(conn, db_path, tokens)
        else:
            conn.sendall(b"ERR unknown audit action\n")
    except Exception as exc:
        conn.sendall(f"ERR {exc}\n".encode())


# Command dispatcher mapping
COMMAND_HANDLERS = {
    "PING": _handle_ping,
    "HEALTH": _handle_health,
    "VERSION": _handle_version,
    "INGEST_JOURNAL": _handle_ingest_journal,
    "QUERY_LOGS": _handle_query_logs,
    "DISCOVER": _handle_discover,
    "CONFIG": _handle_config,
    "INGEST_ALL": _handle_ingest_all,
    "SEARCH": _handle_search,
    "INDEX": _handle_index,
    "ANOMALIES": _handle_anomalies,
    "METRICS": _handle_metrics,
    "COLLECT_METRICS": _handle_collect_metrics,
    "ALERTS": _handle_alerts,
    "CHAT": _handle_chat,
    "CHAT_HISTORY": _handle_chat_history,
    "CHAT_CLEAR": _handle_chat_clear,
    "CHAT_STATS": _handle_chat_stats,
    "REPORT": _handle_report,
    "AUDIT": _handle_audit,
}


def handle_client(conn: socket.socket, db_path: Optional[str]) -> None:
    """Handle client connection with command dispatcher pattern"""
    try:
        data = conn.recv(4096)
        if not data:
            return
        text = data.decode(errors="ignore").strip()
        logger.debug(f"Received command: {text}")
        tokens = text.split()
        command = tokens[0].upper() if tokens else ""

        # Find matching handler
        handler = None
        for cmd_prefix, cmd_handler in COMMAND_HANDLERS.items():
            if command.startswith(cmd_prefix):
                handler = cmd_handler
                break

        if handler:
            handler(conn, db_path, tokens)
        else:
            conn.sendall(b"ERR unknown command\n")
    finally:
        conn.close()


def main() -> None:
    """Main server function"""
    # Load configuration
    _cfg = ChimeraConfig.load()
    global DEFAULT_SOCKET_PATH, DEFAULT_DB_PATH
    DEFAULT_SOCKET_PATH = os.environ.get("CHIMERA_API_SOCKET", _cfg.socket_path)
    DEFAULT_DB_PATH = os.environ.get("CHIMERA_DB_PATH", _cfg.db_path)
    logger.info(f"Runtime configuration. Socket: {DEFAULT_SOCKET_PATH}, DB: {DEFAULT_DB_PATH}")

    # Quick DB check
    try:
        _init_conn = get_connection(DEFAULT_DB_PATH)
        initialize_schema(_init_conn)
        clear_table(_init_conn, 'ingest_state')
        try:
            _init_conn.close()
        except Exception:
            pass
    except Exception as exc:
        print(f"[chimera] warning: DB not initialized: {exc}", file=sys.stderr)

    ensure_dir(DEFAULT_SOCKET_PATH)
    cleanup_socket(DEFAULT_SOCKET_PATH)
    # Create socket with restricted permissions
    old_umask = os.umask(0o117)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(DEFAULT_SOCKET_PATH)
            set_permissions(DEFAULT_SOCKET_PATH)
            server.listen(5)

            def shutdown_handler(signum, frame):
                try:
                    server.close()
                finally:
                    cleanup_socket(DEFAULT_SOCKET_PATH)
                    sys.exit(0)

            # Only install signal handlers in the main thread
            try:
                if threading.current_thread() is threading.main_thread():
                    signal.signal(signal.SIGINT, shutdown_handler)
                    signal.signal(signal.SIGTERM, shutdown_handler)
            except Exception as _sig_exc:
                logger.warning(f"Skipping signal handlers: {_sig_exc}")

            while True:
                conn, _ = server.accept()
                t = threading.Thread(target=handle_client, args=(conn, DEFAULT_DB_PATH), daemon=True)
                t.start()
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup_socket(DEFAULT_SOCKET_PATH)
