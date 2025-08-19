import pytest
from unittest.mock import patch, MagicMock
import datetime as dt
from api.system_health import SystemMetricsCollector, SystemHealthMonitor


def test_system_metrics_collector_init():
    """Test SystemMetricsCollector initialization"""
    collector = SystemMetricsCollector()
    assert collector.db_path is None
    
    # Test with db_path
    collector = SystemMetricsCollector("/tmp/test.db")
    assert collector.db_path == "/tmp/test.db"


@patch('api.system_health.psutil')
def test_collect_cpu_metrics(mock_psutil):
    """Test collecting CPU metrics"""
    # Mock psutil responses
    mock_psutil.cpu_percent.return_value = 25.0
    mock_psutil.cpu_count.return_value = 4
    mock_psutil.cpu_freq.return_value = MagicMock(current=2400.0, min=800.0, max=3200.0)
    mock_psutil.cpu_stats.return_value = MagicMock(
        ctx_switches=1000, interrupts=500, soft_interrupts=200, syscalls=800
    )
    
    collector = SystemMetricsCollector()
    metrics = collector.collect_cpu_metrics()
    
    # Check metrics structure
    assert metrics["metric_type"] == "cpu"
    assert metrics["cpu_percent"] == 25.0
    assert metrics["cpu_count"] == 4
    assert metrics["cpu_freq_current"] == 2400.0
    assert metrics["cpu_ctx_switches"] == 1000


@patch('api.system_health.psutil')
def test_collect_memory_metrics(mock_psutil):
    """Test collecting memory metrics"""
    # Mock psutil responses
    mock_memory = MagicMock(
        total=8000000000, available=2000000000, used=6000000000,
        percent=75.0, free=1000000000, active=5000000000,
        inactive=1000000000, buffers=500000000, cached=1500000000,
        shared=100000000
    )
    mock_swap = MagicMock(
        total=2000000000, used=500000000, free=1500000000, percent=25.0
    )
    mock_psutil.virtual_memory.return_value = mock_memory
    mock_psutil.swap_memory.return_value = mock_swap
    
    collector = SystemMetricsCollector()
    metrics = collector.collect_memory_metrics()
    
    # Check metrics structure
    assert metrics["metric_type"] == "memory"
    assert metrics["memory_total"] == 8000000000
    assert metrics["memory_percent"] == 75.0
    assert metrics["swap_total"] == 2000000000
    assert metrics["swap_percent"] == 25.0


@patch('api.system_health.psutil')
def test_collect_disk_metrics(mock_psutil):
    """Test collecting disk metrics"""
    # Mock psutil responses
    mock_psutil.disk_partitions.return_value = [
        MagicMock(device='/dev/sda1', mountpoint='/', fstype='ext4'),
        MagicMock(device='/dev/sda2', mountpoint='/home', fstype='ext4')
    ]
    
    mock_psutil.disk_usage.side_effect = [
        MagicMock(total=50000000000, used=30000000000, free=20000000000, percent=60.0),
        MagicMock(total=100000000000, used=40000000000, free=60000000000, percent=40.0)
    ]
    
    # Mock disk I/O counters
    mock_io_counters = MagicMock(
        read_count=1000, write_count=500, read_bytes=1024000,
        write_bytes=512000, read_time=100, write_time=50
    )
    mock_disk_io = MagicMock()
    mock_disk_io.get.return_value = mock_io_counters
    mock_psutil.disk_io_counters.return_value = mock_disk_io
    
    collector = SystemMetricsCollector()
    metrics = collector.collect_disk_metrics()
    
    # Check we got metrics for both partitions
    assert len(metrics) == 2
    assert metrics[0]["metric_type"] == "disk"
    assert metrics[0]["device"] == "/dev/sda1"
    assert metrics[0]["mountpoint"] == "/"
    assert metrics[0]["percent"] == 60.0
    assert metrics[0]["read_count"] == 1000


@patch('api.system_health.psutil')
def test_collect_disk_metrics_handles_oserror(mock_psutil):
    mock_psutil.disk_partitions.return_value = [
        MagicMock(device='/dev/sda1', mountpoint='/', fstype='ext4')
    ]
    def raise_oserror(_):
        raise OSError("boom")
    mock_psutil.disk_usage.side_effect = raise_oserror
    mock_psutil.disk_io_counters.return_value = MagicMock(get=lambda *_: None)
    collector = SystemMetricsCollector()
    metrics = collector.collect_disk_metrics()
    assert metrics == []


@patch('api.system_health.socket')
@patch('api.system_health.psutil')
def test_collect_network_metrics(mock_psutil, mock_socket):
    """Test collecting network metrics"""
    # Mock socket constants
    mock_socket.AF_INET = 2
    mock_socket.AF_INET6 = 10
    
    # Mock psutil responses
    mock_counters = MagicMock()
    mock_counters.bytes_sent = 1000000
    mock_counters.bytes_recv = 2000000
    mock_counters.packets_sent = 1000
    mock_counters.packets_recv = 2000
    mock_counters.errin = 5
    mock_counters.errout = 3
    mock_counters.dropin = 2
    mock_counters.dropout = 1
    
    # Mock net_io_counters to return dict with eth0 interface
    mock_net_io = {"eth0": mock_counters}
    mock_psutil.net_io_counters.return_value = mock_net_io
    
    mock_addr = MagicMock()
    mock_addr.family = 2  # AF_INET
    mock_addr.address = "192.168.1.100"
    
    mock_psutil.net_if_addrs.return_value = {"eth0": [mock_addr]}
    
    collector = SystemMetricsCollector()
    metrics = collector.collect_network_metrics()
    
    # Check that we get at least one metric
    assert len(metrics) >= 1
    # Find the eth0 metric
    eth0_metric = None
    for metric in metrics:
        if metric["interface"] == "eth0":
            eth0_metric = metric
            break
    
    assert eth0_metric is not None
    assert eth0_metric["metric_type"] == "network"
    assert eth0_metric["bytes_sent"] == 1000000
    assert eth0_metric["ipv4"] == "192.168.1.100"


@patch('api.system_health.psutil')
def test_collect_network_metrics_handles_exception(mock_psutil):
    mock_psutil.net_io_counters.return_value = {"eth0": object()}
    mock_psutil.net_if_addrs.return_value = {}
    collector = SystemMetricsCollector()
    metrics = collector.collect_network_metrics()
    assert metrics == []


@patch('api.system_health.subprocess')
def test_collect_service_metrics(mock_subprocess):
    """Test collecting service metrics"""
    # Mock subprocess response
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = """UNIT                     LOAD   ACTIVE SUB     DESCRIPTION
sshd.service             loaded active running OpenSSH Daemon
nginx.service            loaded active running nginx
systemd-networkd.service loaded active running Network Configuration
"""
    mock_subprocess.run.return_value = mock_result
    
    collector = SystemMetricsCollector()
    metrics = collector.collect_service_metrics()
    
    # Check metrics structure
    assert len(metrics) == 3
    assert metrics[0]["metric_type"] == "service"
    assert metrics[0]["service_name"] == "sshd.service"
    assert metrics[0]["active_state"] == "active"


@patch('api.system_health.subprocess.run', side_effect=Exception("fail"))
def test_collect_service_metrics_exception(_mock_run):
    collector = SystemMetricsCollector()
    metrics = collector.collect_service_metrics()
    assert metrics == []


@patch('api.system_health.psutil')
@patch('api.system_health.dt')
def test_collect_uptime_metrics(mock_dt_module, mock_psutil):
    """Test collecting uptime metrics"""
    # Mock psutil responses
    boot_time = dt.datetime(2023, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
    current_time = dt.datetime(2023, 1, 6, 12, 0, 0, tzinfo=dt.timezone.utc)  # 5 days later
    
    # Mock the datetime class and its methods used in code
    mock_datetime = MagicMock()
    mock_datetime.now.return_value = current_time
    mock_datetime.fromtimestamp.return_value = boot_time
    mock_dt_module.datetime = mock_datetime
    mock_dt_module.timezone = dt.timezone
    
    mock_psutil.boot_time.return_value = int(boot_time.timestamp())
    
    collector = SystemMetricsCollector()
    metrics = collector.collect_uptime_metrics()
    
    # Check metrics structure
    assert metrics["metric_type"] == "uptime"
    # Check that we have reasonable values
    assert "uptime_days" in metrics
    assert "uptime_seconds" in metrics
    # 5 days = 432000 seconds
    assert metrics["uptime_seconds"] == 432000.0
    assert metrics["uptime_days"] == 5


def test_convert_timestamps_to_iso():
    collector = SystemMetricsCollector()
    data = {
        'a': dt.datetime(2024,1,1, tzinfo=dt.timezone.utc),
        'b': {'c': dt.datetime(2024,1,2, tzinfo=dt.timezone.utc)},
        'd': [
            {'e': dt.datetime(2024,1,3, tzinfo=dt.timezone.utc)},
            5
        ]
    }
    out = collector._convert_timestamps_to_iso(data)
    assert isinstance(out['a'], str)
    assert isinstance(out['b']['c'], str)
    assert isinstance(out['d'][0]['e'], str)


def test_collect_all_metrics():
    collector = SystemMetricsCollector()
    # Patch methods
    collector.collect_cpu_metrics = lambda: {'timestamp': dt.datetime.now(dt.timezone.utc), 'metric_type': 'cpu'}
    collector.collect_memory_metrics = lambda: {'timestamp': dt.datetime.now(dt.timezone.utc), 'metric_type': 'memory'}
    collector.collect_disk_metrics = lambda: []
    collector.collect_network_metrics = lambda: []
    collector.collect_service_metrics = lambda: []
    collector.collect_uptime_metrics = lambda: {'timestamp': dt.datetime.now(dt.timezone.utc), 'metric_type': 'uptime'}
    metrics = collector.collect_all_metrics()
    assert set(metrics.keys()) == {'cpu','memory','disk','network','services','uptime'}


def test_system_health_monitor_init():
    """Test SystemHealthMonitor initialization"""
    monitor = SystemHealthMonitor()
    assert monitor.db_path is None
    assert monitor._monitoring is False
    assert monitor._monitor_thread is None
    
    # Test with db_path
    monitor = SystemHealthMonitor("/tmp/test.db")
    assert monitor.db_path == "/tmp/test.db"

@patch('api.system_health.time.sleep', side_effect=lambda *_: None)
def test_monitor_loop_and_start_stop(mock_sleep):
    monitor = SystemHealthMonitor()
    # Prepare collector to raise once to hit exception handler
    calls = {'count': 0}
    def raise_once():
        if calls['count'] == 0:
            calls['count'] += 1
            raise Exception('boom')
        return {'cpu': {}, 'memory': {}, 'disk': [], 'network': [], 'services': [], 'uptime': {}}
    monitor.collector.collect_all_metrics = raise_once
    monitor.collector.store_metrics = lambda *_: None
    monitor.check_alerts = lambda *_: []
    monitor.store_alerts = lambda *_: None

    # Run one loop iteration manually with _monitoring True, then set to False via next sleep
    monitor._monitoring = True
    # Call loop; because sleep is patched and we set _monitoring False immediately after exception handled
    # We'll toggle monitoring flag ourselves to break after first cycle
    def toggle_and_sleep(*_):
        monitor._monitoring = False
    mock_sleep.side_effect = toggle_and_sleep
    monitor._monitor_loop(interval_seconds=0)
    # exercise stop when already False
    monitor.stop_monitoring()

    # start_monitoring should start a thread; calling again should no-op
    monitor._monitoring = False
    monitor.start_monitoring(interval_seconds=0)
    # Call start again while already monitoring True to hit early return
    monitor._monitoring = True
    monitor.start_monitoring(interval_seconds=0)
    # Stop
    monitor._monitoring = False
    monitor.stop_monitoring()


def test_check_alerts_memory():
    monitor = SystemHealthMonitor()
    metrics = {
        'cpu': {'cpu_percent': 10},
        'memory': {'memory_percent': 95},
        'disk': [],
        'services': []
    }
    alerts = monitor.check_alerts(metrics)
    assert any(a['alert_type']=='high_memory' for a in alerts)

@patch('api.system_health.time.sleep', side_effect=lambda *_: None)
def test_monitor_loop_with_alerts(mock_sleep):
    monitor = SystemHealthMonitor()
    monitor.collector.collect_all_metrics = lambda: {'cpu': {'cpu_percent': 95}, 'memory': {}, 'disk': [], 'network': [], 'services': [], 'uptime': {}}
    # Avoid DB write in store_metrics
    monitor.collector.store_metrics = lambda *_: None
    # Ensure check_alerts returns non-empty
    monitor.check_alerts = lambda metrics: [{'timestamp': dt.datetime.now(dt.timezone.utc), 'alert_type': 'high_cpu', 'severity': 'warning', 'message': 'm', 'metric_data': {}}]
    called = {'store_alerts': 0}
    def _store(alerts):
        called['store_alerts'] += 1
    monitor.store_alerts = _store

    monitor._monitoring = True
    def stop_after(*_):
        monitor._monitoring = False
    mock_sleep.side_effect = stop_after
    monitor._monitor_loop(interval_seconds=0)
    assert called['store_alerts'] == 1

@patch('api.system_health.socket')
@patch('api.system_health.psutil')
def test_collect_network_metrics_ipv6(mock_psutil, mock_socket):
    mock_socket.AF_INET = 2
    mock_socket.AF_INET6 = 10
    counters = MagicMock(bytes_sent=1, bytes_recv=2, packets_sent=1, packets_recv=1, errin=0, errout=0, dropin=0, dropout=0)
    mock_psutil.net_io_counters.return_value = {'eth1': counters}
    addr6 = MagicMock(); addr6.family = 10; addr6.address = 'fe80::1'
    mock_psutil.net_if_addrs.return_value = {'eth1': [addr6]}
    collector = SystemMetricsCollector()
    metrics = collector.collect_network_metrics()
    assert metrics and metrics[0]['ipv6'] == 'fe80::1'

import pytest
from unittest.mock import patch, MagicMock
import datetime as dt
from api.system_health import SystemMetricsCollector, SystemHealthMonitor


def test_system_metrics_collector_init():
    """Test SystemMetricsCollector initialization"""
    collector = SystemMetricsCollector()
    assert collector.db_path is None
    
    # Test with db_path
    collector = SystemMetricsCollector("/tmp/test.db")
    assert collector.db_path == "/tmp/test.db"


@patch('api.system_health.psutil')
def test_collect_cpu_metrics(mock_psutil):
    """Test collecting CPU metrics"""
    # Mock psutil responses
    mock_psutil.cpu_percent.return_value = 25.0
    mock_psutil.cpu_count.return_value = 4
    mock_psutil.cpu_freq.return_value = MagicMock(current=2400.0, min=800.0, max=3200.0)
    mock_psutil.cpu_stats.return_value = MagicMock(
        ctx_switches=1000, interrupts=500, soft_interrupts=200, syscalls=800
    )
    
    collector = SystemMetricsCollector()
    metrics = collector.collect_cpu_metrics()
    
    # Check metrics structure
    assert metrics["metric_type"] == "cpu"
    assert metrics["cpu_percent"] == 25.0
    assert metrics["cpu_count"] == 4
    assert metrics["cpu_freq_current"] == 2400.0
    assert metrics["cpu_ctx_switches"] == 1000


@patch('api.system_health.psutil')
def test_collect_memory_metrics(mock_psutil):
    """Test collecting memory metrics"""
    # Mock psutil responses
    mock_memory = MagicMock(
        total=8000000000, available=2000000000, used=6000000000,
        percent=75.0, free=1000000000, active=5000000000,
        inactive=1000000000, buffers=500000000, cached=1500000000,
        shared=100000000
    )
    mock_swap = MagicMock(
        total=2000000000, used=500000000, free=1500000000, percent=25.0
    )
    mock_psutil.virtual_memory.return_value = mock_memory
    mock_psutil.swap_memory.return_value = mock_swap
    
    collector = SystemMetricsCollector()
    metrics = collector.collect_memory_metrics()
    
    # Check metrics structure
    assert metrics["metric_type"] == "memory"
    assert metrics["memory_total"] == 8000000000
    assert metrics["memory_percent"] == 75.0
    assert metrics["swap_total"] == 2000000000
    assert metrics["swap_percent"] == 25.0


@patch('api.system_health.psutil')
def test_collect_disk_metrics(mock_psutil):
    """Test collecting disk metrics"""
    # Mock psutil responses
    mock_psutil.disk_partitions.return_value = [
        MagicMock(device='/dev/sda1', mountpoint='/', fstype='ext4'),
        MagicMock(device='/dev/sda2', mountpoint='/home', fstype='ext4')
    ]
    
    mock_psutil.disk_usage.side_effect = [
        MagicMock(total=50000000000, used=30000000000, free=20000000000, percent=60.0),
        MagicMock(total=100000000000, used=40000000000, free=60000000000, percent=40.0)
    ]
    
    # Mock disk I/O counters
    mock_io_counters = MagicMock(
        read_count=1000, write_count=500, read_bytes=1024000,
        write_bytes=512000, read_time=100, write_time=50
    )
    mock_disk_io = MagicMock()
    mock_disk_io.get.return_value = mock_io_counters
    mock_psutil.disk_io_counters.return_value = mock_disk_io
    
    collector = SystemMetricsCollector()
    metrics = collector.collect_disk_metrics()
    
    # Check we got metrics for both partitions
    assert len(metrics) == 2
    assert metrics[0]["metric_type"] == "disk"
    assert metrics[0]["device"] == "/dev/sda1"
    assert metrics[0]["mountpoint"] == "/"
    assert metrics[0]["percent"] == 60.0
    assert metrics[0]["read_count"] == 1000


@patch('api.system_health.socket')
@patch('api.system_health.psutil')
def test_collect_network_metrics(mock_psutil, mock_socket):
    """Test collecting network metrics"""
    # Mock socket constants
    mock_socket.AF_INET = 2
    mock_socket.AF_INET6 = 10
    
    # Mock psutil responses
    mock_counters = MagicMock()
    mock_counters.bytes_sent = 1000000
    mock_counters.bytes_recv = 2000000
    mock_counters.packets_sent = 1000
    mock_counters.packets_recv = 2000
    mock_counters.errin = 5
    mock_counters.errout = 3
    mock_counters.dropin = 2
    mock_counters.dropout = 1
    
    # Mock net_io_counters to return dict with eth0 interface
    mock_net_io = {"eth0": mock_counters}
    mock_psutil.net_io_counters.return_value = mock_net_io
    
    mock_addr = MagicMock()
    mock_addr.family = 2  # AF_INET
    mock_addr.address = "192.168.1.100"
    
    mock_psutil.net_if_addrs.return_value = {"eth0": [mock_addr]}
    
    collector = SystemMetricsCollector()
    metrics = collector.collect_network_metrics()
    
    # Check that we get at least one metric
    assert len(metrics) >= 1
    # Find the eth0 metric
    eth0_metric = None
    for metric in metrics:
        if metric["interface"] == "eth0":
            eth0_metric = metric
            break
    
    assert eth0_metric is not None
    assert eth0_metric["metric_type"] == "network"
    assert eth0_metric["bytes_sent"] == 1000000
    assert eth0_metric["ipv4"] == "192.168.1.100"


@patch('api.system_health.subprocess')
def test_collect_service_metrics(mock_subprocess):
    """Test collecting service metrics"""
    # Mock subprocess response
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = """UNIT                     LOAD   ACTIVE SUB     DESCRIPTION
sshd.service             loaded active running OpenSSH Daemon
nginx.service            loaded active running nginx
systemd-networkd.service loaded active running Network Configuration
"""
    mock_subprocess.run.return_value = mock_result
    
    collector = SystemMetricsCollector()
    metrics = collector.collect_service_metrics()
    
    # Check metrics structure
    assert len(metrics) == 3
    assert metrics[0]["metric_type"] == "service"
    assert metrics[0]["service_name"] == "sshd.service"
    assert metrics[0]["active_state"] == "active"


@patch('api.system_health.psutil')
@patch('api.system_health.dt')
def test_collect_uptime_metrics(mock_dt_module, mock_psutil):
    """Test collecting uptime metrics"""
    # Mock psutil responses
    boot_time = dt.datetime(2023, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
    current_time = dt.datetime(2023, 1, 6, 12, 0, 0, tzinfo=dt.timezone.utc)  # 5 days later
    
    # Mock the datetime class and its methods used in code
    mock_datetime = MagicMock()
    mock_datetime.now.return_value = current_time
    mock_datetime.fromtimestamp.return_value = boot_time
    mock_dt_module.datetime = mock_datetime
    mock_dt_module.timezone = dt.timezone
    
    mock_psutil.boot_time.return_value = int(boot_time.timestamp())
    
    collector = SystemMetricsCollector()
    metrics = collector.collect_uptime_metrics()
    
    # Check metrics structure
    assert metrics["metric_type"] == "uptime"
    # Check that we have reasonable values
    assert "uptime_days" in metrics
    assert "uptime_seconds" in metrics
    # 5 days = 432000 seconds
    assert metrics["uptime_seconds"] == 432000.0
    assert metrics["uptime_days"] == 5


def test_system_health_monitor_init():
    """Test SystemHealthMonitor initialization"""
    monitor = SystemHealthMonitor()
    assert monitor.db_path is None
    assert monitor._monitoring is False
    assert monitor._monitor_thread is None
    
    # Test with db_path
    monitor = SystemHealthMonitor("/tmp/test.db")
    assert monitor.db_path == "/tmp/test.db"