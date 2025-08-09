import json
import hashlib
import subprocess
import datetime as dt
import logging
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger("chimera")

JOURNALCTL_BIN = "journalctl"


def validate_journald_cursor(cursor: str) -> bool:
    """Validate journald cursor format to prevent command injection.

    Journald cursors are base64-like strings with specific characters.
    """
    if not cursor or len(cursor) > 500:  # Reasonable length limit
        return False

    # Allow alphanumeric, +, /, =, -, and _ characters (base64 + journald specific)
    import re
    if not re.match(r'^[A-Za-z0-9+/=_-]+$', cursor):
        return False

    return True


def _parse_priority(value: Optional[str]) -> Optional[str]:
    mapping = {
        "0": "emerg",
        "1": "alert",
        "2": "crit",
        "3": "err",
        "4": "warning",
        "5": "notice",
        "6": "info",
        "7": "debug",
    }
    if value is None:
        return None
    return mapping.get(str(value), str(value))


def _parse_realtime_timestamp(micros: Optional[str]) -> Optional[dt.datetime]:
    if micros is None:
        return None
    try:
        # journald __REALTIME_TIMESTAMP is in microseconds
        micros_int = int(micros)
        aware = dt.datetime.fromtimestamp(micros_int / 1_000_000, tz=dt.timezone.utc)
        # DuckDB TIMESTAMP is naive (no tz). Store as UTC naive.
        return aware.replace(tzinfo=None)
    except Exception as e:
        logger.warning(f"Failed to parse realtime timestamp {micros}: {e}")
        return None


def _journalctl_json_lines(last_seconds: int, limit: Optional[int], after_cursor: Optional[str]) -> Iterable[dict]:
    # Validate cursor parameter to prevent command injection
    if after_cursor and not validate_journald_cursor(after_cursor):
        logger.error(f"Invalid journald cursor format: {after_cursor[:50]}...")
        raise ValueError("Invalid journald cursor format")

    cmd = [
        JOURNALCTL_BIN,
        "--no-pager",
        "-o",
        "json",
    ]
    if after_cursor:
        cmd.extend(["--after-cursor", after_cursor])
    else:
        since = f"-{last_seconds}s"
        cmd.extend(["--since", since])
    if limit is not None and limit > 0:
        cmd.extend(["-n", str(limit)])
    logger.debug(f"Executing journalctl command: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        logger.error("journalctl command timed out after 300 seconds")
        raise RuntimeError("journalctl command timed out")

    if proc.returncode != 0:
        logger.error(f"journalctl failed with exit code {proc.returncode}: {proc.stderr.strip()}")
        raise RuntimeError(f"journalctl failed with exit code {proc.returncode}: {proc.stderr.strip()}")
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse journalctl JSON line: {line[:100]}... Error: {e}")
            continue


def ingest_journal_into_duckdb(conn, last_seconds: int = 3600, limit: Optional[int] = None) -> Tuple[int, int]:
    logger.info(f"Starting journald ingestion for last {last_seconds}s, limit {limit or 'None'}")
    rows: List[Tuple] = []
    # Find last cursor
    last_cursor_row = conn.execute("SELECT cursor FROM ingest_state WHERE source = 'journald'").fetchone()
    after_cursor: Optional[str] = last_cursor_row[0] if last_cursor_row and last_cursor_row[0] else None
    logger.debug(f"Last cursor for journald: {after_cursor or 'None'}")
    last_seen_cursor: Optional[str] = None

    try:
        for entry in _journalctl_json_lines(last_seconds=last_seconds, limit=limit, after_cursor=after_cursor):
            ts = _parse_realtime_timestamp(entry.get("__REALTIME_TIMESTAMP"))
            if ts is None:
                logger.debug(f"Skipping entry due to missing/invalid timestamp: {entry.get('MESSAGE', '')[:50]}...")
                continue
            hostname = entry.get("_HOSTNAME")
            unit = entry.get("_SYSTEMD_UNIT") or entry.get("SYSLOG_IDENTIFIER")
            facility = entry.get("SYSLOG_FACILITY")
            severity = _parse_priority(entry.get("PRIORITY"))
            pid = int(entry.get("_PID", 0)) if entry.get("_PID") else None
            uid = int(entry.get("_UID", 0)) if entry.get("_UID") else None
            gid = int(entry.get("_GID", 0)) if entry.get("_GID") else None
            message = entry.get("MESSAGE")
            raw_json = json.dumps(entry)
            cursor = entry.get("__CURSOR")
            if cursor:
                last_seen_cursor = cursor
            # Compute a lightweight fingerprint to dedupe when cursor is missing
            fp_src = f"{ts}|{hostname}|{unit}|{severity}|{pid}|{message}".encode()
            fingerprint = hashlib.sha256(fp_src).hexdigest()
            # Deterministic numeric id from fingerprint (first 8 bytes of sha256)
            digest = hashlib.sha256(fp_src).digest()
            numeric_id = int.from_bytes(digest[:8], byteorder="big", signed=True)
            rows.append((numeric_id, ts, hostname, "journald", unit, facility, severity, pid, uid, gid, message, raw_json, fingerprint, cursor))

        if not rows:
            logger.info("No new journald entries to ingest.")
            return (0, 0)

        cur = conn.cursor()
        inserted_count = 0
        try:
            # Get count before insert
            count_before = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            cur.executemany(
                """
                INSERT INTO logs (id, ts, hostname, source, unit, facility, severity, pid, uid, gid, message, raw, fingerprint, cursor)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                rows,
            )
            # Get count after insert and calculate difference
            count_after = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            inserted_count = count_after - count_before
            logger.info(f"Attempted to insert {len(rows)} journald entries. Actual inserted count: {inserted_count}")
        except Exception as e:
            logger.error(f"Error during batch insert of journald logs: {e}")
            raise

        # Update last cursor if advanced
        if last_seen_cursor:
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO ingest_state(source, cursor, updated_at) VALUES('journald', ?, CURRENT_TIMESTAMP)",
                    [last_seen_cursor],
                )
                logger.debug(f"Updated journald ingest cursor to {last_seen_cursor}")
            except Exception as e:
                logger.error(f"Error updating ingest_state cursor for journald: {e}")
                raise

        total_logs_in_db = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
        logger.info(f"Journald ingestion complete. Total logs in DB: {total_logs_in_db}")
        return (inserted_count, total_logs_in_db)
    except Exception as e:
        logger.error(f"An error occurred during ingest_journal_into_duckdb: {e}")
        raise
