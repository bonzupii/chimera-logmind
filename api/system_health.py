#!/usr/bin/env python3
import psutil
import socket
import datetime as dt
import subprocess
import json
from typing import Dict, Any, List, Optional
import threading
import time

from .db import get_connection


class SystemMetricsCollector:
    """Collect system metrics and store in DuckDB"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path

    def collect_cpu_metrics(self) -> Dict[str, Any]:
        """Collect CPU metrics"""
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_count = psutil.cpu_count()
        cpu_freq = psutil.cpu_freq()
        cpu_stats = psutil.cpu_stats()

        return {
            "timestamp": dt.datetime.now(dt.timezone.utc),
            "metric_type": "cpu",
            "cpu_percent": cpu_percent,
            "cpu_count": cpu_count,
            "cpu_freq_current": cpu_freq.current if cpu_freq else None,
            "cpu_freq_min": cpu_freq.min if cpu_freq else None,
            "cpu_freq_max": cpu_freq.max if cpu_freq else None,
            "cpu_ctx_switches": cpu_stats.ctx_switches,
            "cpu_interrupts": cpu_stats.interrupts,
            "cpu_soft_interrupts": cpu_stats.soft_interrupts,
            "cpu_syscalls": cpu_stats.syscalls,
        }

    def collect_memory_metrics(self) -> Dict[str, Any]:
        """Collect memory metrics"""
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()

        return {
            "timestamp": dt.datetime.now(dt.timezone.utc),
            "metric_type": "memory",
            "memory_total": memory.total,
            "memory_available": memory.available,
            "memory_used": memory.used,
            "memory_percent": memory.percent,
            "memory_free": memory.free,
            "memory_active": memory.active,
            "memory_inactive": memory.inactive,
            "memory_buffers": memory.buffers,
            "memory_cached": memory.cached,
            "memory_shared": memory.shared,
            "swap_total": swap.total,
            "swap_used": swap.used,
            "swap_free": swap.free,
            "swap_percent": swap.percent,
        }

    def collect_disk_metrics(self) -> List[Dict[str, Any]]:
        """Collect disk metrics for all partitions"""
        metrics = []
        timestamp = dt.datetime.utcnow()

        for partition in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                io_counters = psutil.disk_io_counters(perdisk=True).get(partition.device, None)

                metric = {
                    "timestamp": timestamp,
                    "metric_type": "disk",
                    "device": partition.device,
                    "mountpoint": partition.mountpoint,
                    "fstype": partition.fstype,
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": usage.percent,
                }

                if io_counters:
                    metric.update({
                        "read_count": io_counters.read_count,
                        "write_count": io_counters.write_count,
                        "read_bytes": io_counters.read_bytes,
                        "write_bytes": io_counters.write_bytes,
                        "read_time": io_counters.read_time,
                        "write_time": io_counters.write_time,
                    })

                metrics.append(metric)
            except (OSError, PermissionError):
                continue

        return metrics

    def collect_network_metrics(self) -> List[Dict[str, Any]]:
        """Collect network metrics for all interfaces"""
        metrics = []
        timestamp = dt.datetime.utcnow()

        # Get network I/O counters
        net_io = psutil.net_io_counters(pernic=True)

        # Get network addresses
        net_addrs = psutil.net_if_addrs()

        for interface, counters in net_io.items():
            try:
                # Get interface addresses
                addrs = net_addrs.get(interface, [])
                ipv4 = None
                ipv6 = None

                for addr in addrs:
                    if addr.family == socket.AF_INET:
                        ipv4 = addr.address
                    elif addr.family == socket.AF_INET6:
                        ipv6 = addr.address

                metric = {
                    "timestamp": timestamp,
                    "metric_type": "network",
                    "interface": interface,
                    "bytes_sent": counters.bytes_sent,
                    "bytes_recv": counters.bytes_recv,
                    "packets_sent": counters.packets_sent,
                    "packets_recv": counters.packets_recv,
                    "errin": counters.errin,
                    "errout": counters.errout,
                    "dropin": counters.dropin,
                    "dropout": counters.dropout,
                    "ipv4": ipv4,
                    "ipv6": ipv6,
                }

                metrics.append(metric)
            except Exception:
                continue

        return metrics

    def collect_service_metrics(self) -> List[Dict[str, Any]]:
        """Collect systemd service status metrics"""
        metrics = []
        timestamp = dt.datetime.utcnow()

        try:
            # Get systemd service status
            result = subprocess.run(
                ["systemctl", "list-units", "--type=service", "--state=running", "--no-pager"],
                capture_output=True, text=True, timeout=10
            )

            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                for line in lines[1:]:  # Skip header
                    if line.strip():
                        parts = line.split()
                        if len(parts) >= 4:
                            service_name = parts[0]
                            load_state = parts[1]
                            active_state = parts[2]
                            sub_state = parts[3]

                            metrics.append({
                                "timestamp": timestamp,
                                "metric_type": "service",
                                "service_name": service_name,
                                "load_state": load_state,
                                "active_state": active_state,
                                "sub_state": sub_state,
                            })
        except Exception:
            pass

        return metrics

    def collect_uptime_metrics(self) -> Dict[str, Any]:
        """Collect system uptime metrics"""
        boot_time = dt.datetime.fromtimestamp(psutil.boot_time())
        uptime = dt.datetime.utcnow() - boot_time

        return {
            "timestamp": dt.datetime.utcnow(),
            "metric_type": "uptime",
            "boot_time": boot_time,
            "uptime_seconds": uptime.total_seconds(),
            "uptime_days": uptime.days,
        }

    def collect_all_metrics(self) -> Dict[str, Any]:
        """Collect all system metrics"""
        return {
            "cpu": self.collect_cpu_metrics(),
            "memory": self.collect_memory_metrics(),
            "disk": self.collect_disk_metrics(),
            "network": self.collect_network_metrics(),
            "services": self.collect_service_metrics(),
            "uptime": self.collect_uptime_metrics(),
        }

    def _convert_timestamps_to_iso(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively convert datetime objects in a dictionary to ISO format strings."""
        for k, v in data.items():
            if isinstance(v, dt.datetime):
                data[k] = v.isoformat()
            elif isinstance(v, dict):
                data[k] = self._convert_timestamps_to_iso(v)
            elif isinstance(v, list):
                data[k] = [self._convert_timestamps_to_iso(item) if isinstance(item, dict) else item for item in v]
        return data

    def store_metrics(self, metrics: Dict[str, Any]) -> int:
        """Store metrics in DuckDB"""
        conn = get_connection(self.db_path)
        try:
            # Ensure metrics table exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_metrics (
                    timestamp TIMESTAMP NOT NULL,
                    metric_type TEXT NOT NULL,
                    metric_data TEXT
                );
            """)

            conn.execute("CREATE INDEX IF NOT EXISTS idx_system_metrics_ts_type ON system_metrics (timestamp, metric_type);")

            total_stored = 0

            # Store each metric type
            for metric_type, metric_data in metrics.items():
                if isinstance(metric_data, list):
                    # Multiple metrics (disk, network, services)
                    for metric in metric_data:
                        conn.execute(
                            "INSERT INTO system_metrics (timestamp, metric_type, metric_data) VALUES (?, ?, ?)",
                            [metric["timestamp"], metric_type, json.dumps(self._convert_timestamps_to_iso(metric))]
                        )
                        total_stored += 1
                else:
                    # Single metric (cpu, memory, uptime)
                    conn.execute(
                        "INSERT INTO system_metrics (timestamp, metric_type, metric_data) VALUES (?, ?, ?)",
                        [metric_data["timestamp"], metric_type, json.dumps(self._convert_timestamps_to_iso(metric_data))]
                    )
                    total_stored += 1

            return total_stored

        finally:
            conn.close()


class SystemHealthMonitor:
    """System health monitoring with alerts and thresholds"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        self.collector = SystemMetricsCollector(db_path)
        self._monitoring = False
        self._monitor_thread = None

    def start_monitoring(self, interval_seconds: int = 60):
        """Start continuous monitoring"""
        if self._monitoring:
            return

        self._monitoring = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval_seconds,),
            daemon=True
        )
        self._monitor_thread.start()

    def stop_monitoring(self):
        """Stop continuous monitoring"""
        self._monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)

    def _monitor_loop(self, interval_seconds: int):
        """Monitoring loop"""
        while self._monitoring:
            try:
                metrics = self.collector.collect_all_metrics()
                self.collector.store_metrics(metrics)

                # Check for alerts
                alerts = self.check_alerts(metrics)
                if alerts:
                    self.store_alerts(alerts)

            except Exception as e:
                print(f"Error in monitoring loop: {e}")

            time.sleep(interval_seconds)

    def check_alerts(self, metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Check metrics against thresholds and generate alerts"""
        alerts = []
        timestamp = dt.datetime.utcnow()

        # CPU alerts
        cpu_metric = metrics.get("cpu", {})
        if cpu_metric.get("cpu_percent", 0) > 90:
            alerts.append({
                "timestamp": timestamp,
                "alert_type": "high_cpu",
                "severity": "warning",
                "message": f"High CPU usage: {cpu_metric['cpu_percent']}%",
                "metric_data": cpu_metric,
            })

        # Memory alerts
        memory_metric = metrics.get("memory", {})
        if memory_metric.get("memory_percent", 0) > 90:
            alerts.append({
                "timestamp": timestamp,
                "alert_type": "high_memory",
                "severity": "warning",
                "message": f"High memory usage: {memory_metric['memory_percent']}%",
                "metric_data": memory_metric,
            })

        # Disk alerts
        for disk_metric in metrics.get("disk", []):
            if disk_metric.get("percent", 0) > 90:
                alerts.append({
                    "timestamp": timestamp,
                    "alert_type": "high_disk",
                    "severity": "warning",
                    "message": f"High disk usage on {disk_metric['mountpoint']}: {disk_metric['percent']}%",
                    "metric_data": disk_metric,
                })

        # Service alerts
        services = metrics.get("services", [])
        critical_services = ["sshd", "systemd", "dbus"]
        running_services = {s["service_name"] for s in services}

        for service in critical_services:
            if service not in running_services:
                alerts.append({
                    "timestamp": timestamp,
                    "alert_type": "service_down",
                    "severity": "critical",
                    "message": f"Critical service {service} is not running",
                    "metric_data": {"service": service},
                })

        return alerts

    def store_alerts(self, alerts: List[Dict[str, Any]]):
        """Store alerts in database"""
        conn = get_connection(self.db_path)
        try:
            # Ensure alerts table exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_alerts (
                    timestamp TIMESTAMP NOT NULL,
                    alert_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    metric_data TEXT,
                    acknowledged BOOLEAN DEFAULT FALSE,
                    acknowledged_at TIMESTAMP,
                    INDEX (timestamp, alert_type, severity)
                );
            """)

            for alert in alerts:
                conn.execute(
                    "INSERT INTO system_alerts (timestamp, alert_type, severity, message, metric_data) VALUES (?, ?, ?, ?, ?)",
                    [
                        alert["timestamp"],
                        alert["alert_type"],
                        alert["severity"],
                        alert["message"],
                        json.dumps(alert["metric_data"])
                    ]
                )

        finally:
            conn.close()

    def get_metrics(self, metric_type: Optional[str] = None,
                   since_seconds: int = 3600, limit: int = 1000) -> List[Dict[str, Any]]:
        """Get stored metrics"""
        conn = get_connection(self.db_path)
        try:
            since_ts = dt.datetime.utcnow() - dt.timedelta(seconds=since_seconds)

            if metric_type:
                sql = """
                    SELECT timestamp, metric_type, metric_data
                    FROM system_metrics
                    WHERE timestamp >= ? AND metric_type = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """
                params = [since_ts, metric_type, limit]
            else:
                sql = """
                    SELECT timestamp, metric_type, metric_data
                    FROM system_metrics
                    WHERE timestamp >= ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """
                params = [since_ts, limit]

            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

            metrics = []
            for timestamp, metric_type, metric_data in rows:
                try:
                    data = json.loads(metric_data)
                    metrics.append({
                        "timestamp": timestamp.isoformat(),
                        "metric_type": metric_type,
                        "data": data,
                    })
                except json.JSONDecodeError:
                    continue

            return metrics

        finally:
            conn.close()

    def get_alerts(self, since_seconds: int = 86400,
                  severity: Optional[str] = None,
                  acknowledged: Optional[bool] = None) -> List[Dict[str, Any]]:
        """Get stored alerts"""
        conn = get_connection(self.db_path)
        try:
            since_ts = dt.datetime.utcnow() - dt.timedelta(seconds=since_seconds)

            sql = "SELECT timestamp, alert_type, severity, message, metric_data, acknowledged FROM system_alerts WHERE timestamp >= ?"
            params: list = [since_ts]

            if severity:
                sql += " AND severity = ?"
                params.append(severity)

            if acknowledged is not None:
                sql += " AND acknowledged = ?"
                params.append(acknowledged)

            sql += " ORDER BY timestamp DESC"

            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

            alerts = []
            for timestamp, alert_type, severity, message, metric_data, acknowledged in rows:
                try:
                    data = json.loads(metric_data) if metric_data else {}
                    alerts.append({
                        "timestamp": timestamp.isoformat(),
                        "alert_type": alert_type,
                        "severity": severity,
                        "message": message,
                        "data": data,
                        "acknowledged": bool(acknowledged),
                    })
                except json.JSONDecodeError:
                    continue

            return alerts

        finally:
            conn.close()
