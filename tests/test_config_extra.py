import os
import json
import tempfile
from api.config import ChimeraConfig, LogSource


def test_config_save_and_load_env_path(monkeypatch, tmp_path):
    cfg = ChimeraConfig(
        log_sources=[],
        db_path='/tmp/db.duckdb',
        socket_path='/tmp/api.sock',
        max_ingest_limit=123,
        default_retention_days=7,
    )
    conf_dir = tmp_path / 'conf'
    conf_dir.mkdir()
    conf_file = conf_dir / 'config.json'
    monkeypatch.setenv('CHIMERA_CONFIG_PATH', str(conf_file))

    # Save without passing path to hit env fallback
    cfg.save()

    # Load without passing path to hit env fallback
    loaded = ChimeraConfig.load()
    assert loaded.db_path == '/tmp/db.duckdb'
    assert loaded.socket_path == '/tmp/api.sock'
    assert loaded.max_ingest_limit == 123
    assert loaded.default_retention_days == 7


def test_config_add_remove_update_edge_cases():
    cfg = ChimeraConfig(log_sources=[], db_path='db', socket_path='sock')

    # add_source with duplicate name raises
    src = LogSource(name='s1', type='file')
    cfg.add_source(src)
    try:
        cfg.add_source(LogSource(name='s1', type='file'))
        assert False, 'Expected ValueError'
    except ValueError:
        pass

    # remove_source returns False when not found
    assert cfg.remove_source('nope') is False

    # update_source returns False when not found
    assert cfg.update_source('nope', enabled=False) is False

    # update existing
    assert cfg.update_source('s1', enabled=False) is True
    assert cfg.get_source_by_name('s1').enabled is False
