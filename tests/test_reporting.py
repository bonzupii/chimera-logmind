import datetime as dt
from unittest.mock import patch, MagicMock

import pytest
from api.reporting import ReportGenerator


class FakeCursor:
    def __init__(self, sequences):
        # sequences is a list of return values for fetchone/fetchall in order of execute calls
        self.sequences = sequences
        self.exec_index = 0

    def execute(self, *_args, **_kwargs):
        return self

    def fetchone(self):
        val = self.sequences[self.exec_index]
        self.exec_index += 1
        return val

    def fetchall(self):
        val = self.sequences[self.exec_index]
        self.exec_index += 1
        return val


class FakeConn:
    def __init__(self, sequences):
        self._cursor = FakeCursor(sequences)

    def cursor(self):
        return self._cursor

    def close(self):
        pass


@patch('api.reporting.get_connection')
def test_get_log_summary_basic(mock_get_conn):
    # Prepare cursor sequences: fetchone total logs, fetchall severity, fetchall top_units, fetchall top_sources
    sequences = [
        (42,),  # total logs
        [("info", 30), ("err", 12)],  # severity counts
        [("unitA", 25), ("unitB", 10)],  # top units
        [("src1", 40), ("src2", 2)],  # top sources
    ]
    mock_get_conn.return_value = FakeConn(sequences)

    rg = ReportGenerator()
    summary = rg._get_log_summary(since_seconds=3600)

    assert summary["total_logs"] == 42
    assert summary["severity_distribution"]["err"] == 12
    assert summary["error_count"] == 12
    assert summary["error_rate"] == round(12 / 42 * 100, 2)
    assert summary["period_hours"] == 1


def test_get_system_health_summary_with_alerts():
    rg = ReportGenerator()
    # Patch the health_monitor methods directly
    rg.health_monitor = MagicMock()
    rg.health_monitor.get_metrics.return_value = [
        {"cpu_percent": 10, "memory_percent": 50, "disk_percent": 40},
        {"cpu_percent": 30, "memory_percent": 70, "disk_percent": 60},
    ]
    rg.health_monitor.get_alerts.return_value = [
        {"severity": "warning", "message": "High CPU", "timestamp": dt.datetime.now(dt.timezone.utc)},
        {"severity": "critical", "message": "Disk full", "timestamp": dt.datetime.now(dt.timezone.utc)},
    ]

    summary = rg._get_system_health_summary(since_seconds=600)

    assert summary["cpu_average"] == 20.0
    assert summary["memory_average"] == 60.0
    assert summary["disk_average"] == 50.0
    assert summary["active_alerts"] == 2
    assert len(summary["alert_summary"]) == 2


@patch('api.reporting.get_connection')
def test_get_anomaly_summary_detects_spikes(mock_get_conn):
    # First query (error spikes) returns rows, second (high volume) returns rows
    sequences = [
        # error spikes
        [("unit1", 12), ("unit2", 11)],
        # high volume
        [("source1", 1500)],
    ]
    mock_get_conn.return_value = FakeConn(sequences)

    rg = ReportGenerator()
    summary = rg._get_anomaly_summary(since_seconds=7200)

    assert summary["total_anomalies"] == 3
    assert summary["anomaly_types"]["error_spike"] == 2
    assert summary["anomaly_types"]["high_volume"] == 1
    assert summary["high_severity"] == 2
    assert summary["medium_severity"] == 1


@patch('api.reporting.get_connection')
def test_get_log_summary_zero_logs(mock_get_conn):
    # No logs in the window
    sequences = [
        (0,),  # total logs
        [],  # severity
        [],  # top units
        [],  # top sources
    ]
    mock_get_conn.return_value = FakeConn(sequences)

    rg = ReportGenerator()
    summary = rg._get_log_summary(since_seconds=60)

    assert summary["total_logs"] == 0
    assert summary["error_rate"] == 0
    assert summary["severity_distribution"] == {}
    assert summary["top_units"] == {}
    assert summary["top_sources"] == {}


@patch('api.reporting.get_connection')
def test_get_log_summary_db_error(mock_get_conn):
    mock_get_conn.side_effect = RuntimeError("db down")
    rg = ReportGenerator()
    with pytest.raises(RuntimeError):
        rg._get_log_summary()


def test_get_system_health_summary_error():
    rg = ReportGenerator()
    rg.health_monitor = MagicMock()
    rg.health_monitor.get_metrics.side_effect = Exception("metric error")

    summary = rg._get_system_health_summary()
    assert summary["status"] == "error"
    assert "metric error" in summary["message"]


@patch('api.reporting.get_connection')
def test_get_anomaly_summary_no_results(mock_get_conn):
    sequences = [
        [],  # error spikes
        [],  # high volume
    ]
    mock_get_conn.return_value = FakeConn(sequences)

    rg = ReportGenerator()
    summary = rg._get_anomaly_summary()
    assert summary["total_anomalies"] == 0
    assert summary["anomaly_types"] == {}
    assert summary["high_severity"] == 0
    assert summary["medium_severity"] == 0
