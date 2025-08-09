#!/usr/bin/env python3
import json
import datetime as dt
import subprocess
import os
import re
import time
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

from .db import get_connection


class SecurityAuditor:
    """Comprehensive security auditing with multiple tools"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        self.audit_results_dir = "/var/lib/chimera/audits"
        Path(self.audit_results_dir).mkdir(parents=True, exist_ok=True)

    def _run_command(self, cmd: List[str], timeout: int = 300) -> Tuple[int, str, str]:
        """Run a command and return (return_code, stdout, stderr)"""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"Command timed out after {timeout} seconds"
        except FileNotFoundError:
            return -1, "", f"Command not found: {cmd[0]}"
        except Exception as e:
            return -1, "", str(e)

    def _store_audit_result(self, tool: str, result: Dict[str, Any]) -> Optional[int]:
        """Store audit result in database"""
        conn = get_connection(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS security_audits (
                    id BIGINT PRIMARY KEY,
                    tool TEXT NOT NULL,
                    scan_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT NOT NULL,
                    result_data TEXT,
                    summary TEXT,
                    severity TEXT DEFAULT 'info'
                )
            """)

            conn.execute("""
                INSERT INTO security_audits (tool, status, result_data, summary, severity)
                VALUES (?, ?, ?, ?, ?)
            """, [
                tool,
                result.get("status", "unknown"),
                json.dumps(result),
                result.get("summary", ""),
                result.get("severity", "info")
            ])

            last_row_id_result = conn.execute("SELECT last_insert_rowid()").fetchone()
            return last_row_id_result[0] if last_row_id_result else None

        finally:
            conn.close()

    def run_auditd_check(self) -> Dict[str, Any]:
        """Check auditd status and recent events"""
        result: Dict[str, Any] = {
            "tool": "auditd",
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "status": "unknown",
            "summary": "",
            "severity": "info"
        }

        try:
            # Check if auditd is running
            rc, stdout, stderr = self._run_command(["systemctl", "is-active", "auditd"])
            if rc == 0 and stdout.strip() == "active":
                result["status"] = "running"
                result["summary"] = "auditd service is active"
            else:
                result["status"] = "stopped"
                result["severity"] = "warning"
                result["summary"] = "auditd service is not running"

            # Get recent audit events
            rc, stdout, stderr = self._run_command(["ausearch", "-ts", "today"], timeout=60)
            if rc == 0:
                events = []
                for line in stdout.split('\n'):
                    if line.strip():
                        events.append(line.strip())

                result["events_count"] = len(events)
                result["recent_events"] = events[:10]  # Last 10 events

                # Look for suspicious events
                suspicious_patterns = [
                    r"type=EXECVE.*suid=",
                    r"type=SYSCALL.*syscall=2",  # open
                    r"type=SYSCALL.*syscall=59",  # execve
                    r"type=LOGIN.*res=failed"
                ]

                suspicious_count = 0
                for event in events:
                    for pattern in suspicious_patterns:
                        if re.search(pattern, event):
                            suspicious_count += 1
                            break

                if suspicious_count > 0:
                    result["severity"] = "warning"
                    result["summary"] += f" - {suspicious_count} suspicious events detected"

        except Exception as e:
            result["status"] = "error"
            result["summary"] = f"Error checking auditd: {e}"
            result["severity"] = "error"

        return result

    def run_aide_check(self) -> Dict[str, Any]:
        """Run AIDE (Advanced Intrusion Detection Environment) check"""
        result: Dict[str, Any] = {
            "tool": "aide",
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "status": "unknown",
            "summary": "",
            "severity": "info"
        }

        try:
            # Check if AIDE database exists
            aide_db = "/var/lib/aide/aide.db"
            if not os.path.exists(aide_db):
                result["status"] = "not_configured"
                result["summary"] = "AIDE database not found"
                result["severity"] = "warning"
                return result

            # Run AIDE check
            rc, stdout, stderr = self._run_command(["aide", "--check"], timeout=600)

            if rc == 0:
                result["status"] = "clean"
                result["summary"] = "No integrity violations detected"
            elif rc == 1:
                result["status"] = "violations"
                result["severity"] = "critical"
                result["summary"] = "File integrity violations detected"

                # Parse violations
                violations = []
                for line in stdout.split('\n'):
                    if line.strip() and not line.startswith('AIDE'):
                        violations.append(line.strip())

                result["violations"] = violations
                result["violation_count"] = len(violations)
            else:
                result["status"] = "error"
                result["summary"] = f"AIDE check failed: {stderr}"
                result["severity"] = "error"

        except Exception as e:
            result["status"] = "error"
            result["summary"] = f"Error running AIDE: {e}"
            result["severity"] = "error"

        return result

    def run_rkhunter_check(self) -> Dict[str, Any]:
        """Run rkhunter (Rootkit Hunter) check"""
        result: Dict[str, Any] = {
            "tool": "rkhunter",
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "status": "unknown",
            "summary": "",
            "severity": "info"
        }

        try:
            # Run rkhunter check
            rc, stdout, stderr = self._run_command(["rkhunter", "--check", "--skip-keypress"], timeout=300)

            if rc == 0:
                result["status"] = "clean"
                result["summary"] = "No rootkits detected"
            elif rc == 1:
                result["status"] = "warnings"
                result["severity"] = "warning"
                result["summary"] = "Some warnings found"
            elif rc == 2:
                result["status"] = "suspicious"
                result["severity"] = "critical"
                result["summary"] = "Suspicious files detected"
            else:
                result["status"] = "error"
                result["summary"] = f"rkhunter check failed: {stderr}"
                result["severity"] = "error"

            # Parse output for details
            warnings = []
            for line in stdout.split('\n'):
                if 'Warning:' in line or 'Suspicious:' in line:
                    warnings.append(line.strip())

            if warnings:
                result["warnings"] = warnings[:10]  # Top 10 warnings

        except Exception as e:
            result["status"] = "error"
            result["summary"] = f"Error running rkhunter: {e}"
            result["severity"] = "error"

        return result

    def run_chkrootkit_check(self) -> Dict[str, Any]:
        """Run chkrootkit check"""
        result: Dict[str, Any] = {
            "tool": "chkrootkit",
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "status": "unknown",
            "summary": "",
            "severity": "info"
        }

        try:
            # Run chkrootkit
            rc, stdout, stderr = self._run_command(["chkrootkit"], timeout=300)

            if rc == 0:
                result["status"] = "clean"
                result["summary"] = "No rootkits detected"
            elif rc == 1:
                result["status"] = "suspicious"
                result["severity"] = "critical"
                result["summary"] = "Suspicious files detected"
            else:
                result["status"] = "error"
                result["summary"] = f"chkrootkit check failed: {stderr}"
                result["severity"] = "error"

            # Parse output for suspicious files
            suspicious = []
            for line in stdout.split('\n'):
                if 'INFECTED' in line or 'Warning:' in line:
                    suspicious.append(line.strip())

            if suspicious:
                result["suspicious_files"] = suspicious[:10]  # Top 10 suspicious

        except Exception as e:
            result["status"] = "error"
            result["summary"] = f"Error running chkrootkit: {e}"
            result["severity"] = "error"

        return result

    def run_clamav_check(self) -> Dict[str, Any]:
        """Run ClamAV virus scan"""
        result: Dict[str, Any] = {
            "tool": "clamav",
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "status": "unknown",
            "summary": "",
            "severity": "info"
        }

        try:
            # Check if ClamAV is installed
            rc, stdout, stderr = self._run_command(["clamscan", "--version"])
            if rc != 0:
                result["status"] = "not_installed"
                result["summary"] = "ClamAV not installed"
                result["severity"] = "warning"
                return result

            # Run quick scan on common directories
            scan_dirs = ["/tmp", "/var/tmp", "/home"]
            infected_files = []
            scanned_files = 0

            for scan_dir in scan_dirs:
                if os.path.exists(scan_dir):
                    rc, stdout, stderr = self._run_command(
                        ["clamscan", "-r", "--infected", "--suppress-ok-results", scan_dir],
                        timeout=600
                    )

                    # Parse results
                    for line in stdout.split('\n'):
                        if line.strip():
                            if 'Infected files:' in line:
                                match = re.search(r'Infected files: (\d+)', line)
                                if match and int(match.group(1)) > 0:
                                    infected_files.append(f"{scan_dir}: {match.group(1)} infected")
                            elif 'Scanned files:' in line:
                                match = re.search(r'Scanned files: (\d+)', line)
                                if match:
                                    scanned_files += int(match.group(1))

            if infected_files:
                result["status"] = "infected"
                result["severity"] = "critical"
                result["summary"] = f"Virus detected in {len(infected_files)} locations"
                result["infected_locations"] = infected_files
            else:
                result["status"] = "clean"
                result["summary"] = f"No viruses detected in {scanned_files} scanned files"

            result["scanned_files"] = scanned_files

        except Exception as e:
            result["status"] = "error"
            result["summary"] = f"Error running ClamAV: {e}"
            result["severity"] = "error"

        return result

    def run_openscap_check(self) -> Dict[str, Any]:
        """Run OpenSCAP security compliance check"""
        result: Dict[str, Any] = {
            "tool": "openscap",
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "status": "unknown",
            "summary": "",
            "severity": "info"
        }

        try:
            # Check if oscap is available
            rc, stdout, stderr = self._run_command(["oscap", "--version"])
            if rc != 0:
                result["status"] = "not_installed"
                result["summary"] = "OpenSCAP not installed"
                result["severity"] = "warning"
                return result

            # Run basic system scan
            output_file = f"{self.audit_results_dir}/openscap_scan_{int(time.time())}.xml"
            rc, stdout, stderr = self._run_command([
                "oscap", "xccdf", "eval", "--results", output_file,
                "--profile", "xccdf_org.ssgproject.content_profile_standard",
                "/usr/share/xml/scap/ssg/content/ssg-debian11-xccdf.xml"
            ], timeout=900)

            if rc == 0:
                result["status"] = "completed"
                result["summary"] = "OpenSCAP scan completed"
                result["output_file"] = output_file

                # Parse results for summary
                if os.path.exists(output_file):
                    with open(output_file, 'r') as f:
                        content = f.read()

                    # Count findings
                    pass_count = content.count('result="pass"')
                    fail_count = content.count('result="fail"')
                    error_count = content.count('result="error"')

                    result["findings"] = {
                        "pass": pass_count,
                        "fail": fail_count,
                        "error": error_count
                    }

                    if fail_count > 0:
                        result["severity"] = "warning"
                        result["summary"] += f" - {fail_count} compliance failures"
            else:
                result["status"] = "error"
                result["summary"] = f"OpenSCAP scan failed: {stderr}"
                result["severity"] = "error"

        except Exception as e:
            result["status"] = "error"
            result["summary"] = f"Error running OpenSCAP: {e}"
            result["severity"] = "error"

        return result

    def run_lynis_check(self) -> Dict[str, Any]:
        """Run Lynis security audit"""
        result: Dict[str, Any] = {
            "tool": "lynis",
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "status": "unknown",
            "summary": "",
            "severity": "info"
        }

        try:
            # Check if Lynis is available
            rc, stdout, stderr = self._run_command(["lynis", "--version"])
            if rc != 0:
                result["status"] = "not_installed"
                result["summary"] = "Lynis not installed"
                result["severity"] = "warning"
                return result

            # Run Lynis audit
            rc, stdout, stderr = self._run_command(["lynis", "audit", "system", "--quick"], timeout=600)

            if rc == 0:
                result["status"] = "completed"
                result["summary"] = "Lynis audit completed"

                # Parse output for warnings and suggestions
                warnings = []
                suggestions = []

                for line in stdout.split('\n'):
                    if '[WARN]' in line:
                        warnings.append(line.strip())
                    elif '[SUGGESTION]' in line:
                        suggestions.append(line.strip())

                result["warnings"] = warnings[:10]  # Top 10 warnings
                result["suggestions"] = suggestions[:10]  # Top 10 suggestions

                if warnings:
                    result["severity"] = "warning"
                    result["summary"] += f" - {len(warnings)} warnings found"
            else:
                result["status"] = "error"
                result["summary"] = f"Lynis audit failed: {stderr}"
                result["severity"] = "error"

        except Exception as e:
            result["status"] = "error"
            result["summary"] = f"Error running Lynis: {e}"
            result["severity"] = "error"

        return result

    def run_full_audit(self) -> Dict[str, Any]:
        """Run all security audits"""
        audit_results: Dict[str, Any] = {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "audits": {},
            "summary": {
                "total_audits": 0,
                "passed": 0,
                "warnings": 0,
                "critical": 0,
                "errors": 0
            }
        }

        # Run all audits
        audit_functions = [
            ("auditd", self.run_auditd_check),
            ("aide", self.run_aide_check),
            ("rkhunter", self.run_rkhunter_check),
            ("chkrootkit", self.run_chkrootkit_check),
            ("clamav", self.run_clamav_check),
            ("openscap", self.run_openscap_check),
            ("lynis", self.run_lynis_check)
        ]

        for tool_name, audit_func in audit_functions:
            try:
                result = audit_func()
                audit_results["audits"][tool_name] = result

                # Store in database
                self._store_audit_result(tool_name, result)

                # Update summary
                audit_results["summary"]["total_audits"] += 1
                severity = result.get("severity", "info")

                if severity == "info":
                    audit_results["summary"]["passed"] += 1
                elif severity == "warning":
                    audit_results["summary"]["warnings"] += 1
                elif severity == "critical":
                    audit_results["summary"]["critical"] += 1
                elif severity == "error":
                    audit_results["summary"]["errors"] += 1

            except Exception as e:
                error_result = {
                    "tool": tool_name,
                    "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "status": "error",
                    "summary": f"Exception during audit: {e}",
                    "severity": "error"
                }
                audit_results["audits"][tool_name] = error_result
                audit_results["summary"]["errors"] += 1

        return audit_results

    def get_audit_history(self, tool: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Get audit history from database"""
        conn = get_connection(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS security_audits (
                    id BIGINT PRIMARY KEY,
                    tool TEXT NOT NULL,
                    scan_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT NOT NULL,
                    result_data TEXT,
                    summary TEXT,
                    severity TEXT DEFAULT 'info'
                )
            """)

            if tool:
                cur = conn.execute("""
                    SELECT id, tool, scan_time, status, summary, severity
                    FROM security_audits
                    WHERE tool = ?
                    ORDER BY scan_time DESC
                    LIMIT ?
                """, [tool, limit])
            else:
                cur = conn.execute("""
                    SELECT id, tool, scan_time, status, summary, severity
                    FROM security_audits
                    ORDER BY scan_time DESC
                    LIMIT ?
                """, [limit])

            results = []
            for row in cur.fetchall():
                results.append({
                    "id": row[0],
                    "tool": row[1],
                    "scan_time": row[2],
                    "status": row[3],
                    "summary": row[4],
                    "severity": row[5]
                })

            return results

        finally:
            conn.close()

    def get_audit_details(self, audit_id: int) -> Optional[Dict[str, Any]]:
        """Get detailed audit result by ID"""
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute("""
                SELECT tool, scan_time, status, result_data, summary, severity
                FROM security_audits
                WHERE id = ?
            """, [audit_id])

            row = cur.fetchone()
            if row:
                return {
                    "tool": row[0],
                    "scan_time": row[1],
                    "status": row[2],
                    "result_data": json.loads(row[3]) if row[3] else None,
                    "summary": row[4],
                    "severity": row[5]
                }

            return None

        finally:
            conn.close()
