import os
import io
import json
import datetime as dt
from unittest.mock import patch
import duckdb
import tempfile

from api.ingest_framework import IngestionFramework
from api.config import LogSource
from api.db import initialize_schema


def make_temp_file(dirpath, name, content, mtime=None):
    fp = os.path.join(dirpath, name)
    with open(fp, 'w', encoding='utf-8') as f:
        f.write(content)
    if mtime is not None:
        os.utime(fp, (mtime, mtime))
    return fp


def test_ingest_files_end_to_end(tmp_path):
    # Create temp log files with syslog-like lines
    now = dt.datetime.now()
    dir1 = tmp_path / 'logs'
    dir1.mkdir()

    # Valid syslog lines
    line1 = '<14>Jan 01 12:00:00 host app[123]: message one'  # priority 14 -> notice
    line2 = '<11>Jan 01 12:00:01 host app: message two'      # priority 11 -> err

    make_temp_file(str(dir1), 'app.log', f"{line1}\n{line2}\n")

    db_path = str(tmp_path / 'files.duckdb')
    conn = duckdb.connect(db_path, read_only=False)
    try:
        initialize_schema(conn)
    finally:
        conn.close()

    fw = IngestionFramework(db_path)
    source = LogSource(
        name='files',
        type='file',
        enabled=True,
        config={
            'paths': [str(dir1)],
            'patterns': ['*.log'],
            'max_file_size_mb': 10,
        }
    )

    inserted, total = fw.ingest_source(source, last_seconds=3600, limit=10)
    assert inserted >= 2
    rows = duckdb.connect(db_path).execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    assert rows == total


def test_collect_and_filter_files(monkeypatch, tmp_path):
    fw = IngestionFramework()
    d = tmp_path / 'd'
    d.mkdir()
    small = make_temp_file(str(d), 'a.log', 'x', mtime=dt.datetime.now().timestamp())
    big = make_temp_file(str(d), 'b.log', 'x' * (2 * 1024 * 1024), mtime=dt.datetime.now().timestamp())

    all_files = fw._collect_files([str(d)], ['*.log'])
    assert small in all_files and big in all_files

    cutoff = dt.datetime.now() - dt.timedelta(seconds=3600)
    valid = fw._filter_files(all_files, max_size_mb=1, cutoff_time=cutoff)
    assert small in valid and big not in valid


@patch('api.ingest_framework.subprocess.run')
@patch('api.ingest_framework.os.uname')
def test_ingest_containers(monkeypatch_uname, monkeypatch_run, tmp_path):
    # Mock docker ps and logs
    class R:
        def __init__(self, code, out):
            self.returncode = code
            self.stdout = out
            self.stderr = ''

    def run_side_effect(cmd, check=False, capture_output=True, text=True):
        if cmd[:3] == ['docker', 'ps', '--format']:
            return R(0, 'c1\nchimera-xyz\n')
        if cmd[:2] == ['docker', 'logs']:
            # Provide two log lines, one per container
            ts = '2024-06-01T10:20:30.123456Z'
            return R(0, f"{ts} stdout hello\n{ts} stderr warn\n")
        return R(1, '')

    monkeypatch_run.side_effect = run_side_effect
    monkeypatch_uname.return_value = type('U', (), {'nodename': 'node'})

    db_path = str(tmp_path / 'containers.duckdb')
    conn = duckdb.connect(db_path, read_only=False)
    try:
        initialize_schema(conn)
    finally:
        conn.close()

    fw = IngestionFramework(db_path)
    source = LogSource(name='containers', type='container', enabled=True,
                       config={'runtime': 'docker', 'include_patterns': ['*'], 'exclude_patterns': ['chimera-*']})

    inserted, total = fw.ingest_source(source, last_seconds=60, limit=10)
    assert inserted >= 2
    assert total >= inserted
