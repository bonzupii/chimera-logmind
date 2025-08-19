import datetime as dt
from unittest.mock import patch, MagicMock

import pytest
import subprocess
from api.security_audit import SecurityAuditor


@patch('api.security_audit.Path')
def test_auditor_init_creates_dir(mock_path):
    auditor = SecurityAuditor(db_path=None)
    mock_path.assert_called()


@patch('api.security_audit.Path')
@patch('api.security_audit.SecurityAuditor._run_command')
def test_run_auditd_check_running(mock_run, mock_path):
    # systemctl is-active auditd -> active
    mock_run.side_effect = [
        (0, 'active\n', ''),  # systemctl
        (0, 'type=EXECVE msg=audit\n', ''),  # ausearch
    ]
    auditor = SecurityAuditor()
    result = auditor.run_auditd_check()

    assert result["tool"] == "auditd"
    assert result["status"] in ("running", "stopped", "error")
    # With stdout lines, we should have events_count
    assert "events_count" in result


@patch('api.security_audit.Path')
@patch('api.security_audit.os.path.exists', return_value=False)
def test_run_aide_check_not_configured(_mock_exists, mock_path):
    auditor = SecurityAuditor()
    result = auditor.run_aide_check()
    assert result["status"] == "not_configured"
    assert result["severity"] == "warning"


@patch('api.security_audit.Path')
@patch('api.security_audit.subprocess.run')
def test__run_command_success(mock_run, mock_path):
    mock_run.return_value = MagicMock(returncode=0, stdout='ok', stderr='')
    auditor = SecurityAuditor()
    rc, out, err = auditor._run_command(["echo", "hi"], timeout=1)
    assert rc == 0 and out == 'ok'


@patch('api.security_audit.Path')
@patch('api.security_audit.subprocess.run', side_effect=subprocess.TimeoutExpired(cmd=['sleep', '2'], timeout=1))
def test__run_command_timeout(_mock_run, mock_path):
    auditor = SecurityAuditor()
    rc, out, err = auditor._run_command(["sleep", "2"], timeout=1)
    assert rc == -1 and "timed out" in err


@patch('api.security_audit.Path')
@patch('api.security_audit.subprocess.run', side_effect=FileNotFoundError())
def test__run_command_not_found(_mock_run, mock_path):
    auditor = SecurityAuditor()
    rc, out, err = auditor._run_command(["nonexistent"], timeout=1)
    assert rc == -1 and "Command not found" in err


@patch('api.security_audit.Path')
@patch('api.security_audit.get_connection')
def test__store_audit_result_inserts(mock_get_conn, mock_path):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    # last_insert_rowid returns a tuple with id
    mock_conn.execute.side_effect = [None, None, MagicMock(fetchone=lambda: (123,))]
    mock_get_conn.return_value = mock_conn

    auditor = SecurityAuditor()
    new_id = auditor._store_audit_result("toolx", {"status": "ok", "summary": "done"})
    assert new_id == 123
    assert mock_conn.execute.call_count >= 3


@patch('api.security_audit.Path')
@patch('api.security_audit.SecurityAuditor._run_command')
@patch('api.security_audit.os.path.exists', return_value=True)
def test_run_aide_check_violations(_mock_exists, mock_run, mock_path):
    mock_run.return_value = (1, 'VIOLATION: /etc/passwd changed\n', '')
    auditor = SecurityAuditor()
    result = auditor.run_aide_check()
    assert result["status"] == "violations"
    assert result["severity"] == "critical"
    assert result.get("violation_count", 0) >= 1


@patch('api.security_audit.Path')
@patch('api.security_audit.SecurityAuditor._run_command')
def test_run_rkhunter_check_warning(mock_run, mock_path):
    mock_run.return_value = (1, 'warning: something', '')
    auditor = SecurityAuditor()
    result = auditor.run_rkhunter_check()
    assert result["status"] == "warnings"
    assert result["severity"] == "warning"
