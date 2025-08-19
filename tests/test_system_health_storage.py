import json
import datetime as dt
from unittest.mock import patch, MagicMock

from api.system_health import SystemMetricsCollector, SystemHealthMonitor


class FakeConn:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql.strip(), params))
        sql_low = sql.lower()
        if sql_low.startswith('select'):
            return self
        return self

    def cursor(self):
        return self

    def fetchone(self):
        # If last executed was COUNT(*), return count of rows
        if self.executed and 'count(*)' in self.executed[-1][0].lower():
            return (len(self.rows),)
        return (len(self.rows),)

    def fetchall(self):
        # Return structured rows similar to schema
        return self.rows

    def close(self):
        pass

    def executemany(self, sql, seq):
        # record batch inserts
        self.executed.append((sql.strip(), seq))
        return self


@patch('api.system_health.get_connection')
def test_store_metrics_and_get_metrics(mock_get_conn):
    # Prepare fake connection that records inserts
    conn = FakeConn()
    mock_get_conn.return_value = conn

    collector = SystemMetricsCollector(db_path=':memory:')

    metrics = {
        'cpu': {"timestamp": dt.datetime.now(dt.timezone.utc), "metric_type": "cpu", "cpu_percent": 10},
        'memory': {"timestamp": dt.datetime.now(dt.timezone.utc), "metric_type": "memory", "memory_percent": 50},
        'disk': [
            {"timestamp": dt.datetime.now(dt.timezone.utc), "metric_type": "disk", "device": "/dev/sda1", "percent": 10}
        ],
        'network': [
            {"timestamp": dt.datetime.now(dt.timezone.utc), "metric_type": "network", "interface": "eth0", "bytes_sent": 1, "bytes_recv": 2}
        ],
        'services': [
            {"timestamp": dt.datetime.now(dt.timezone.utc), "metric_type": "service", "service_name": "sshd.service", "active_state": "active"}
        ],
        'uptime': {"timestamp": dt.datetime.now(dt.timezone.utc), "metric_type": "uptime", "uptime_seconds": 100}
    }

    total = collector.store_metrics(metrics)
    # Expect 1 + 1 + 1 + 1 + 1 + 1 = 6 rows stored
    assert total == 6
    # Ensure insert statements were attempted
    inserts = [sql for sql, _ in conn.executed if sql.lower().startswith('insert into system_metrics')]
    assert len(inserts) == 6


@patch('api.system_health.get_connection')
def test_get_alerts_and_check_alerts(mock_get_conn):
    # Prepare rows for alerts
    now = dt.datetime.now(dt.timezone.utc)
    rows = [
        (now, 'high_cpu', 'warning', 'High CPU usage', json.dumps({"cpu": 95}), 0),
        (now, 'service_down', 'critical', 'Critical service db is not running', json.dumps({"service": "db"}), 1),
    ]
    conn = FakeConn(rows=rows)
    mock_get_conn.return_value = conn

    monitor = SystemHealthMonitor(db_path=':memory:')

    # get_alerts should parse rows into dicts
    alerts = monitor.get_alerts(since_seconds=3600)
    assert len(alerts) == 2
    assert alerts[0]['alert_type'] == 'high_cpu'

    # check_alerts generates alerts based on thresholds
    metrics = {
        'cpu': {"cpu_percent": 95},
        'memory': {"memory_percent": 10},
        'disk': [{"mountpoint": "/", "percent": 91}],
        'services': []
    }
    generated = monitor.check_alerts(metrics)
    assert any(a['alert_type'] == 'high_cpu' for a in generated)
    assert any(a['alert_type'] == 'high_disk' for a in generated)


@patch('api.system_health.get_connection')
def test_get_metrics_variants_and_errors(mock_get_conn):
    now = dt.datetime.now(dt.timezone.utc)
    # Valid JSON row and invalid JSON row to trigger JSONDecodeError path
    rows = [
        (now, 'cpu', json.dumps({"cpu_percent": 10})),
        (now, 'memory', '{invalid json')
    ]
    conn = FakeConn(rows=rows)
    mock_get_conn.return_value = conn

    monitor = SystemHealthMonitor(db_path=':memory:')

    # metric_type filtered
    metrics = monitor.get_metrics(metric_type='cpu', since_seconds=10, limit=5)
    assert metrics and metrics[0]['metric_type'] == 'cpu'

    # no type filter includes valid entries and skips invalid JSON
    metrics_all = monitor.get_metrics(since_seconds=10, limit=5)
    assert any(m['metric_type'] == 'cpu' for m in metrics_all)

@patch('api.system_health.get_connection')
def test_get_metrics_no_type(mock_get_conn):
    now = dt.datetime.now(dt.timezone.utc)
    rows = [
        (now, 'disk', json.dumps({"percent": 50}))
    ]
    conn = FakeConn(rows=rows)
    mock_get_conn.return_value = conn
    monitor = SystemHealthMonitor(db_path=':memory:')
    res = monitor.get_metrics(since_seconds=10, limit=1)
    assert res[0]['metric_type'] == 'disk'


@patch('api.system_health.get_connection')
def test_store_alerts_and_ack_filter(mock_get_conn):
    conn = FakeConn(rows=[])
    mock_get_conn.return_value = conn

    monitor = SystemHealthMonitor(db_path=':memory:')
    alerts = [
        {"timestamp": dt.datetime.now(dt.timezone.utc), "alert_type": "high_cpu", "severity": "warning", "message": "m", "metric_data": {"x": 1}},
        {"timestamp": dt.datetime.now(dt.timezone.utc), "alert_type": "service_down", "severity": "critical", "message": "m2", "metric_data": {"y": 2}},
    ]
    monitor.store_alerts(alerts)
    # Ensure inserts happened
    insert_count = sum(1 for sql, _ in conn.executed if sql.lower().startswith('insert into system_alerts'))
    assert insert_count == 2

@patch('api.system_health.get_connection')
def test_get_alerts_filters_and_invalid_json(mock_get_conn):
    now = dt.datetime.now(dt.timezone.utc)
    # Row 1 matches severity warning and acknowledged False
    # Row 2 has invalid JSON to hit except branch
    rows = [
        (now, 'high_cpu', 'warning', 'msg', json.dumps({"a":1}), 0),
        (now, 'high_mem', 'warning', 'msg2', '{invalid', 0),
    ]
    conn = FakeConn(rows=rows)
    mock_get_conn.return_value = conn
    monitor = SystemHealthMonitor(db_path=':memory:')
    res = monitor.get_alerts(since_seconds=10, severity='warning', acknowledged=False)
    # Should parse only valid JSON one and skip invalid
    assert any(r['alert_type']=='high_cpu' for r in res)
