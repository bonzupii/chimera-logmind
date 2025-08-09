#!/usr/bin/env python3
import os
import json
import datetime as dt
import socket
import signal
import sys
import threading

from typing import Optional

try:
    from .db import get_connection, initialize_schema  # type: ignore
    from .ingest import ingest_journal_into_duckdb  # type: ignore
    from .config import ChimeraConfig  # type: ignore
    from .ingest_framework import IngestionFramework  # type: ignore
except Exception:
    # Fallback to relative imports when executed directly
    from db import get_connection, initialize_schema  # type: ignore
    from ingest import ingest_journal_into_duckdb  # type: ignore
    from config import ChimeraConfig  # type: ignore
    from ingest_framework import IngestionFramework  # type: ignore

# Load configuration
config = ChimeraConfig.load()
DEFAULT_SOCKET_PATH = config.socket_path
DEFAULT_DB_PATH = config.db_path


def cleanup_socket(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def ensure_dir(path: str) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o750, exist_ok=True)


def set_permissions(path: str) -> None:
    os.chmod(path, 0o660)


APP_VERSION = "0.1.0"


def handle_client(conn: socket.socket, db_path: Optional[str]) -> None:
    try:
        data = conn.recv(4096)
        if not data:
            return
        text = data.decode(errors="ignore").strip()
        tokens = text.split()
        command = tokens[0].upper() if tokens else ""
        if command.startswith("PING"):
            conn.sendall(b"PONG\n")
        elif command.startswith("HEALTH"):
            conn.sendall(b"OK\n")
        elif command.startswith("VERSION"):
            conn.sendall((APP_VERSION + "\n").encode())
        elif command.startswith("INGEST_JOURNAL"):
            try:
                db_conn = get_connection(db_path)
                initialize_schema(db_conn)
            except Exception:
                conn.sendall(b"ERR db-not-initialized\n")
            else:
                # Optional args: seconds limit
                seconds = 3600
                limit = None
                if len(tokens) >= 2:
                    try:
                        seconds = int(tokens[1])
                    except Exception:
                        pass
                if len(tokens) >= 3:
                    try:
                        limit = int(tokens[2])
                    except Exception:
                        pass
                try:
                    inserted, total = ingest_journal_into_duckdb(db_conn, last_seconds=seconds, limit=limit)  # type: ignore
                    conn.sendall(f"OK inserted={inserted} total={total}\n".encode())
                except Exception as exc:
                    conn.sendall(f"ERR {exc}\n".encode())
                finally:
                    try:
                        db_conn.close()
                    except Exception:
                        pass
        elif command.startswith("QUERY_LOGS"):
            try:
                db_conn = get_connection(db_path)
                initialize_schema(db_conn)
            except Exception:
                conn.sendall(b"ERR db-not-initialized\n")
            else:
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

                now = dt.datetime.utcnow()
                since_seconds = int(args.get("since", 3600)) if str(args.get("since", "")).strip() != "" else 3600
                since_ts = now - dt.timedelta(seconds=since_seconds)
                limit = int(args.get("limit", 100)) if str(args.get("limit", "")).strip() != "" else 100
                limit = max(1, min(limit, 10000))
                order = str(args.get("order", "desc")).lower()
                if order not in ("asc", "desc"):
                    order = "desc"

                min_sev = args.get("min_severity")
                source = args.get("source")
                unit = args.get("unit")
                hostname = args.get("hostname")
                contains = args.get("contains")

                where_clauses = ["ts >= ?"]
                params = [since_ts]

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
                    conn.sendall(f"ERR {exc}\n".encode())
                finally:
                    try:
                        db_conn.close()
                    except Exception:
                        pass
        elif command.startswith("DISCOVER"):
            # Usage: DISCOVER UNITS|HOSTNAMES|SOURCES|SEVERITIES [since=SECONDS] [limit=N]
            try:
                db_conn = get_connection(db_path)
                initialize_schema(db_conn)
            except Exception:
                conn.sendall(b"ERR db-not-initialized\n")
            else:
                kind = None
                if len(tokens) >= 2:
                    kind = tokens[1].lower()
                args = {}
                for tok in tokens[2:]:
                    if "=" in tok:
                        k, v = tok.split("=", 1)
                        args[k.lower()] = v
                now = dt.datetime.utcnow()
                since_seconds = int(args.get("since", 86400)) if str(args.get("since", "")).strip() != "" else 86400
                since_ts = now - dt.timedelta(seconds=since_seconds)
                limit = int(args.get("limit", 100)) if str(args.get("limit", "")).strip() != "" else 100
                limit = max(1, min(limit, 10000))

                col = None
                if kind in ("units", "unit"):
                    col = "unit"
                elif kind in ("hostnames", "hostname"):
                    col = "hostname"
                elif kind in ("sources", "source"):
                    col = "source"
                elif kind in ("severities", "severity"):
                    col = "severity"
                if not col:
                    conn.sendall(b"ERR discover-kind-required\n")
                else:
                    try:
                        cur = db_conn.cursor()
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
                        conn.sendall(f"ERR {exc}\n".encode())
                    finally:
                        try:
                            db_conn.close()
                        except Exception:
                            pass
        elif command.startswith("CONFIG"):
            # Usage: CONFIG GET|SET|LIST|ADD_SOURCE|REMOVE_SOURCE|UPDATE_SOURCE
            try:
                if len(tokens) < 2:
                    conn.sendall(b"ERR config-subcommand-required\n")
                    return
                
                subcmd = tokens[1].lower()
                
                if subcmd == "get":
                    # CONFIG GET
                    conn.sendall((json.dumps(config.to_dict()) + "\n").encode())
                
                elif subcmd == "list":
                    # CONFIG LIST
                    sources = []
                    for source in config.log_sources:
                        sources.append({
                            "name": source.name,
                            "type": source.type,
                            "enabled": source.enabled,
                            "config": source.config
                        })
                    conn.sendall((json.dumps({"sources": sources}) + "\n").encode())
                
                elif subcmd == "add_source":
                    # CONFIG ADD_SOURCE name=NAME type=TYPE enabled=BOOL config=JSON
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
                
                elif subcmd == "remove_source":
                    # CONFIG REMOVE_SOURCE name=NAME
                    if len(tokens) < 3:
                        conn.sendall(b"ERR source-name-required\n")
                        return
                    
                    name = tokens[2].split("=", 1)[1] if "=" in tokens[2] else tokens[2]
                    if config.remove_source(name):
                        config.save()
                        conn.sendall(b"OK source-removed\n")
                    else:
                        conn.sendall(b"ERR source-not-found\n")
                
                elif subcmd == "update_source":
                    # CONFIG UPDATE_SOURCE name=NAME enabled=BOOL config=JSON
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
                
                else:
                    conn.sendall(b"ERR unknown-config-subcommand\n")
                    
            except Exception as exc:
                conn.sendall(f"ERR {exc}\n".encode())
        
        elif command.startswith("INGEST_ALL"):
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
        else:
            conn.sendall(b"ERR unknown command\n")
    finally:
        conn.close()


def main() -> None:
    ensure_dir(DEFAULT_SOCKET_PATH)
    cleanup_socket(DEFAULT_SOCKET_PATH)

    old_umask = os.umask(0o117)
    try:
        # Attempt database initialization once at startup (best effort)
        try:
            _init_conn = get_connection(DEFAULT_DB_PATH)
            initialize_schema(_init_conn)
            try:
                _init_conn.close()
            except Exception:
                pass
        except Exception as exc:
            print(f"[chimera] warning: DB not initialized: {exc}", file=sys.stderr)

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

            signal.signal(signal.SIGINT, shutdown_handler)
            signal.signal(signal.SIGTERM, shutdown_handler)

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
