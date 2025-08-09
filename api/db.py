import os
from typing import Optional

try:
    import duckdb  # type: ignore
except Exception as exc:  # pragma: no cover
    duckdb = None  # type: ignore


DEFAULT_DB_PATH = os.environ.get("CHIMERA_DB_PATH", os.path.abspath(os.path.join(os.getcwd(), "data/chimera.duckdb")))


def ensure_parent_directory(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, mode=0o750, exist_ok=True)
        try:
            os.chmod(parent, 0o750)
        except PermissionError:
            pass


def get_connection(db_path: Optional[str] = None):
    if duckdb is None:
        raise RuntimeError("duckdb module is not installed; please install python3-duckdb or pip install duckdb")
    path = db_path or DEFAULT_DB_PATH
    ensure_parent_directory(path)
    conn = duckdb.connect(path, read_only=False)
    return conn


def initialize_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            ts TIMESTAMP NOT NULL,
            hostname TEXT,
            source TEXT,
            unit TEXT,
            facility TEXT,
            severity TEXT,
            pid INTEGER,
            uid INTEGER,
            gid INTEGER,
            message TEXT,
            raw JSON,
            fingerprint TEXT,
            cursor TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ingest_state (
            source TEXT PRIMARY KEY,
            cursor TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    # Backfill columns for existing installations (best-effort)
    try:
        conn.execute("ALTER TABLE logs ADD COLUMN fingerprint TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE logs ADD COLUMN cursor TEXT")
    except Exception:
        pass
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_logs_unit ON logs(unit);
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_logs_hostname ON logs(hostname);
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_logs_severity ON logs(severity);
        """
    )
    # Uniqueness to prevent duplicates; NULLs are allowed and not considered equal
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uidx_logs_cursor ON logs(cursor);
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uidx_logs_fingerprint ON logs(fingerprint);
        """
    )
