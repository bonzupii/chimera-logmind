#!/usr/bin/env python3
import json
import hashlib
import subprocess
import datetime as dt
import os
import glob
import re
from typing import List, Optional, Tuple, Dict, Any
from abc import ABC, abstractmethod


from .config import LogSource
from .db import get_connection


class LogParser(ABC):
    """Abstract base class for log parsers"""

    @abstractmethod
    def parse_line(self, line: str, source_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse a single log line and return structured data or None if unparseable"""
        pass

    @abstractmethod
    def get_source_type(self) -> str:
        """Return the source type this parser handles"""
        pass


class JournaldParser(LogParser):
    """Parser for journald JSON output"""

    def get_source_type(self) -> str:
        return "journald"

    def parse_line(self, line: str, source_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            entry = json.loads(line)
            return self._parse_journal_entry(entry)
        except json.JSONDecodeError:
            return None

    def _parse_journal_entry(self, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse a journald entry"""
        ts = self._parse_realtime_timestamp(entry.get("__REALTIME_TIMESTAMP"))
        if ts is None:
            return None

        hostname = entry.get("_HOSTNAME")
        unit = entry.get("_SYSTEMD_UNIT") or entry.get("SYSLOG_IDENTIFIER")
        facility = entry.get("SYSLOG_FACILITY")
        severity = self._parse_priority(entry.get("PRIORITY"))
        pid = int(entry.get("_PID", 0)) if entry.get("_PID") else None
        uid = int(entry.get("_UID", 0)) if entry.get("_UID") else None
        gid = int(entry.get("_GID", 0)) if entry.get("_GID") else None
        message = entry.get("MESSAGE")
        cursor = entry.get("__CURSOR")

        return {
            "ts": ts,
            "hostname": hostname,
            "source": "journald",
            "unit": unit,
            "facility": facility,
            "severity": severity,
            "pid": pid,
            "uid": uid,
            "gid": gid,
            "message": message,
            "raw": json.dumps(entry),
            "cursor": cursor,
        }

    def _parse_priority(self, value: Optional[str]) -> Optional[str]:
        mapping = {
            "0": "emerg", "1": "alert", "2": "crit", "3": "err",
            "4": "warning", "5": "notice", "6": "info", "7": "debug",
        }
        if value is None:
            return None
        return mapping.get(str(value), str(value))

    def _parse_realtime_timestamp(self, micros: Optional[str]) -> Optional[dt.datetime]:
        if micros is None:
            return None
        try:
            micros_int = int(micros)
            aware = dt.datetime.fromtimestamp(micros_int / 1_000_000, tz=dt.timezone.utc)
            return aware.replace(tzinfo=None)
        except Exception:
            return None


class SyslogParser(LogParser):
    """Parser for syslog format files"""

    def get_source_type(self) -> str:
        return "file"

    def parse_line(self, line: str, source_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # Syslog format: <priority>timestamp hostname program[pid]: message
        pattern = r'^<(\d+)>(\w+\s+\d+\s+\d+:\d+:\d+)\s+(\S+)\s+(\S+)(?:\[(\d+)\])?:\s*(.*)$'
        match = re.match(pattern, line.strip())

        if not match:
            return None

        priority, timestamp, hostname, program, pid, message = match.groups()

        try:
            # Parse timestamp (assuming current year)
            year = dt.datetime.now().year
            ts_str = f"{year} {timestamp}"
            ts = dt.datetime.strptime(ts_str, "%Y %b %d %H:%M:%S")

            severity = self._parse_priority(priority)
            pid_int = int(pid) if pid else None

            return {
                "ts": ts,
                "hostname": hostname,
                "source": "file",
                "unit": program,
                "facility": None,
                "severity": severity,
                "pid": pid_int,
                "uid": None,
                "gid": None,
                "message": message,
                "raw": line,
                "cursor": None,
            }
        except Exception:
            return None

    def _parse_priority(self, priority: str) -> str:
        priority_int = int(priority)
        severity_level = priority_int & 0x07

        severity_map = {
            0: "emerg", 1: "alert", 2: "crit", 3: "err",
            4: "warning", 5: "notice", 6: "info", 7: "debug"
        }
        return severity_map.get(severity_level, "info")


class ContainerLogParser(LogParser):
    """Parser for container logs"""

    def get_source_type(self) -> str:
        return "container"

    def parse_line(self, line: str, source_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # Docker log format: timestamp stream message
        # Example: 2024-01-15T10:30:45.123456789Z stdout message content
        pattern = r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\s+(stdout|stderr)\s+(.*)$'
        match = re.match(pattern, line.strip())

        if not match:
            return None

        timestamp, stream, message = match.groups()

        try:
            ts = dt.datetime.fromisoformat(timestamp.replace('Z', '+00:00')).replace(tzinfo=None)
            container_name = source_info.get('container_name', 'unknown')

            return {
                "ts": ts,
                "hostname": source_info.get('hostname', 'localhost'),
                "source": "container",
                "unit": container_name,
                "facility": None,
                "severity": "info" if stream == "stdout" else "warning",
                "pid": None,
                "uid": None,
                "gid": None,
                "message": message,
                "raw": line,
                "cursor": None,
            }
        except Exception:
            return None


class IngestionFramework:
    """Framework for ingesting logs from various sources"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        self.parsers = {
            "journald": JournaldParser(),
            "file": SyslogParser(),
            "container": ContainerLogParser(),
        }

    def ingest_source(self, source: LogSource, last_seconds: int = 3600, limit: Optional[int] = None) -> Tuple[int, int]:
        """Ingest logs from a specific source"""
        if source.type == "journald":
            return self._ingest_journald(source, last_seconds, limit)
        elif source.type == "file":
            return self._ingest_files(source, last_seconds, limit)
        elif source.type == "container":
            return self._ingest_containers(source, last_seconds, limit)
        else:
            raise ValueError(f"Unsupported source type: {source.type}")

    def _ingest_journald(self, source: LogSource, last_seconds: int, limit: Optional[int]) -> Tuple[int, int]:
        """Ingest from journald"""
        conn = get_connection(self.db_path)
        try:
            # Get last cursor for this source
            last_cursor_row = conn.execute(
                "SELECT cursor FROM ingest_state WHERE source = ?",
                [source.name]
            ).fetchone()
            after_cursor = last_cursor_row[0] if last_cursor_row and last_cursor_row[0] else None

            # Build journalctl command
            cmd = ["journalctl", "--no-pager", "-o", "json"]
            if after_cursor:
                cmd.extend(["--after-cursor", after_cursor])
            else:
                cmd.extend(["--since", f"-{last_seconds}s"])

            # Apply source filters
            if source.config.get('units'):
                for unit in source.config['units']:
                    cmd.extend(["-u", unit])

            # Exclude units if configured (post-filter if journalctl lacks flag)
            exclude_units = source.config.get('exclude_units', [])

            if limit:
                cmd.extend(["-n", str(limit)])

            # Execute and parse
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"journalctl failed: {proc.stderr.strip()}")

            lines = proc.stdout.splitlines()
            if exclude_units:
                filtered_lines = []
                for line in lines:
                    try:
                        entry = json.loads(line)
                        unit_name = entry.get("_SYSTEMD_UNIT") or entry.get("SYSLOG_IDENTIFIER") or ""
                        if any(self._unit_matches_pattern(unit_name, pat) for pat in exclude_units):
                            continue
                        filtered_lines.append(line)
                    except Exception:
                        filtered_lines.append(line)
                lines = filtered_lines

            return self._process_entries(conn, source.name, lines, after_cursor)

        finally:
            conn.close()

    def _collect_files(self, paths: List[str], patterns: List[str]) -> List[str]:
        """Collect all files from paths using patterns"""
        all_files = []
        for path in paths:
            if os.path.isfile(path):
                all_files.append(path)
            elif os.path.isdir(path):
                for pattern in patterns:
                    all_files.extend(glob.glob(os.path.join(path, pattern)))
        return all_files

    def _filter_files(self, all_files: List[str], max_size_mb: int, cutoff_time: dt.datetime) -> List[str]:
        """Filter files by size and modification time"""
        valid_files = []
        max_size_bytes = max_size_mb * 1024 * 1024

        for file_path in all_files:
            try:
                stat = os.stat(file_path)
                if stat.st_size > max_size_bytes:
                    continue
                if dt.datetime.fromtimestamp(stat.st_mtime) < cutoff_time:
                    continue
                valid_files.append(file_path)
            except OSError:
                continue
        return valid_files

    def _parse_files(self, valid_files: List[str], limit: Optional[int]) -> List[Dict[str, Any]]:
        """Parse lines from valid files"""
        entries = []
        for file_path in valid_files:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line_num, line in enumerate(f):
                        if limit and len(entries) >= limit:
                            break
                        if line.strip():
                            source_info = {
                                'file_path': file_path,
                                'line_number': line_num,
                            }
                            parsed = self.parsers['file'].parse_line(line, source_info)
                            if parsed:
                                entries.append(parsed)
            except OSError:
                continue
        return entries

    def _ingest_files(self, source: LogSource, last_seconds: int, limit: Optional[int]) -> Tuple[int, int]:
        """Ingest from log files"""
        conn = get_connection(self.db_path)
        try:
            paths = source.config.get('paths', [])
            patterns = source.config.get('patterns', ['*.log'])
            max_size_mb = source.config.get('max_file_size_mb', 100)

            # Collect all files from paths and patterns
            all_files = self._collect_files(paths, patterns)

            # Filter files by size and modification time
            cutoff_time = dt.datetime.now() - dt.timedelta(seconds=last_seconds)
            valid_files = self._filter_files(all_files, max_size_mb, cutoff_time)

            # Parse files and extract entries
            entries = self._parse_files(valid_files, limit)

            return self._process_entries(conn, source.name, entries, None)

        finally:
            conn.close()

    def _ingest_containers(self, source: LogSource, last_seconds: int, limit: Optional[int]) -> Tuple[int, int]:
        """Ingest from containers (Docker)"""
        conn = get_connection(self.db_path)
        try:
            runtime = source.config.get('runtime', 'docker')
            include_patterns = source.config.get('include_patterns', ['*'])
            exclude_patterns = source.config.get('exclude_patterns', [])

            if runtime != 'docker':
                raise ValueError(f"Unsupported container runtime: {runtime}")

            # Get list of running containers
            cmd = ["docker", "ps", "--format", "{{.Names}}"]
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
            if proc.returncode != 0:
                return (0, 0)

            container_names = proc.stdout.strip().split('\n') if proc.stdout.strip() else []

            # Filter containers
            filtered_containers = []
            for name in container_names:
                if not name:
                    continue

                # Check include/exclude patterns
                include_match = any(re.match(pattern, name) for pattern in include_patterns)
                exclude_match = any(re.match(pattern, name) for pattern in exclude_patterns)

                if include_match and not exclude_match:
                    filtered_containers.append(name)

            entries = []
            for container_name in filtered_containers:
                if limit and len(entries) >= limit:
                    break

                # Get container logs
                cmd = ["docker", "logs", "--since", f"{last_seconds}s", container_name]
                proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
                if proc.returncode != 0:
                    continue

                for line in proc.stdout.splitlines():
                    if limit and len(entries) >= limit:
                        break
                    if line.strip():
                        source_info = {
                            'container_name': container_name,
                            'hostname': os.uname().nodename,
                        }
                        parsed = self.parsers['container'].parse_line(line, source_info)
                        if parsed:
                            entries.append(parsed)

            return self._process_entries(conn, source.name, entries, None)

        finally:
            conn.close()

    def _process_entries(self, conn, source_name: str, entries: List[Any], last_cursor: Optional[str]) -> Tuple[int, int]:
        """Process parsed entries and insert into database"""
        if not entries:
            return (0, 0)

        rows = []
        last_seen_cursor = last_cursor

        for entry in entries:
            if isinstance(entry, str):
                # Raw line from journald
                parsed = self.parsers['journald'].parse_line(entry, {})
                if not parsed:
                    continue
                entry = parsed

            # Compute fingerprint
            fp_src = f"{entry['ts']}|{entry['hostname']}|{entry['unit']}|{entry['severity']}|{entry['pid']}|{entry['message']}".encode()
            fingerprint = hashlib.sha256(fp_src).hexdigest()

            # Deterministic numeric id from fingerprint (first 8 bytes of sha256)
            import hashlib as _hashlib
            digest = _hashlib.sha256(fp_src).digest()
            numeric_id = int.from_bytes(digest[:8], byteorder="big", signed=True)
            row = (
                numeric_id,
                entry['ts'], entry['hostname'], entry['source'], entry['unit'],
                entry['facility'], entry['severity'], entry['pid'], entry['uid'],
                entry['gid'], entry['message'], entry['raw'], fingerprint, entry['cursor']
            )
            rows.append(row)

            if entry['cursor']:
                last_seen_cursor = entry['cursor']

        if not rows:
            return (0, 0)

        # Insert into database
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO logs (id, ts, hostname, source, unit, facility, severity, pid, uid, gid, message, raw, fingerprint, cursor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            rows,
        )

        # Update cursor if advanced
        if last_seen_cursor and last_seen_cursor != last_cursor:
            conn.execute(
                "INSERT OR REPLACE INTO ingest_state(source, cursor, updated_at) VALUES(?, ?, CURRENT_TIMESTAMP)",
                [source_name, last_seen_cursor],
            )

        return (len(rows), conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0])

    def _unit_matches_pattern(self, unit: str, pattern: str) -> bool:
        # Simple glob-like pattern matching where * matches any substring
        if pattern == unit:
            return True
        if '*' in pattern:
            import re as _re
            regex = '^' + _re.escape(pattern).replace('\\*', '.*') + '$'
            return _re.match(regex, unit) is not None
        return False
