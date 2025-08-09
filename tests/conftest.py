import contextlib
import pytest

try:
    import duckdb
except ImportError:
    duckdb = None


@pytest.fixture()
def temp_db_path(tmp_path):
    db_path = tmp_path / "test.duckdb"
    return str(db_path)


@pytest.fixture()
def duckdb_conn(temp_db_path):
    if duckdb is None:
        pytest.skip("duckdb not installed")
    conn = duckdb.connect(temp_db_path, read_only=False)
    try:
        yield conn
    finally:
        with contextlib.suppress(Exception):
            conn.close()


@pytest.fixture()
def temp_socket_path(tmp_path, monkeypatch):
    sock_path = tmp_path / "chimera.sock"
    monkeypatch.setenv("CHIMERA_API_SOCKET", str(sock_path))
    return str(sock_path)


@pytest.fixture()
def temp_env_paths(tmp_path, monkeypatch, temp_db_path):
    # Set environment for server to use temporary locations
    monkeypatch.setenv("CHIMERA_DB_PATH", temp_db_path)
    log_dir = tmp_path / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CHIMERA_LOG_FILE", str(log_dir / "api.log"))
    monkeypatch.setenv("CHIMERA_LOG_LEVEL", "DEBUG")
    cfg_dir = tmp_path / "etc"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CHIMERA_CONFIG_PATH", str(cfg_dir / "config.json"))
    return {
        "db": temp_db_path,
        "log": str(log_dir / "api.log"),
        "cfg": str(cfg_dir / "config.json"),
    }
