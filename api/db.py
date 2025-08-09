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


def _ensure_column_exists(conn, table_name: str, column_name: str, column_type: str) -> None:
    """Add a column only if it does not already exist (avoids startup warnings)."""
    try:
        # Use a literal for table name (we only call with trusted names)
        exists = (
            conn.execute(
                f"SELECT COUNT(*) FROM pragma_table_info('{table_name}') WHERE name = ?",
                [column_name],
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
            logger.warning(
                f"Could not add '{column_name}' column to '{table_name}' table: {e}"
            )


def _migrate_logs_table(conn) -> None:
    """Handle migration of existing logs table if needed."""
    try:
        table_exists = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'logs'"
        ).fetchone()[0]
        if table_exists:
            # Ensure optional columns exist (pre-migration)
            _ensure_column_exists(conn, "logs", "fingerprint", "TEXT")
            _ensure_column_exists(conn, "logs", "cursor", "TEXT")
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


def _create_tables(conn) -> None:
    """Create all required database tables."""
    # Create logs table
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

    # Create ingest_state table
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

    # Create log_embeddings table
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

    # Create system_alerts table
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS system_alerts (
                id BIGINT PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL,
                alert_type TEXT,
                severity TEXT,
                message TEXT,
                metric_data JSON,
                acknowledged BOOLEAN
            );
            """
        )
        logger.debug("Table 'system_alerts' created or already exists.")
    except Exception as e:
        logger.error(f"Error creating system_alerts table: {e}")
        raise


def _create_indexes(conn) -> None:
    """Create all required database indexes."""
    indexes = [
        ("idx_logs_ts", "logs(ts)"),
        ("idx_logs_unit", "logs(unit)"),
        ("idx_logs_hostname", "logs(hostname)"),
        ("idx_logs_severity", "logs(severity)"),
    ]

    unique_indexes = [
        ("uidx_logs_cursor", "logs(cursor)"),
        ("uidx_logs_fingerprint", "logs(fingerprint)"),
    ]

    # Create regular indexes
    for index_name, index_def in indexes:
        try:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {index_def};")
            logger.debug(f"Index '{index_name}' created or already exists.")
        except Exception as e:
            logger.warning(f"Could not create index '{index_name}': {e}")

    # Create unique indexes
    for index_name, index_def in unique_indexes:
        try:
            conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {index_def};")
            logger.debug(f"Unique index '{index_name}' created or already exists.")
        except Exception as e:
            logger.warning(f"Could not create unique index '{index_name}': {e}")


def initialize_schema(conn) -> None:
    """Initialize database schema with tables and indexes."""
    logger.info("Initializing database schema...")

    # Handle migration of existing installations
    _migrate_logs_table(conn)

    # Create all tables
    _create_tables(conn)

    # Backfill columns for existing installations (idempotent, without warnings)
    _ensure_column_exists(conn, "logs", "fingerprint", "TEXT")
    _ensure_column_exists(conn, "logs", "cursor", "TEXT")

    # Create all indexes
    _create_indexes(conn)

    logger.info("Database schema initialization complete.")

def clear_table(conn, table_name: str) -> None:
    """Clears all data from the specified table."""
    try:
        conn.execute(f"DELETE FROM {table_name}")
        logger.info(f"Table '{table_name}' cleared.")
    except Exception as e:
        logger.error(f"Error clearing table '{table_name}': {e}")
        raise
