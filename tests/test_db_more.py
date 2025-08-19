from unittest.mock import MagicMock, patch
import types
import pytest

import api.db as db


class Result:
    def __init__(self, one=None, all_list=None):
        self._one = one
        self._all = all_list or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


class FakeConn:
    def __init__(self, logs_table_exists=False, id_missing=False, pragma_has_column=False, fail_on=None):
        self.executed = []
        self.logs_table_exists = logs_table_exists
        self.id_missing = id_missing
        self.pragma_has_column = pragma_has_column
        self.fail_on = fail_on or set()

    def execute(self, sql, params=None):
        self.executed.append((sql.strip(), tuple(params) if params else None))
        sql_low = sql.lower()
        # Inject failures for error path coverage
        for marker in list(self.fail_on):
            if marker in sql_low:
                raise Exception(f"forced failure on {marker}")
        # Handle checks used by code
        if "from information_schema.tables" in sql_low and "table_name = 'logs'" in sql_low:
            # Return 1 if exists
            return Result((1 if self.logs_table_exists else 0,))
        if "from information_schema.columns" in sql_low and "table_name='logs'" in sql_low and "column_name='id'" in sql_low:
            # Return 0 if id missing
            return Result((0 if self.id_missing else 1,))
        if "from pragma_table_info('logs')" in sql_low or 'from pragma_table_info("logs")' in sql_low:
            # Return 1 if column exists in pragma
            return Result((1 if self.pragma_has_column else 0,))
        # For SELECT last_insert_rowid() used elsewhere
        if "last_insert_rowid" in sql_low:
            return Result((12345,))
        # Generic return for other SELECT count(*) when not critical
        if sql_low.strip().startswith("select"):
            return Result((0,))
        # For non-select, return self to allow chaining if needed
        return self


def test_get_connection_duckdb_missing(monkeypatch):
    monkeypatch.setattr(db, 'duckdb', None)
    with pytest.raises(RuntimeError):
        db.get_connection()


def test_get_connection_success(monkeypatch):
    calls = {}

    class FakeDuckDB:
        def connect(self, path, read_only=False):
            calls['path'] = path
            calls['read_only'] = read_only
            return FakeConn()

    # Avoid touching filesystem
    monkeypatch.setattr(db, 'ensure_parent_directory', lambda p: None)
    monkeypatch.setattr(db, 'duckdb', FakeDuckDB())

    conn = db.get_connection('/tmp/test.duckdb')
    assert isinstance(conn, FakeConn)
    assert calls['path'] == '/tmp/test.duckdb'
    assert calls['read_only'] is False


def test_initialize_schema_with_migration_and_indexes():
    # Simulate existing logs table with missing id to trigger migration
    conn = FakeConn(logs_table_exists=True, id_missing=True, pragma_has_column=False)

    db.initialize_schema(conn)


def test_migrate_logs_table_exception_path():
    # Force exception so function hits warning path without raising
    conn = FakeConn()
    conn.fail_on.add("information_schema.tables")
    db._migrate_logs_table(conn)
    # Only the initial query attempted and warning logged; ensure it didn't crash
    assert any("information_schema.tables" in sql.lower() for sql, _ in conn.executed)


def test_clear_table_success():
    conn = FakeConn()
    db.clear_table(conn, 'logs')
    sqls = [sql for sql, _ in conn.executed]
    assert any(s.lower().startswith('delete from logs') for s in sqls)


def test_clear_table_error():
    class ErrConn(FakeConn):
        def execute(self, sql, params=None):
            raise Exception("boom")
    with pytest.raises(Exception):
        db.clear_table(ErrConn(), 'logs')


def test_ensure_parent_directory_errors(monkeypatch, caplog, tmp_path):
    # Provide a path with parent; simulate permission error via os.makedirs mocking
    called = {'count': 0}
    def makedirs_fail(path, mode=0o750, exist_ok=True):
        called['count'] += 1
        raise PermissionError('denied')
    monkeypatch.setattr('os.makedirs', makedirs_fail)
    # Also chmod may raise a generic exception after
    def chmod_fail(path, mode):
        raise Exception('chmod fail')
    monkeypatch.setattr('os.chmod', chmod_fail)
    # Use module-qualified names for monkeypatch
    import os as _os
    monkeypatch.setattr(_os, 'makedirs', makedirs_fail, raising=True)
    monkeypatch.setattr(_os, 'chmod', chmod_fail, raising=True)
    db.ensure_parent_directory(str(tmp_path / 'a' / 'b.duckdb'))
    assert called['count'] == 1


def test_ensure_parent_directory_generic_exception(monkeypatch, tmp_path):
    # Make makedirs succeed and chmod raise a generic Exception to hit generic except path
    def makedirs_ok(path, mode=0o750, exist_ok=True):
        return None
    def chmod_fail(path, mode):
        raise Exception('generic fail')
    import os as _os
    monkeypatch.setattr(_os, 'makedirs', makedirs_ok, raising=True)
    monkeypatch.setattr(_os, 'chmod', chmod_fail, raising=True)
    db.ensure_parent_directory(str(tmp_path / 'x' / 'y.duckdb'))


def test_get_connection_connect_failure(monkeypatch):
    class Duck:
        def connect(self, path, read_only=False):
            raise Exception('connect failed')
    monkeypatch.setattr(db, 'duckdb', Duck())
    monkeypatch.setattr(db, 'ensure_parent_directory', lambda p: None)
    with pytest.raises(RuntimeError):
        db.get_connection('/tmp/nowhere.duckdb')


def test_ensure_column_exists_error_paths(caplog):
    conn = FakeConn()
    # Force failure on pragma_table_info check
    conn.fail_on.add('pragma_table_info')
    # Should log warning and attempt to add column
    db._ensure_column_exists(conn, 'logs', 'cursor', 'TEXT')
    # Now force failure on ALTER TABLE to hit warning path
    conn2 = FakeConn()
    conn2.fail_on.add('alter table')
    db._ensure_column_exists(conn2, 'logs', 'cursor', 'TEXT')


def test_create_tables_error_paths():
    # Tables creation raise exceptions should be caught and re-raised
    conn = FakeConn()
    # Force errors on create statements one by one
    for marker in [
        'create table if not exists logs',
        'create table if not exists ingest_state',
        'create table if not exists log_embeddings',
        'create table if not exists system_alerts'
    ]:
        conn = FakeConn()
        conn.fail_on.add(marker)
        with pytest.raises(Exception):
            db._create_tables(conn)


def test_create_indexes_warning_paths():
    conn = FakeConn()
    # Make index creation fail to hit warnings
    conn.fail_on.add('create index if not exists')
    conn.fail_on.add('create unique index if not exists')
    db._create_indexes(conn)


def test_initialize_schema_calls_paths():
    # Force migrate to be robust; also ensure _ensure_column_exists is called
    conn = FakeConn(logs_table_exists=True, id_missing=False, pragma_has_column=True)
    db.initialize_schema(conn)
