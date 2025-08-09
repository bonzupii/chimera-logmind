import json
import subprocess
import datetime as dt
import types
import duckdb
import pytest

from api.db import initialize_schema, get_connection
from api import ingest as ingest_mod


class FakeCompleted:
    def __init__(self, code: int, out: str, err: str = ""):
        self.returncode = code
        self.stdout = out
        self.stderr = err


def test_journald_ingest_basic(monkeypatch, tmp_path):
    # Prepare fake journalctl output (JSON lines)
    now = int(dt.datetime.now(tz=dt.timezone.utc).timestamp() * 1_000_000)
    lines = []
    for i in range(3):
        entry = {
            "__REALTIME_TIMESTAMP": str(now + i),
            "_HOSTNAME": "testhost",
            "_SYSTEMD_UNIT": "sshd.service",
            "PRIORITY": "4",
            "_PID": "123",
            "MESSAGE": f"hello {i}",
            "__CURSOR": f"cursor-{i}",
        }
        lines.append(json.dumps(entry))
    out = "\n".join(lines)

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(0, out))

    db_path = str(tmp_path / "ingest.duckdb")
    conn = duckdb.connect(db_path, read_only=False)
    try:
        initialize_schema(conn)
        inserted, total = ingest_mod.ingest_journal_into_duckdb(conn, last_seconds=3600, limit=10)
        assert total >= 3
        # Verify ingest_state updated
        cursor = conn.execute("SELECT cursor FROM ingest_state WHERE source='journald'").fetchone()
        assert cursor and cursor[0] == "cursor-2"
    finally:
        conn.close()
