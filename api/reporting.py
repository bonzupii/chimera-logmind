#!/usr/bin/env python3
"""
Reporting Module for Chimera LogMind

Generates daily reports combining DuckDB analytics and semantic results,
with support for local mail delivery via Exim4.
"""

import json
import logging
import os
import subprocess
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List, Optional, Any
import duckdb

try:
    from .db import get_connection
    from .embeddings import SemanticSearchEngine, AnomalyDetector
    from .system_health import SystemHealthMonitor
    from .rag_chat import RAGChatEngine
    from .config import ChimeraConfig
except ImportError:
    from db import get_connection
    from embeddings import SemanticSearchEngine, AnomalyDetector
    from system_health import SystemHealthMonitor
    from rag_chat import RAGChatEngine
    from config import ChimeraConfig

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generates comprehensive system reports"""
    
    def __init__(self, db_path: str, config: ChimeraConfig):
        self.db_path = db_path
        self.config = config
        self.search_engine = SemanticSearchEngine(db_path, config)
        self.anomaly_detector = AnomalyDetector(db_path, config)
        self.health_monitor = SystemHealthMonitor(db_path)
        self.chat_engine = RAGChatEngine(db_path, config)
    
    def _get_log_summary(self, since_seconds: int = 86400) -> Dict[str, Any]:
        """Get log summary statistics"""
        try:
            db_conn = get_connection(self.db_path)
            
            # Total log count
            total_query = """
            SELECT COUNT(*) as total
            FROM logs 
            WHERE timestamp >= datetime('now', '-{} seconds')
            """.format(since_seconds)
            
            total_result = db_conn.execute(total_query).fetchone()
            total_logs = total_result[0] if total_result else 0
            
            # Logs by severity
            severity_query = """
            SELECT severity, COUNT(*) as count
            FROM logs 
            WHERE timestamp >= datetime('now', '-{} seconds')
            GROUP BY severity
            ORDER BY count DESC
            """.format(since_seconds)
            
            severity_stats = {}
            for row in db_conn.execute(severity_query).fetchall():
                severity_stats[row[0]] = row[1]
            
            # Logs by source
            source_query = """
            SELECT source, COUNT(*) as count
            FROM logs 
            WHERE timestamp >= datetime('now', '-{} seconds')
            GROUP BY source
            ORDER BY count DESC
            """.format(since_seconds)
            
            source_stats = {}
            for row in db_conn.execute(source_query).fetchall():
                source_stats[row[0]] = row[1]
            
            # Top units/services
            unit_query = """
            SELECT unit, COUNT(*) as count
            FROM logs 
            WHERE timestamp >= datetime('now', '-{} seconds')
            AND unit IS NOT NULL AND unit != ''
            GROUP BY unit
            ORDER BY count DESC
            LIMIT 10
            """.format(since_seconds)
            
            top_units = []
            for row in db_conn.execute(unit_query).fetchall():
                top_units.append({'unit': row[0], 'count': row[1]})
            
            # Recent errors
            errors_query = """
            SELECT timestamp, unit, message
            FROM logs 
            WHERE timestamp >= datetime('now', '-{} seconds')
            AND severity IN ('err', 'crit', 'alert', 'emerg')
            ORDER BY timestamp DESC
            LIMIT 20
            """.format(since_seconds)
            
            recent_errors = []
            for row in db_conn.execute(errors_query).fetchall():
                recent_errors.append({
                    'timestamp': row[0],
                    'unit': row[1],
                    'message': row[2][:200] + '...' if len(row[2]) > 200 else row[2]
                })
            
            db_conn.close()
            
            return {
                'total_logs': total_logs,
                'severity_stats': severity_stats,
                'source_stats': source_stats,
                'top_units': top_units,
                'recent_errors': recent_errors
            }
            
        except Exception as e:
            logger.error(f"Error getting log summary: {e}")
            return {}
    
    def _get_system_health_summary(self, since_seconds: int = 86400) -> Dict[str, Any]:
        """Get system health summary"""
        try:
            # Get recent metrics
            metrics = self.health_monitor.get_metrics(
                since_seconds=since_seconds,
                limit=1000
            )
            
            # Get recent alerts
            alerts = self.health_monitor.get_alerts(
                since_seconds=since_seconds,
                acknowledged=False
            )
            
            # Calculate averages by metric type
            metric_averages = {}
            for metric in metrics:
                metric_type = metric.get('metric_type', 'unknown')
                if metric_type not in metric_averages:
                    metric_averages[metric_type] = {'sum': 0, 'count': 0}
                metric_averages[metric_type]['sum'] += metric.get('value', 0)
                metric_averages[metric_type]['count'] += 1
            
            # Calculate averages
            for metric_type in metric_averages:
                count = metric_averages[metric_type]['count']
                if count > 0:
                    metric_averages[metric_type]['average'] = metric_averages[metric_type]['sum'] / count
                else:
                    metric_averages[metric_type]['average'] = 0
            
            return {
                'metric_averages': metric_averages,
                'alerts_count': len(alerts),
                'recent_alerts': alerts[:10]  # Top 10 alerts
            }
            
        except Exception as e:
            logger.error(f"Error getting system health summary: {e}")
            return {}
    
    def _get_anomaly_summary(self, since_seconds: int = 86400) -> Dict[str, Any]:
        """Get anomaly detection summary"""
        try:
            anomalies = self.anomaly_detector.detect_anomalies(since_seconds=since_seconds)
            
            # Group anomalies by type
            anomaly_types = {}
            for anomaly in anomalies:
                anomaly_type = anomaly.get('type', 'unknown')
                if anomaly_type not in anomaly_types:
                    anomaly_types[anomaly_type] = []
                anomaly_types[anomaly_type].append(anomaly)
            
            return {
                'total_anomalies': len(anomalies),
                'anomaly_types': anomaly_types,
                'recent_anomalies': anomalies[:10]  # Top 10 anomalies
            }
            
        except Exception as e:
            logger.error(f"Error getting anomaly summary: {e}")
            return {}
    
    def _get_semantic_insights(self, since_seconds: int = 86400) -> Dict[str, Any]:
        """Get semantic search insights"""
        try:
            # Generate some automated insights using RAG chat
            insights = []
            
            # Common analysis questions
            analysis_questions = [
                "What are the most common error patterns in the recent logs?",
                "Are there any performance issues or resource constraints?",
                "What services are generating the most logs?",
                "Are there any security-related events or suspicious activity?"
            ]
            
            for question in analysis_questions:
                try:
                    response = self.chat_engine.chat(question)
                    insights.append({
                        'question': question,
                        'response': response.get('response', 'No response available'),
                        'relevant_logs_count': response.get('relevant_logs_count', 0)
                    })
                except Exception as e:
                    logger.warning(f"Failed to generate insight for '{question}': {e}")
                    insights.append({
                        'question': question,
                        'response': f"Analysis failed: {str(e)}",
                        'relevant_logs_count': 0
                    })
            
            return {
                'insights': insights
            }
            
        except Exception as e:
            logger.error(f"Error getting semantic insights: {e}")
            return {}
    
    def generate_daily_report(self, report_date: Optional[datetime] = None) -> Dict[str, Any]:
        """Generate a comprehensive daily report"""
        if report_date is None:
            report_date = datetime.now()
        
        # Get data for the last 24 hours
        since_seconds = 86400
        
        report = {
            'report_date': report_date.isoformat(),
            'generated_at': datetime.now().isoformat(),
            'period_seconds': since_seconds,
            'log_summary': self._get_log_summary(since_seconds),
            'system_health': self._get_system_health_summary(since_seconds),
            'anomalies': self._get_anomaly_summary(since_seconds),
            'semantic_insights': self._get_semantic_insights(since_seconds)
        }
        
        return report
    
    def format_report_as_text(self, report: Dict[str, Any]) -> str:
        """Format report as plain text"""
        lines = []
        
        # Header
        lines.append("=" * 80)
        lines.append("CHIMERA LOGMIND - DAILY SYSTEM REPORT")
        lines.append("=" * 80)
        lines.append(f"Report Date: {report.get('report_date', 'Unknown')}")
        lines.append(f"Generated: {report.get('generated_at', 'Unknown')}")
        lines.append(f"Period: {report.get('period_seconds', 0) // 3600} hours")
        lines.append("")
        
        # Log Summary
        log_summary = report.get('log_summary', {})
        lines.append("LOG SUMMARY")
        lines.append("-" * 40)
        lines.append(f"Total Logs: {log_summary.get('total_logs', 0):,}")
        lines.append("")
        
        # Severity breakdown
        severity_stats = log_summary.get('severity_stats', {})
        if severity_stats:
            lines.append("Logs by Severity:")
            for severity, count in severity_stats.items():
                lines.append(f"  {severity.upper()}: {count:,}")
            lines.append("")
        
        # Source breakdown
        source_stats = log_summary.get('source_stats', {})
        if source_stats:
            lines.append("Logs by Source:")
            for source, count in source_stats.items():
                lines.append(f"  {source}: {count:,}")
            lines.append("")
        
        # Top units
        top_units = log_summary.get('top_units', [])
        if top_units:
            lines.append("Top Logging Units:")
            for unit_info in top_units[:5]:
                lines.append(f"  {unit_info['unit']}: {unit_info['count']:,}")
            lines.append("")
        
        # Recent errors
        recent_errors = log_summary.get('recent_errors', [])
        if recent_errors:
            lines.append("Recent Critical Errors:")
            for error in recent_errors[:5]:
                lines.append(f"  [{error['timestamp']}] {error['unit']}: {error['message']}")
            lines.append("")
        
        # System Health
        system_health = report.get('system_health', {})
        lines.append("SYSTEM HEALTH")
        lines.append("-" * 40)
        
        metric_averages = system_health.get('metric_averages', {})
        if metric_averages:
            lines.append("System Metrics (Averages):")
            for metric_type, data in metric_averages.items():
                avg = data.get('average', 0)
                lines.append(f"  {metric_type}: {avg:.2f}")
            lines.append("")
        
        alerts_count = system_health.get('alerts_count', 0)
        lines.append(f"Active Alerts: {alerts_count}")
        
        recent_alerts = system_health.get('recent_alerts', [])
        if recent_alerts:
            lines.append("Recent Alerts:")
            for alert in recent_alerts[:5]:
                lines.append(f"  [{alert.get('severity', 'unknown')}] {alert.get('message', '')}")
            lines.append("")
        
        # Anomalies
        anomalies = report.get('anomalies', {})
        lines.append("ANOMALY DETECTION")
        lines.append("-" * 40)
        total_anomalies = anomalies.get('total_anomalies', 0)
        lines.append(f"Total Anomalies Detected: {total_anomalies}")
        
        anomaly_types = anomalies.get('anomaly_types', {})
        if anomaly_types:
            lines.append("Anomalies by Type:")
            for anomaly_type, type_anomalies in anomaly_types.items():
                lines.append(f"  {anomaly_type}: {len(type_anomalies)}")
            lines.append("")
        
        recent_anomalies = anomalies.get('recent_anomalies', [])
        if recent_anomalies:
            lines.append("Recent Anomalies:")
            for anomaly in recent_anomalies[:5]:
                lines.append(f"  [{anomaly.get('type', 'unknown')}] {anomaly.get('description', '')}")
            lines.append("")
        
        # Semantic Insights
        semantic_insights = report.get('semantic_insights', {})
        lines.append("SEMANTIC INSIGHTS")
        lines.append("-" * 40)
        
        insights = semantic_insights.get('insights', [])
        for insight in insights:
            lines.append(f"Q: {insight.get('question', '')}")
            lines.append(f"A: {insight.get('response', '')}")
            lines.append("")
        
        # Footer
        lines.append("=" * 80)
        lines.append("Report generated by Chimera LogMind")
        lines.append("=" * 80)
        
        return "\n".join(lines)
    
    def format_report_as_html(self, report: Dict[str, Any]) -> str:
        """Format report as HTML"""
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Chimera LogMind - Daily Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .header {{ background-color: #f0f0f0; padding: 20px; border-radius: 5px; }}
        .section {{ margin: 20px 0; padding: 15px; border: 1px solid #ddd; border-radius: 5px; }}
        .section h2 {{ color: #333; border-bottom: 2px solid #007cba; padding-bottom: 5px; }}
        .metric {{ margin: 10px 0; }}
        .error {{ color: #d32f2f; }}
        .warning {{ color: #f57c00; }}
        .success {{ color: #388e3c; }}
        .stats {{ display: flex; flex-wrap: wrap; gap: 20px; }}
        .stat-box {{ background-color: #f9f9f9; padding: 10px; border-radius: 3px; min-width: 150px; }}
        table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Chimera LogMind - Daily System Report</h1>
        <p><strong>Report Date:</strong> {report.get('report_date', 'Unknown')}</p>
        <p><strong>Generated:</strong> {report.get('generated_at', 'Unknown')}</p>
        <p><strong>Period:</strong> {report.get('period_seconds', 0) // 3600} hours</p>
    </div>
"""
        
        # Log Summary Section
        log_summary = report.get('log_summary', {})
        html += f"""
    <div class="section">
        <h2>Log Summary</h2>
        <div class="stats">
            <div class="stat-box">
                <strong>Total Logs:</strong><br>
                {log_summary.get('total_logs', 0):,}
            </div>
        </div>
"""
        
        # Severity breakdown
        severity_stats = log_summary.get('severity_stats', {})
        if severity_stats:
            html += "<h3>Logs by Severity</h3><table><tr><th>Severity</th><th>Count</th></tr>"
            for severity, count in severity_stats.items():
                html += f"<tr><td>{severity.upper()}</td><td>{count:,}</td></tr>"
            html += "</table>"
        
        # Recent errors
        recent_errors = log_summary.get('recent_errors', [])
        if recent_errors:
            html += "<h3>Recent Critical Errors</h3><table><tr><th>Time</th><th>Unit</th><th>Message</th></tr>"
            for error in recent_errors[:10]:
                html += f"<tr class='error'><td>{error['timestamp']}</td><td>{error['unit']}</td><td>{error['message']}</td></tr>"
            html += "</table>"
        
        html += "</div>"
        
        # System Health Section
        system_health = report.get('system_health', {})
        html += f"""
    <div class="section">
        <h2>System Health</h2>
        <p><strong>Active Alerts:</strong> {system_health.get('alerts_count', 0)}</p>
"""
        
        metric_averages = system_health.get('metric_averages', {})
        if metric_averages:
            html += "<h3>System Metrics (Averages)</h3><table><tr><th>Metric</th><th>Average</th></tr>"
            for metric_type, data in metric_averages.items():
                avg = data.get('average', 0)
                html += f"<tr><td>{metric_type}</td><td>{avg:.2f}</td></tr>"
            html += "</table>"
        
        html += "</div>"
        
        # Anomalies Section
        anomalies = report.get('anomalies', {})
        html += f"""
    <div class="section">
        <h2>Anomaly Detection</h2>
        <p><strong>Total Anomalies:</strong> {anomalies.get('total_anomalies', 0)}</p>
"""
        
        anomaly_types = anomalies.get('anomaly_types', {})
        if anomaly_types:
            html += "<h3>Anomalies by Type</h3><table><tr><th>Type</th><th>Count</th></tr>"
            for anomaly_type, type_anomalies in anomaly_types.items():
                html += f"<tr><td>{anomaly_type}</td><td>{len(type_anomalies)}</td></tr>"
            html += "</table>"
        
        html += "</div>"
        
        # Semantic Insights Section
        semantic_insights = report.get('semantic_insights', {})
        insights = semantic_insights.get('insights', [])
        if insights:
            html += """
    <div class="section">
        <h2>Semantic Insights</h2>
"""
            for insight in insights:
                html += f"""
        <div class="metric">
            <h4>Q: {insight.get('question', '')}</h4>
            <p>A: {insight.get('response', '')}</p>
        </div>
"""
            html += "</div>"
        
        html += """
</body>
</html>
"""
        return html
    
    def save_report(self, report: Dict[str, Any], output_dir: str = "/var/lib/chimera/reports") -> str:
        """Save report to file"""
        try:
            os.makedirs(output_dir, exist_ok=True)
            
            report_date = datetime.fromisoformat(report['report_date'].replace('Z', '+00:00'))
            date_str = report_date.strftime("%Y-%m-%d")
            
            # Save JSON version
            json_path = os.path.join(output_dir, f"report-{date_str}.json")
            with open(json_path, 'w') as f:
                json.dump(report, f, indent=2)
            
            # Save text version
            text_content = self.format_report_as_text(report)
            text_path = os.path.join(output_dir, f"report-{date_str}.txt")
            with open(text_path, 'w') as f:
                f.write(text_content)
            
            # Save HTML version
            html_content = self.format_report_as_html(report)
            html_path = os.path.join(output_dir, f"report-{date_str}.html")
            with open(html_path, 'w') as f:
                f.write(html_content)
            
            logger.info(f"Report saved to {output_dir}")
            return json_path
            
        except Exception as e:
            logger.error(f"Error saving report: {e}")
            raise
    
    def send_report_via_email(self, report: Dict[str, Any], recipient: str, 
                            subject: Optional[str] = None) -> bool:
        """Send report via email using local mail system"""
        try:
            if subject is None:
                report_date = datetime.fromisoformat(report['report_date'].replace('Z', '+00:00'))
                subject = f"Chimera LogMind Daily Report - {report_date.strftime('%Y-%m-%d')}"
            
            # Create email message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = 'chimera@localhost'
            msg['To'] = recipient
            
            # Add text version
            text_content = self.format_report_as_text(report)
            text_part = MIMEText(text_content, 'plain')
            msg.attach(text_part)
            
            # Add HTML version
            html_content = self.format_report_as_html(report)
            html_part = MIMEText(html_content, 'html')
            msg.attach(html_part)
            
            # Send via local mail system
            try:
                # Try using sendmail first
                sendmail_process = subprocess.Popen(
                    ['sendmail', '-t'],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                stdout, stderr = sendmail_process.communicate(input=msg.as_string().encode())
                
                if sendmail_process.returncode == 0:
                    logger.info(f"Report sent via sendmail to {recipient}")
                    return True
                else:
                    logger.warning(f"Sendmail failed: {stderr.decode()}")
                    
            except FileNotFoundError:
                logger.warning("Sendmail not found, trying SMTP")
            
            # Fallback to SMTP
            try:
                with smtplib.SMTP('localhost', 25) as server:
                    server.send_message(msg)
                    logger.info(f"Report sent via SMTP to {recipient}")
                    return True
            except Exception as smtp_error:
                logger.error(f"SMTP failed: {smtp_error}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending report via email: {e}")
            return False


class ReportScheduler:
    """Manages scheduled report generation and delivery"""
    
    def __init__(self, db_path: str, config: ChimeraConfig):
        self.db_path = db_path
        self.config = config
        self.generator = ReportGenerator(db_path, config)
    
    def generate_and_save_daily_report(self, output_dir: str = "/var/lib/chimera/reports") -> str:
        """Generate and save daily report"""
        try:
            report = self.generator.generate_daily_report()
            return self.generator.save_report(report, output_dir)
        except Exception as e:
            logger.error(f"Error generating daily report: {e}")
            raise
    
    def generate_and_email_daily_report(self, recipient: str, 
                                      output_dir: str = "/var/lib/chimera/reports") -> bool:
        """Generate, save, and email daily report"""
        try:
            # Generate and save report
            report_path = self.generate_and_save_daily_report(output_dir)
            
            # Generate report object for email
            report = self.generator.generate_daily_report()
            
            # Send via email
            success = self.generator.send_report_via_email(report, recipient)
            
            if success:
                logger.info(f"Daily report generated and sent to {recipient}")
            else:
                logger.error(f"Failed to send report to {recipient}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error in generate_and_email_daily_report: {e}")
            return False