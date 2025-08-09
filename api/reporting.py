#!/usr/bin/env python3
import json
import datetime as dt
import subprocess
import os
import tempfile
from typing import Dict, List, Any, Optional
from pathlib import Path

from .db import get_connection
from .embeddings import AnomalyDetector
from .system_health import SystemHealthMonitor


class ReportGenerator:
    """Generate comprehensive system reports"""
    
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        self.anomaly_detector = AnomalyDetector(db_path)
        self.health_monitor = SystemHealthMonitor(db_path)
    
    def _get_log_summary(self, since_seconds: int = 86400) -> Dict[str, Any]:
        """Get summary statistics for logs"""
        conn = get_connection(self.db_path)
        try:
            since_ts = dt.datetime.utcnow() - dt.timedelta(seconds=since_seconds)
            
            cur = conn.cursor()
            
            # Total log count
            cur.execute("SELECT COUNT(*) FROM logs WHERE ts >= ?", [since_ts])
            total_logs = cur.fetchone()[0]
            
            # Logs by severity
            cur.execute("""
                SELECT severity, COUNT(*) 
                FROM logs 
                WHERE ts >= ? 
                GROUP BY severity 
                ORDER BY COUNT(*) DESC
            """, [since_ts])
            severity_counts = dict(cur.fetchall())
            
            # Top units by log volume
            cur.execute("""
                SELECT unit, COUNT(*) 
                FROM logs 
                WHERE ts >= ? 
                GROUP BY unit 
                ORDER BY COUNT(*) DESC 
                LIMIT 10
            """, [since_ts])
            top_units = dict(cur.fetchall())
            
            # Top sources
            cur.execute("""
                SELECT source, COUNT(*) 
                FROM logs 
                WHERE ts >= ? 
                GROUP BY source 
                ORDER BY COUNT(*) DESC 
                LIMIT 5
            """, [since_ts])
            top_sources = dict(cur.fetchall())
            
            # Error rate
            error_count = sum(severity_counts.get(sev, 0) for sev in ['err', 'crit', 'emerg'])
            error_rate = (error_count / total_logs * 100) if total_logs > 0 else 0
            
            return {
                "total_logs": total_logs,
                "severity_distribution": severity_counts,
                "top_units": top_units,
                "top_sources": top_sources,
                "error_count": error_count,
                "error_rate": round(error_rate, 2),
                "period_hours": since_seconds // 3600
            }
            
        finally:
            conn.close()
    
    def _get_system_health_summary(self, since_seconds: int = 86400) -> Dict[str, Any]:
        """Get system health summary"""
        try:
            # Get recent metrics
            metrics = self.health_monitor.get_metrics(
                since_seconds=since_seconds,
                limit=1000
            )
            
            if not metrics:
                return {"status": "no_data", "message": "No system metrics available"}
            
            # Calculate averages
            cpu_avg = sum(m.get('cpu_percent', 0) for m in metrics) / len(metrics)
            memory_avg = sum(m.get('memory_percent', 0) for m in metrics) / len(metrics)
            disk_avg = sum(m.get('disk_percent', 0) for m in metrics) / len(metrics)
            
            # Get current alerts
            alerts = self.health_monitor.get_alerts(
                since_seconds=since_seconds,
                acknowledged=False
            )
            
            return {
                "cpu_average": round(cpu_avg, 2),
                "memory_average": round(memory_avg, 2),
                "disk_average": round(disk_avg, 2),
                "active_alerts": len(alerts),
                "alert_summary": [
                    {
                        "severity": alert.get("severity"),
                        "message": alert.get("message"),
                        "timestamp": alert.get("timestamp")
                    }
                    for alert in alerts[:5]  # Top 5 alerts
                ]
            }
            
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def _get_anomaly_summary(self, since_seconds: int = 86400) -> Dict[str, Any]:
        """Get anomaly detection summary"""
        try:
            anomalies = self.anomaly_detector.detect_anomalies(since_seconds)
            
            # Group by type
            anomaly_types = {}
            for anomaly in anomalies:
                anomaly_type = anomaly.get("type", "unknown")
                if anomaly_type not in anomaly_types:
                    anomaly_types[anomaly_type] = []
                anomaly_types[anomaly_type].append(anomaly)
            
            return {
                "total_anomalies": len(anomalies),
                "anomaly_types": {
                    anomaly_type: len(anomalies_list)
                    for anomaly_type, anomalies_list in anomaly_types.items()
                },
                "high_severity": len([a for a in anomalies if a.get("severity") == "high"]),
                "recent_anomalies": [
                    {
                        "type": a.get("type"),
                        "severity": a.get("severity"),
                        "description": a.get("description")
                    }
                    for a in anomalies[:5]  # Top 5 anomalies
                ]
            }
            
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def generate_daily_report(self, since_seconds: int = 86400) -> Dict[str, Any]:
        """Generate a comprehensive daily report"""
        report_time = dt.datetime.utcnow()
        
        report = {
            "report_id": f"daily_{report_time.strftime('%Y%m%d')}",
            "generated_at": report_time.isoformat(),
            "period_hours": since_seconds // 3600,
            "summary": {
                "log_analytics": self._get_log_summary(since_seconds),
                "system_health": self._get_system_health_summary(since_seconds),
                "anomalies": self._get_anomaly_summary(since_seconds)
            }
        }
        
        # Add recommendations
        recommendations = []
        
        # Check error rate
        error_rate = report["summary"]["log_analytics"].get("error_rate", 0)
        if error_rate > 5:
            recommendations.append(f"High error rate detected: {error_rate}%. Review system logs for issues.")
        
        # Check system health
        health = report["summary"]["system_health"]
        if health.get("cpu_average", 0) > 80:
            recommendations.append("High CPU usage detected. Consider investigating resource-intensive processes.")
        
        if health.get("memory_average", 0) > 85:
            recommendations.append("High memory usage detected. Consider memory optimization or cleanup.")
        
        if health.get("active_alerts", 0) > 0:
            recommendations.append(f"{health['active_alerts']} active alerts require attention.")
        
        # Check anomalies
        anomalies = report["summary"]["anomalies"]
        if anomalies.get("high_severity", 0) > 0:
            recommendations.append(f"{anomalies['high_severity']} high-severity anomalies detected.")
        
        report["recommendations"] = recommendations
        
        return report
    
    def format_report_as_text(self, report: Dict[str, Any]) -> str:
        """Format report as human-readable text"""
        lines = []
        
        # Header
        lines.append("=" * 80)
        lines.append("CHIMERA LOGMIND DAILY REPORT")
        lines.append("=" * 80)
        lines.append(f"Generated: {report['generated_at']}")
        lines.append(f"Period: {report['period_hours']} hours")
        lines.append("")
        
        # Log Analytics
        log_data = report["summary"]["log_analytics"]
        lines.append("LOG ANALYTICS")
        lines.append("-" * 40)
        lines.append(f"Total logs: {log_data['total_logs']:,}")
        lines.append(f"Error rate: {log_data['error_rate']}%")
        lines.append("")
        
        lines.append("Severity Distribution:")
        for severity, count in log_data.get("severity_distribution", {}).items():
            lines.append(f"  {severity}: {count:,}")
        lines.append("")
        
        lines.append("Top Units by Volume:")
        for unit, count in list(log_data.get("top_units", {}).items())[:5]:
            lines.append(f"  {unit}: {count:,}")
        lines.append("")
        
        # System Health
        health_data = report["summary"]["system_health"]
        lines.append("SYSTEM HEALTH")
        lines.append("-" * 40)
        if health_data.get("status") == "no_data":
            lines.append("No system metrics available")
        else:
            lines.append(f"CPU Average: {health_data.get('cpu_average', 0)}%")
            lines.append(f"Memory Average: {health_data.get('memory_average', 0)}%")
            lines.append(f"Disk Average: {health_data.get('disk_average', 0)}%")
            lines.append(f"Active Alerts: {health_data.get('active_alerts', 0)}")
            
            if health_data.get("alert_summary"):
                lines.append("Recent Alerts:")
                for alert in health_data["alert_summary"]:
                    lines.append(f"  [{alert['severity']}] {alert['message']}")
        lines.append("")
        
        # Anomalies
        anomaly_data = report["summary"]["anomalies"]
        lines.append("ANOMALY DETECTION")
        lines.append("-" * 40)
        if anomaly_data.get("status") == "error":
            lines.append(f"Error: {anomaly_data['message']}")
        else:
            lines.append(f"Total Anomalies: {anomaly_data.get('total_anomalies', 0)}")
            lines.append(f"High Severity: {anomaly_data.get('high_severity', 0)}")
            
            if anomaly_data.get("recent_anomalies"):
                lines.append("Recent Anomalies:")
                for anomaly in anomaly_data["recent_anomalies"]:
                    lines.append(f"  [{anomaly['severity']}] {anomaly['description']}")
        lines.append("")
        
        # Recommendations
        if report.get("recommendations"):
            lines.append("RECOMMENDATIONS")
            lines.append("-" * 40)
            for i, rec in enumerate(report["recommendations"], 1):
                lines.append(f"{i}. {rec}")
            lines.append("")
        
        lines.append("=" * 80)
        lines.append("End of Report")
        lines.append("=" * 80)
        
        return "\n".join(lines)
    
    def format_report_as_html(self, report: Dict[str, Any]) -> str:
        """Format report as HTML"""
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Chimera LogMind Daily Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .header {{ background-color: #2c3e50; color: white; padding: 20px; text-align: center; }}
        .section {{ margin: 20px 0; padding: 15px; border: 1px solid #ddd; border-radius: 5px; }}
        .section h2 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 5px; }}
        .metric {{ display: inline-block; margin: 10px; padding: 10px; background-color: #f8f9fa; border-radius: 3px; }}
        .alert {{ background-color: #f8d7da; border: 1px solid #f5c6cb; padding: 10px; margin: 5px 0; border-radius: 3px; }}
        .recommendation {{ background-color: #d1ecf1; border: 1px solid #bee5eb; padding: 10px; margin: 5px 0; border-radius: 3px; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background-color: #f2f2f2; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Chimera LogMind Daily Report</h1>
        <p>Generated: {report['generated_at']} | Period: {report['period_hours']} hours</p>
    </div>
"""
        
        # Log Analytics
        log_data = report["summary"]["log_analytics"]
        html += f"""
    <div class="section">
        <h2>Log Analytics</h2>
        <div class="metric"><strong>Total Logs:</strong> {log_data['total_logs']:,}</div>
        <div class="metric"><strong>Error Rate:</strong> {log_data['error_rate']}%</div>
        
        <h3>Severity Distribution</h3>
        <table>
            <tr><th>Severity</th><th>Count</th></tr>
"""
        for severity, count in log_data.get("severity_distribution", {}).items():
            html += f"            <tr><td>{severity}</td><td>{count:,}</td></tr>\n"
        
        html += """
        </table>
    </div>
"""
        
        # System Health
        health_data = report["summary"]["system_health"]
        html += """
    <div class="section">
        <h2>System Health</h2>
"""
        if health_data.get("status") == "no_data":
            html += "        <p>No system metrics available</p>\n"
        else:
            html += f"""
        <div class="metric"><strong>CPU Average:</strong> {health_data.get('cpu_average', 0)}%</div>
        <div class="metric"><strong>Memory Average:</strong> {health_data.get('memory_average', 0)}%</div>
        <div class="metric"><strong>Disk Average:</strong> {health_data.get('disk_average', 0)}%</div>
        <div class="metric"><strong>Active Alerts:</strong> {health_data.get('active_alerts', 0)}</div>
"""
            if health_data.get("alert_summary"):
                html += "        <h3>Recent Alerts</h3>\n"
                for alert in health_data["alert_summary"]:
                    html += f'        <div class="alert"><strong>[{alert["severity"]}]</strong> {alert["message"]}</div>\n'
        
        html += "    </div>\n"
        
        # Anomalies
        anomaly_data = report["summary"]["anomalies"]
        html += """
    <div class="section">
        <h2>Anomaly Detection</h2>
"""
        if anomaly_data.get("status") == "error":
            html += f"        <p>Error: {anomaly_data['message']}</p>\n"
        else:
            html += f"""
        <div class="metric"><strong>Total Anomalies:</strong> {anomaly_data.get('total_anomalies', 0)}</div>
        <div class="metric"><strong>High Severity:</strong> {anomaly_data.get('high_severity', 0)}</div>
"""
            if anomaly_data.get("recent_anomalies"):
                html += "        <h3>Recent Anomalies</h3>\n"
                for anomaly in anomaly_data["recent_anomalies"]:
                    html += f'        <div class="alert"><strong>[{anomaly["severity"]}]</strong> {anomaly["description"]}</div>\n'
        
        html += "    </div>\n"
        
        # Recommendations
        if report.get("recommendations"):
            html += """
    <div class="section">
        <h2>Recommendations</h2>
"""
            for i, rec in enumerate(report["recommendations"], 1):
                html += f'        <div class="recommendation">{i}. {rec}</div>\n'
            html += "    </div>\n"
        
        html += """
</body>
</html>
"""
        return html


class ReportDelivery:
    """Handle report delivery via email"""
    
    def __init__(self, smtp_host: str = "localhost", smtp_port: int = 25):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
    
    def send_report_email(self, report_text: str, report_html: str, 
                         to_email: str, subject: str = "Chimera LogMind Daily Report") -> bool:
        """Send report via email using Exim4"""
        try:
            # Create temporary files for the email
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as txt_file:
                txt_file.write(report_text)
                txt_file_path = txt_file.name
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as html_file:
                html_file.write(report_html)
                html_file_path = html_file.name
            
            # Use Exim4 to send the email
            cmd = [
                "exim4",
                "-t",  # Read recipient from message headers
                "-f", "chimera@localhost",  # From address
                "-S", f"{self.smtp_host}:{self.smtp_port}"
            ]
            
            # Create email content
            email_content = f"""To: {to_email}
From: chimera@localhost
Subject: {subject}
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="boundary"

--boundary
Content-Type: text/plain; charset=utf-8

{report_text}

--boundary
Content-Type: text/html; charset=utf-8

{report_html}

--boundary--
"""
            
            # Send email
            result = subprocess.run(
                cmd,
                input=email_content.encode('utf-8'),
                capture_output=True,
                text=True
            )
            
            # Clean up temporary files
            os.unlink(txt_file_path)
            os.unlink(html_file_path)
            
            if result.returncode == 0:
                return True
            else:
                print(f"Email delivery failed: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"Error sending email: {e}")
            return False
    
    def save_report_to_file(self, report_text: str, report_html: str, 
                           output_dir: str = "/var/lib/chimera/reports") -> str:
        """Save report to files"""
        try:
            # Ensure output directory exists
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            
            # Generate filenames
            timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            txt_file = Path(output_dir) / f"report_{timestamp}.txt"
            html_file = Path(output_dir) / f"report_{timestamp}.html"
            
            # Write files
            with open(txt_file, 'w') as f:
                f.write(report_text)
            
            with open(html_file, 'w') as f:
                f.write(report_html)
            
            return str(txt_file)
            
        except Exception as e:
            print(f"Error saving report: {e}")
            return ""