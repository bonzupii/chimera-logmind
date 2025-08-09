import os
import logging
from typing import Optional

logger = logging.getLogger("chimera")

try:
    import duckdb  # type: ignore
except Exception as exc:  # pragma: no cover
    logger.warning(f"duckdb module not found: {exc}")
    duckdb = None  # type: ignore


DEFAULT_DB_PATH = os.environ.get("CHIMERA_DB_PATH", os.path.abspath(os.path.join(os.getcwd(), "data/chimera.duckdb")))


def ensure_parent_directory(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        try:
            os.makedirs(parent, mode=0o750, exist_ok=True)
            os.chmod(parent, 0o750)
        except PermissionError as e:
            logger.error(f"Permission denied creating/setting permissions for {parent}: {e}")
        except Exception as e:
            logger.error(f"Error ensuring parent directory {parent}: {e}")


def get_connection(db_path: Optional[str] = None):
    if duckdb is None:
        logger.error("Attempted to get DB connection but duckdb module is not installed.")
        raise RuntimeError("duckdb module is not installed; please install python3-duckdb or pip install duckdb")
    path = db_path or DEFAULT_DB_PATH
    ensure_parent_directory(path)
    try:
        conn = duckdb.connect(path, read_only=False)
        logger.info(f"Successfully connected to DuckDB at {path}")
    except Exception as e:
        logger.error(f"Failed to connect to DuckDB at {path}: {e}")
        raise RuntimeError(f"Failed to connect to DuckDB at {path}: {e}") from e
    return conn


def initialize_schema(conn) -> None:
    logger.info("Initializing database schema...")

    def ensure_column_exists(table_name: str, column_name: str, column_type: str) -> None:
        """Add a column only if it does not already exist (avoids startup warnings)."""
        try:
            exists = (
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.columns
                    WHERE table_name = ? AND column_name = ?
                    """,
                    [table_name, column_name],
                ).fetchone()[0]
                > 0
            )
        except Exception as e:
            logger.warning(f"Failed to check column existence for {table_name}.{column_name}: {e}")
            exists = False
        if not exists:
            try:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                logger.debug(f"Added '{column_name}' column to '{table_name}' table.")
            except Exception as e:
                # If this fails, surface it once here rather than warn every startup
                logger.warning(
                    f"Could not add '{column_name}' column to '{table_name}' table: {e}"
                )

    # Best-effort: if an older installation exists, make sure required columns exist
    try:
        table_exists = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'logs'"
        ).fetchone()[0]
        if table_exists:
            # Ensure optional columns exist (pre-migration)
            ensure_column_exists("logs", "fingerprint", "TEXT")
            ensure_column_exists("logs", "cursor", "TEXT")
            # If id column missing, migrate to new table with synthetic IDs
            id_missing = (
                conn.execute(
                    "SELECT COUNT(*) FROM information_schema.columns WHERE table_name='logs' AND column_name='id'"
                ).fetchone()[0]
                == 0
            )
            if id_missing:
                logger.info("Migrating 'logs' table to add 'id' primary key...")
                conn.execute(
                    """
                    CREATE TABLE logs_new (
                        id BIGINT PRIMARY KEY,
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
                # Compute stable BIGINT id from fingerprint or message+ts when fingerprint is null
                conn.execute(
                    """
                    INSERT INTO logs_new (id, ts, hostname, source, unit, facility, severity, pid, uid, gid, message, raw, fingerprint, cursor)
                    SELECT 
                        CAST(hash(COALESCE(fingerprint, hostname || source || unit || COALESCE(severity,'') || COALESCE(CAST(pid AS VARCHAR),'') || COALESCE(message,'') || COALESCE(CAST(ts AS VARCHAR),''))) AS BIGINT) as id,
                        ts, hostname, source, unit, facility, severity, pid, uid, gid, message, raw, fingerprint, cursor
                    FROM logs
                    """
                )
                conn.execute("DROP TABLE logs")
                conn.execute("ALTER TABLE logs_new RENAME TO logs")
                logger.info("Migration of 'logs' table complete.")
    except Exception as e:
        logger.warning(f"Pre-migration checks failed or skipped: {e}")

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id BIGINT PRIMARY KEY,
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
        logger.debug("Table 'logs' created or already exists.")
    except Exception as e:
        logger.error(f"Error creating logs table: {e}")
        raise
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ingest_state (
                source TEXT PRIMARY KEY,
                cursor TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        logger.debug("Table 'ingest_state' created or already exists.")
    except Exception as e:
        logger.error(f"Error creating ingest_state table: {e}")
        raise
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS log_embeddings (
                log_id BIGINT PRIMARY KEY,
                indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (log_id) REFERENCES logs(id)
            );
            """
        )
        logger.debug("Table 'log_embeddings' created or already exists.")
    except Exception as e:
        logger.error(f"Error creating log_embeddings table: {e}")
        raise
    # Backfill columns for existing installations (idempotent, without warnings)
    ensure_column_exists("logs", "fingerprint", "TEXT")
    ensure_column_exists("logs", "cursor", "TEXT")
    
    try:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
            """
        )
        logger.debug("Index 'idx_logs_ts' created or already exists.")
    except Exception as e:
        logger.warning(f"Could not create index 'idx_logs_ts': {e}")
    try:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_logs_unit ON logs(unit);
            """
        )
        logger.debug("Index 'idx_logs_unit' created or already exists.")
    except Exception as e:
        logger.warning(f"Could not create index 'idx_logs_unit': {e}")
    try:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_logs_hostname ON logs(hostname);
            """
        )
        logger.debug("Index 'idx_logs_hostname' created or already exists.")
    except Exception as e:
        logger.warning(f"Could not create index 'idx_logs_hostname': {e}")
    try:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_logs_severity ON logs(severity);
            """
        )
        logger.debug("Index 'idx_logs_severity' created or already exists.")
    except Exception as e:
        logger.warning(f"Could not create index 'idx_logs_severity': {e}")
    # Uniqueness to prevent duplicates; NULLs are allowed and not considered equal
    try:
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uidx_logs_cursor ON logs(cursor);
            """
        )
        logger.debug("Unique index 'uidx_logs_cursor' created or already exists.")
    except Exception as e:
        logger.warning(f"Could not create unique index 'uidx_logs_cursor': {e}")
    try:
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uidx_logs_fingerprint ON logs(fingerprint);
            """
        )
        logger.debug("Unique index 'uidx_logs_fingerprint' created or already exists.")
    except Exception as e:
        logger.warning(f"Could not create unique index 'uidx_logs_fingerprint': {e}")
    logger.info("Database schema initialization complete.")
