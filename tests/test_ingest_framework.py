import json
import subprocess
import datetime as dt
import duckdb
import pytest

from api.ingest_framework import IngestionFramework
from api.db import initialize_schema
from api.config import LogSource


class FakeCompleted:
    def __init__(self, code: int, out: str, err: str = ""):
        self.returncode = code
        self.stdout = out
        self.stderr = err


def test_framework_journald_ingest_with_exclude(tmp_path, monkeypatch):
    now = int(dt.datetime.now(tz=dt.timezone.utc).timestamp() * 1_000_000)
    allowed = json.dumps({
        "__REALTIME_TIMESTAMP": str(now),
        "_HOSTNAME": "h",
        "_SYSTEMD_UNIT": "nginx.service",
        "MESSAGE": "ok",
        "PRIORITY": "6",
        "__CURSOR": "c1"
    })
    excluded = json.dumps({
        "__REALTIME_TIMESTAMP": str(now+1),
        "_HOSTNAME": "h",
        "_SYSTEMD_UNIT": "systemd-networkd.service",
        "MESSAGE": "skip",
        "PRIORITY": "6",
        "__CURSOR": "c2"
    })
    out = "\n".join([allowed, excluded])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(0, out))

    db_path = str(tmp_path / "fw.duckdb")
    fw = IngestionFramework(db_path)
    conn = duckdb.connect(db_path, read_only=False)
    try:
        initialize_schema(conn)
    finally:
        conn.close()

    source = LogSource(name="j", type="journald", enabled=True, config={
        "exclude_units": ["systemd-*"]
    })

    inserted, total = fw.ingest_source(source, last_seconds=3600, limit=10)
    assert inserted >= 1
    # Ensure excluded unit not present
    rows = duckdb.connect(db_path).execute("SELECT unit FROM logs").fetchall()
    units = {r[0] for r in rows}
    assert "systemd-networkd.service" not in units
