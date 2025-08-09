import duckdb

from api.db import initialize_schema, get_connection


def test_initialize_schema_creates_tables(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("CHIMERA_DB_PATH", db_path)
    conn = get_connection(db_path)
    try:
        initialize_schema(conn)
        # Check core tables exist
        tables = set(r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables").fetchall())
        assert "logs" in tables
        assert "ingest_state" in tables
        assert "log_embeddings" in tables
        # DuckDB does not expose show_indexes pragma across versions consistently; ensure a query runs
        conn.execute("SELECT COUNT(*) FROM logs WHERE ts >= CURRENT_TIMESTAMP - INTERVAL 1 HOUR").fetchone()
    finally:
        conn.close()


def test_schema_migration_adds_id(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_migrate.duckdb")
    conn = duckdb.connect(db_path, read_only=False)
    try:
        # Simulate old schema without id
        conn.execute(
            """
            CREATE TABLE logs (
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
        from api.db import initialize_schema
        initialize_schema(conn)
        # Verify id column exists
        cols = [r[1] for r in conn.execute("PRAGMA table_info('logs')").fetchall()]
        assert "id" in cols
    finally:
        conn.close()
