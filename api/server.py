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
    from .db import get_connection, initialize_schema
    from .ingest import ingest_journal_into_duckdb
    from .config import ChimeraConfig
    from .ingest_framework import IngestionFramework
    from .embeddings import SemanticSearchEngine, AnomalyDetector, RAGChatEngine
    from .system_health import SystemHealthMonitor
except Exception:
    # Fallback to relative imports when executed directly
    from db import get_connection, initialize_schema
    from ingest import ingest_journal_into_duckdb
    from config import ChimeraConfig
    from ingest_framework import IngestionFramework
    from embeddings import SemanticSearchEngine, AnomalyDetector, RAGChatEngine
    from system_health import SystemHealthMonitor

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
                    inserted, total = ingest_journal_into_duckdb(db_conn, last_seconds=seconds, limit=limit)
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
        
        elif command.startswith("SEARCH"):
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
                
                n_results = int(args.get("n_results", 10))
                since_seconds = int(args.get("since", 86400)) if args.get("since") else None
                source = args.get("source")
                unit = args.get("unit")
                severity = args.get("severity")
                
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
        
        elif command.startswith("INDEX"):
            # Usage: INDEX [since=SECONDS] [limit=N]
            try:
                args = {}
                for tok in tokens[1:]:
                    if "=" in tok:
                        k, v = tok.split("=", 1)
                        args[k.lower()] = v
                
                since_seconds = int(args.get("since", 86400))
                limit = int(args.get("limit", 1000)) if args.get("limit") else None
                
                search_engine = SemanticSearchEngine(db_path)
                indexed, total = search_engine.index_logs(since_seconds=since_seconds)
                
                conn.sendall(f"OK indexed={indexed} total={total}\n".encode())
                
            except Exception as exc:
                conn.sendall(f"ERR {exc}\n".encode())
        
        elif command.startswith("ANOMALIES"):
            # Usage: ANOMALIES [since=SECONDS]
            try:
                args = {}
                for tok in tokens[1:]:
                    if "=" in tok:
                        k, v = tok.split("=", 1)
                        args[k.lower()] = v
                
                since_seconds = int(args.get("since", 3600))
                
                detector = AnomalyDetector(db_path)
                anomalies = detector.detect_anomalies(since_seconds=since_seconds)
                
                # Stream anomalies as JSONL
                for anomaly in anomalies:
                    conn.sendall((json.dumps(anomaly) + "\n").encode())
                    
            except Exception as exc:
                conn.sendall(f"ERR {exc}\n".encode())
        
        elif command.startswith("METRICS"):
            # Usage: METRICS [type=TYPE] [since=SECONDS] [limit=N]
            try:
                args = {}
                for tok in tokens[1:]:
                    if "=" in tok:
                        k, v = tok.split("=", 1)
                        args[k.lower()] = v
                
                metric_type = args.get("type")
                since_seconds = int(args.get("since", 3600))
                limit = int(args.get("limit", 1000))
                
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
        
        elif command.startswith("COLLECT_METRICS"):
            # Usage: COLLECT_METRICS
            try:
                from system_health import SystemMetricsCollector
                collector = SystemMetricsCollector(db_path)
                metrics = collector.collect_all_metrics()
                stored = collector.store_metrics(metrics)
                conn.sendall(f"OK collected={stored}\n".encode())
                
            except Exception as exc:
                conn.sendall(f"ERR {exc}\n".encode())
        
        elif command.startswith("ALERTS"):
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
                
                since_seconds = int(args.get("since", 86400))
                severity = args.get("severity")
                acknowledged = args.get("acknowledged")
                
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
        
        elif command.startswith("CHAT"):
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
                    from embeddings import RAGChatEngine
                    chat_engine = RAGChatEngine(db_path)
                    response = chat_engine.chat(message, context_size=5, since_seconds=3600)
                    conn.sendall((json.dumps({"response": response}) + "\n").encode())
                else:
                    # RAG query mode
                    query = args.get("query")
                    context_size = int(args.get("context_size", 10))
                    since_seconds = int(args.get("since", 3600))
                    
                    from embeddings import RAGChatEngine
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
        
        elif command.startswith("CHAT_HISTORY"):
            # Usage: CHAT_HISTORY
            try:
                from embeddings import RAGChatEngine
                chat_engine = RAGChatEngine(db_path)
                history = chat_engine.get_chat_history()
                conn.sendall((json.dumps(history) + "\n").encode())
            except Exception as exc:
                conn.sendall(f"ERR {exc}\n".encode())
        
        elif command.startswith("CHAT_CLEAR"):
            # Usage: CHAT_CLEAR
            try:
                from embeddings import RAGChatEngine
                chat_engine = RAGChatEngine(db_path)
                chat_engine.clear_chat_history()
                conn.sendall(b"OK history-cleared\n")
            except Exception as exc:
                conn.sendall(f"ERR {exc}\n".encode())
        
        elif command.startswith("CHAT_STATS"):
            # Usage: CHAT_STATS
            try:
                from embeddings import RAGChatEngine
                chat_engine = RAGChatEngine(db_path)
                stats = chat_engine.get_chat_stats()
                conn.sendall((json.dumps(stats) + "\n").encode())
            except Exception as exc:
                conn.sendall(f"ERR {exc}\n".encode())
        
        elif command.startswith("REPORT"):
            # Usage: REPORT GENERATE|SEND|LIST [args...]
            try:
                if len(tokens) < 2:
                    conn.sendall(b"ERR missing report action\n")
                    return
                
                report_action = tokens[1].upper()
                
                if report_action == "GENERATE":
                    # Usage: REPORT GENERATE [since=SECONDS] [format=FORMAT] [output=PATH]
                    args = {}
                    for tok in tokens[2:]:
                        if "=" in tok:
                            k, v = tok.split("=", 1)
                            args[k] = v
                    
                    since_seconds = int(args.get("since", 86400))
                    format_type = args.get("format", "text")
                    output_path = args.get("output")
                    
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
                
                elif report_action == "SEND":
                    # Usage: REPORT SEND [to=EMAIL] [since=SECONDS] [subject=SUBJECT]
                    args = {}
                    for tok in tokens[2:]:
                        if "=" in tok:
                            k, v = tok.split("=", 1)
                            args[k] = v
                    
                    to_email = args.get("to")
                    since_seconds = int(args.get("since", 86400))
                    subject = args.get("subject", "Chimera LogMind Daily Report")
                    
                    if not to_email:
                        conn.sendall(b"ERR missing recipient email\n")
                        return
                    
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
                
                elif report_action == "LIST":
                    # Usage: REPORT LIST [limit=N]
                    args = {}
                    for tok in tokens[2:]:
                        if "=" in tok:
                            k, v = tok.split("=", 1)
                            args[k] = v
                    
                    limit = int(args.get("limit", 10))
                    
                    from reporting import ReportDelivery
                    delivery = ReportDelivery()
                    
                    # List saved reports
                    import os
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
                
                else:
                    conn.sendall(b"ERR unknown report action\n")
            except Exception as exc:
                conn.sendall(f"ERR {exc}\n".encode())
        
        elif command.startswith("AUDIT"):
            # Usage: AUDIT FULL|TOOL|HISTORY|DETAILS [args...]
            try:
                if len(tokens) < 2:
                    conn.sendall(b"ERR missing audit action\n")
                    return
                
                audit_action = tokens[1].upper()
                
                from security_audit import SecurityAuditor
                auditor = SecurityAuditor(db_path)
                
                if audit_action == "FULL":
                    # Run full security audit
                    results = auditor.run_full_audit()
                    conn.sendall((json.dumps(results, indent=2) + "\n").encode())
                
                elif audit_action == "TOOL":
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
                
                elif audit_action == "HISTORY":
                    # Usage: AUDIT HISTORY [tool=TOOL_NAME] [limit=N]
                    args = {}
                    for tok in tokens[2:]:
                        if "=" in tok:
                            k, v = tok.split("=", 1)
                            args[k] = v
                    
                    tool = args.get("tool")
                    limit = int(args.get("limit", 50))
                    
                    history = auditor.get_audit_history(tool, limit)
                    for entry in history:
                        conn.sendall((json.dumps(entry) + "\n").encode())
                
                elif audit_action == "DETAILS":
                    # Usage: AUDIT DETAILS [id=ID]
                    args = {}
                    for tok in tokens[2:]:
                        if "=" in tok:
                            k, v = tok.split("=", 1)
                            args[k] = v
                    
                    audit_id = int(args.get("id"))
                    if not audit_id:
                        conn.sendall(b"ERR missing audit ID\n")
                        return
                    
                    details = auditor.get_audit_details(audit_id)
                    if details:
                        conn.sendall((json.dumps(details, indent=2) + "\n").encode())
                    else:
                        conn.sendall(b"ERR audit not found\n".encode())
                
                else:
                    conn.sendall(b"ERR unknown audit action\n".encode())
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
