use anyhow::Result;
use crossterm::{
    event::{self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyModifiers},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{
    backend::CrosstermBackend,
    layout::{Alignment, Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::Span,
    widgets::{Block, Borders, Clear, Gauge, List, ListItem, ListState, Paragraph, Tabs, Wrap},
    Frame, Terminal,
};

use std::collections::HashMap;
use std::io::{self, Write};
use std::net::Shutdown;
use std::os::unix::net::UnixStream;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

// Data structures
#[derive(Clone, Debug)]
struct LogItem {
    ts: String,
    hostname: String,
    unit: String,
    severity: String,
    source: String,
    message: String,
    #[allow(dead_code)]
    fingerprint: Option<String>,
}

#[derive(Clone, Debug)]
struct MetricItem {
    timestamp: f64,
    metric_type: String,
    value: f64,
    unit: String,
    hostname: String,
}

#[derive(Clone, Debug)]
struct AlertItem {
    id: String,
    timestamp: f64,
    severity: String,
    message: String,
    acknowledged: bool,
    source: String,
}

#[derive(Clone, Debug)]
struct ChatMessage {
    role: String,
    content: String,
    timestamp: f64,
    confidence: Option<f64>,
    sources_count: Option<usize>,
}

#[derive(Clone, Debug)]
struct AnomalyItem {
    timestamp: f64,
    anomaly_score: f64,
    message: String,
    unit: String,
    severity: String,
}

#[derive(Clone, Debug)]
struct ReportItem {
    id: String,
    title: String,
    format: String,
    generated_at: String,
    size_bytes: usize,
}

#[derive(Clone, Debug)]
struct SecurityAuditItem {
    id: String,
    tool: String,
    timestamp: f64,
    status: String,
    findings_count: usize,
    summary: String,
}

#[derive(Clone, Debug)]
struct ConfigSource {
    name: String,
    source_type: String,
    enabled: bool,
    config: HashMap<String, String>,
}

#[derive(Clone, Debug)]
struct SystemHealth {
    cpu_percent: f64,
    memory_percent: f64,
    disk_percent: f64,
    uptime_seconds: u64,
    load_average: (f64, f64, f64),
    network_connections: usize,
    service_status: HashMap<String, bool>,
}

// Application state
struct App {
    // Navigation
    tab_index: usize,
    should_quit: bool,

    // Data
    logs: Vec<LogItem>,
    metrics: Vec<MetricItem>,
    alerts: Vec<AlertItem>,
    chat_messages: Vec<ChatMessage>,
    anomalies: Vec<AnomalyItem>,
    reports: Vec<ReportItem>,
    security_audits: Vec<SecurityAuditItem>,
    config_sources: Vec<ConfigSource>,
    system_health: Option<SystemHealth>,

    // UI state
    selected_log: usize,
    selected_alert: usize,
    selected_report: usize,
    selected_audit: usize,
    selected_config: usize,

    // Input state
    input_mode: InputMode,
    input_buffer: String,
    search_query: String,
    search_results: Vec<(LogItem, f64)>,

    // Status and timing
    status: String,
    last_refresh: Instant,
    auto_refresh: bool,

    // Popup state
    show_help: bool,
    show_error: Option<String>,

    // List states for scrolling
    log_list_state: ListState,
    alert_list_state: ListState,
    report_list_state: ListState,
    audit_list_state: ListState,
    config_list_state: ListState,
}

#[derive(PartialEq, Debug)]
enum InputMode {
    Normal,
    Search,
    Chat,
}

impl App {
    fn new() -> Self {
        let mut app = Self {
            tab_index: 0,
            should_quit: false,
            logs: Vec::new(),
            metrics: Vec::new(),
            alerts: Vec::new(),
            chat_messages: Vec::new(),
            anomalies: Vec::new(),
            reports: Vec::new(),
            security_audits: Vec::new(),
            config_sources: Vec::new(),
            system_health: None,
            selected_log: 0,
            selected_alert: 0,
            selected_report: 0,
            selected_audit: 0,
            selected_config: 0,
            input_mode: InputMode::Normal,
            input_buffer: String::new(),
            search_query: String::new(),
            search_results: Vec::new(),
            status: "Ready".to_string(),
            last_refresh: Instant::now(),
            auto_refresh: true,
            show_help: false,
            show_error: None,
            log_list_state: ListState::default(),
            alert_list_state: ListState::default(),
            report_list_state: ListState::default(),
            audit_list_state: ListState::default(),
            config_list_state: ListState::default(),
        };
        app.log_list_state.select(Some(0));
        app.alert_list_state.select(Some(0));
        app.report_list_state.select(Some(0));
        app.audit_list_state.select(Some(0));
        app.config_list_state.select(Some(0));
        app
    }

    fn next_tab(&mut self) {
        self.tab_index = (self.tab_index + 1) % 10;
    }

    fn prev_tab(&mut self) {
        self.tab_index = if self.tab_index > 0 {
            self.tab_index - 1
        } else {
            9
        };
    }

    fn next_item(&mut self) {
        match self.tab_index {
            1 => {
                // Logs
                if !self.logs.is_empty() {
                    self.selected_log = (self.selected_log + 1) % self.logs.len();
                    self.log_list_state.select(Some(self.selected_log));
                }
            }
            4 => {
                // Health - Alerts
                if !self.alerts.is_empty() {
                    self.selected_alert = (self.selected_alert + 1) % self.alerts.len();
                    self.alert_list_state.select(Some(self.selected_alert));
                }
            }
            6 => {
                // Reports
                if !self.reports.is_empty() {
                    self.selected_report = (self.selected_report + 1) % self.reports.len();
                    self.report_list_state.select(Some(self.selected_report));
                }
            }
            7 => {
                // Security
                if !self.security_audits.is_empty() {
                    self.selected_audit = (self.selected_audit + 1) % self.security_audits.len();
                    self.audit_list_state.select(Some(self.selected_audit));
                }
            }
            8 => {
                // Config
                if !self.config_sources.is_empty() {
                    self.selected_config = (self.selected_config + 1) % self.config_sources.len();
                    self.config_list_state.select(Some(self.selected_config));
                }
            }
            _ => {}
        }
    }

    fn prev_item(&mut self) {
        match self.tab_index {
            1 => {
                // Logs
                if !self.logs.is_empty() {
                    self.selected_log = if self.selected_log > 0 {
                        self.selected_log - 1
                    } else {
                        self.logs.len() - 1
                    };
                    self.log_list_state.select(Some(self.selected_log));
                }
            }
            4 => {
                // Health - Alerts
                if !self.alerts.is_empty() {
                    self.selected_alert = if self.selected_alert > 0 {
                        self.selected_alert - 1
                    } else {
                        self.alerts.len() - 1
                    };
                    self.alert_list_state.select(Some(self.selected_alert));
                }
            }
            6 => {
                // Reports
                if !self.reports.is_empty() {
                    self.selected_report = if self.selected_report > 0 {
                        self.selected_report - 1
                    } else {
                        self.reports.len() - 1
                    };
                    self.report_list_state.select(Some(self.selected_report));
                }
            }
            7 => {
                // Security
                if !self.security_audits.is_empty() {
                    self.selected_audit = if self.selected_audit > 0 {
                        self.selected_audit - 1
                    } else {
                        self.security_audits.len() - 1
                    };
                    self.audit_list_state.select(Some(self.selected_audit));
                }
            }
            8 => {
                // Config
                if !self.config_sources.is_empty() {
                    self.selected_config = if self.selected_config > 0 {
                        self.selected_config - 1
                    } else {
                        self.config_sources.len() - 1
                    };
                    self.config_list_state.select(Some(self.selected_config));
                }
            }
            _ => {}
        }
    }
}

// Network functions
fn uds_request(socket_path: &str, command: &str) -> Result<String> {
    let mut stream = UnixStream::connect(socket_path)?;
    stream.write_all(command.as_bytes())?;
    stream.shutdown(Shutdown::Write)?;

    let mut response = String::new();
    std::io::Read::read_to_string(&mut stream, &mut response)?;
    Ok(response)
}

// Data fetching functions
fn fetch_logs(socket: &str, since: u64, limit: usize) -> Result<Vec<LogItem>> {
    let cmd = format!("QUERY_LOGS since={} limit={} order=desc", since, limit);
    let response = uds_request(socket, &cmd)?;

    let mut logs = Vec::new();
    for line in response.lines() {
        if line.trim().is_empty() {
            continue;
        }
        if let Ok(log_data) = serde_json::from_str::<serde_json::Value>(line) {
            logs.push(LogItem {
                ts: log_data
                    .get("ts")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                hostname: log_data
                    .get("hostname")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                unit: log_data
                    .get("unit")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                severity: log_data
                    .get("severity")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                source: log_data
                    .get("source")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                message: log_data
                    .get("message")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                fingerprint: log_data
                    .get("fingerprint")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string()),
            });
        }
    }
    Ok(logs)
}

fn fetch_metrics(
    socket: &str,
    metric_type: Option<&str>,
    since: u64,
    limit: usize,
) -> Result<Vec<MetricItem>> {
    let cmd = if let Some(mt) = metric_type {
        format!("METRICS type={} since={} limit={}", mt, since, limit)
    } else {
        format!("METRICS since={} limit={}", since, limit)
    };
    let response = uds_request(socket, &cmd)?;

    let mut metrics = Vec::new();
    for line in response.lines() {
        if line.trim().is_empty() {
            continue;
        }
        if let Ok(metric_data) = serde_json::from_str::<serde_json::Value>(line) {
            metrics.push(MetricItem {
                timestamp: metric_data
                    .get("timestamp")
                    .and_then(|v| v.as_f64())
                    .unwrap_or(0.0),
                metric_type: metric_data
                    .get("type")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                value: metric_data
                    .get("value")
                    .and_then(|v| v.as_f64())
                    .unwrap_or(0.0),
                unit: metric_data
                    .get("unit")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                hostname: metric_data
                    .get("hostname")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
            });
        }
    }
    Ok(metrics)
}

fn fetch_alerts(socket: &str, since: u64, severity: Option<&str>) -> Result<Vec<AlertItem>> {
    let cmd = if let Some(sev) = severity {
        format!("ALERTS since={} severity={}", since, sev)
    } else {
        format!("ALERTS since={}", since)
    };
    let response = uds_request(socket, &cmd)?;

    let mut alerts = Vec::new();
    for line in response.lines() {
        if line.trim().is_empty() {
            continue;
        }
        if let Ok(alert_data) = serde_json::from_str::<serde_json::Value>(line) {
            alerts.push(AlertItem {
                id: alert_data
                    .get("id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                timestamp: alert_data
                    .get("timestamp")
                    .and_then(|v| v.as_f64())
                    .unwrap_or(0.0),
                severity: alert_data
                    .get("severity")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                message: alert_data
                    .get("message")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                acknowledged: alert_data
                    .get("acknowledged")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false),
                source: alert_data
                    .get("source")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
            });
        }
    }
    Ok(alerts)
}

fn fetch_anomalies(socket: &str, since: u64) -> Result<Vec<AnomalyItem>> {
    let cmd = format!("ANOMALIES since={}", since);
    let response = uds_request(socket, &cmd)?;

    let mut anomalies = Vec::new();
    for line in response.lines() {
        if line.trim().is_empty() {
            continue;
        }
        if let Ok(anom_data) = serde_json::from_str::<serde_json::Value>(line) {
            anomalies.push(AnomalyItem {
                timestamp: anom_data
                    .get("timestamp")
                    .and_then(|v| v.as_f64())
                    .unwrap_or(0.0),
                anomaly_score: anom_data
                    .get("anomaly_score")
                    .and_then(|v| v.as_f64())
                    .unwrap_or(0.0),
                message: anom_data
                    .get("message")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                unit: anom_data
                    .get("unit")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                severity: anom_data
                    .get("severity")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
            });
        }
    }
    Ok(anomalies)
}

fn fetch_reports(socket: &str, limit: usize) -> Result<Vec<ReportItem>> {
    let cmd = format!("REPORT LIST limit={}", limit);
    let response = uds_request(socket, &cmd)?;

    let mut reports = Vec::new();
    for line in response.lines() {
        if line.trim().is_empty() {
            continue;
        }
        if let Ok(report_data) = serde_json::from_str::<serde_json::Value>(line) {
            reports.push(ReportItem {
                id: report_data
                    .get("id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                title: report_data
                    .get("title")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                format: report_data
                    .get("format")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                generated_at: report_data
                    .get("generated_at")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                size_bytes: report_data
                    .get("size_bytes")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0) as usize,
            });
        }
    }
    Ok(reports)
}

fn fetch_security_audits(socket: &str, limit: usize) -> Result<Vec<SecurityAuditItem>> {
    let cmd = format!("AUDIT HISTORY limit={}", limit);
    let response = uds_request(socket, &cmd)?;

    let mut audits = Vec::new();
    for line in response.lines() {
        if line.trim().is_empty() {
            continue;
        }
        if let Ok(audit_data) = serde_json::from_str::<serde_json::Value>(line) {
            audits.push(SecurityAuditItem {
                id: audit_data
                    .get("id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                tool: audit_data
                    .get("tool")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                timestamp: audit_data
                    .get("timestamp")
                    .and_then(|v| v.as_f64())
                    .unwrap_or(0.0),
                status: audit_data
                    .get("status")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                findings_count: audit_data
                    .get("findings_count")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0) as usize,
                summary: audit_data
                    .get("summary")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
            });
        }
    }
    Ok(audits)
}

fn fetch_config_sources(socket: &str) -> Result<Vec<ConfigSource>> {
    let response = uds_request(socket, "CONFIG LIST")?;

    let mut sources = Vec::new();
    for line in response.lines() {
        if line.trim().is_empty() {
            continue;
        }
        if let Ok(config_data) = serde_json::from_str::<serde_json::Value>(line) {
            let config_map = config_data
                .get("config")
                .and_then(|v| v.as_object())
                .map(|obj| {
                    obj.iter()
                        .map(|(k, v)| (k.clone(), v.as_str().unwrap_or("").to_string()))
                        .collect()
                })
                .unwrap_or_else(HashMap::new);

            sources.push(ConfigSource {
                name: config_data
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                source_type: config_data
                    .get("type")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                enabled: config_data
                    .get("enabled")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false),
                config: config_map,
            });
        }
    }
    Ok(sources)
}

fn fetch_system_health(socket: &str) -> Result<Option<SystemHealth>> {
    let response = uds_request(socket, "HEALTH")?;

    for line in response.lines() {
        if line.trim().is_empty() {
            continue;
        }
        if let Ok(health_data) = serde_json::from_str::<serde_json::Value>(line) {
            let service_status = health_data
                .get("services")
                .and_then(|v| v.as_object())
                .map(|obj| {
                    obj.iter()
                        .map(|(k, v)| (k.clone(), v.as_bool().unwrap_or(false)))
                        .collect()
                })
                .unwrap_or_else(HashMap::new);

            return Ok(Some(SystemHealth {
                cpu_percent: health_data
                    .get("cpu_percent")
                    .and_then(|v| v.as_f64())
                    .unwrap_or(0.0),
                memory_percent: health_data
                    .get("memory_percent")
                    .and_then(|v| v.as_f64())
                    .unwrap_or(0.0),
                disk_percent: health_data
                    .get("disk_percent")
                    .and_then(|v| v.as_f64())
                    .unwrap_or(0.0),
                uptime_seconds: health_data
                    .get("uptime_seconds")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0),
                load_average: (
                    health_data
                        .get("load_1m")
                        .and_then(|v| v.as_f64())
                        .unwrap_or(0.0),
                    health_data
                        .get("load_5m")
                        .and_then(|v| v.as_f64())
                        .unwrap_or(0.0),
                    health_data
                        .get("load_15m")
                        .and_then(|v| v.as_f64())
                        .unwrap_or(0.0),
                ),
                network_connections: health_data
                    .get("network_connections")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0) as usize,
                service_status,
            }));
        }
    }
    Ok(None)
}

fn search_semantic(
    socket: &str,
    query: &str,
    n_results: usize,
    since: Option<u64>,
) -> Result<Vec<(LogItem, f64)>> {
    let cmd = if let Some(s) = since {
        format!(
            "SEARCH query={} n_results={} since={}",
            urlencoding::encode(query),
            n_results,
            s
        )
    } else {
        format!(
            "SEARCH query={} n_results={}",
            urlencoding::encode(query),
            n_results
        )
    };

    let response = uds_request(socket, &cmd)?;
    let mut results = Vec::new();

    for line in response.lines() {
        if line.trim().is_empty() {
            continue;
        }
        if let Ok(result_data) = serde_json::from_str::<serde_json::Value>(line) {
            let log_item = LogItem {
                ts: result_data
                    .get("ts")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                hostname: result_data
                    .get("hostname")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                unit: result_data
                    .get("unit")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                severity: result_data
                    .get("severity")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                source: result_data
                    .get("source")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                message: result_data
                    .get("message")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                fingerprint: result_data
                    .get("fingerprint")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string()),
            };
            let similarity = result_data
                .get("similarity")
                .and_then(|v| v.as_f64())
                .unwrap_or(0.0);
            results.push((log_item, similarity));
        }
    }
    Ok(results)
}

fn send_chat_message(socket: &str, message: &str) -> Result<ChatMessage> {
    let cmd = format!("CHAT query=\"{}\" context_size=5", message);
    let response = uds_request(socket, &cmd)?;

    if let Some(line) = response.lines().next() {
        if let Ok(chat_data) = serde_json::from_str::<serde_json::Value>(line) {
            return Ok(ChatMessage {
                role: "assistant".to_string(),
                content: chat_data
                    .get("response")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                timestamp: SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_secs_f64(),
                confidence: chat_data.get("confidence").and_then(|v| v.as_f64()),
                sources_count: chat_data
                    .get("sources_count")
                    .and_then(|v| v.as_u64())
                    .map(|v| v as usize),
            });
        }
    }

    Err(anyhow::anyhow!("Failed to parse chat response"))
}

// Action functions
fn trigger_ingest(socket: &str, seconds: u64, limit: Option<usize>) -> Result<String> {
    let cmd = if let Some(l) = limit {
        format!("INGEST_JOURNAL {} {}", seconds, l)
    } else {
        format!("INGEST_JOURNAL {}", seconds)
    };
    uds_request(socket, &cmd)
}

fn trigger_full_ingest(socket: &str) -> Result<String> {
    uds_request(socket, "INGEST_ALL")
}

fn collect_metrics(socket: &str) -> Result<String> {
    uds_request(socket, "COLLECT_METRICS")
}

fn generate_report(socket: &str, since: u64, format: &str) -> Result<String> {
    let cmd = format!("REPORT GENERATE since={} format={}", since, format);
    uds_request(socket, &cmd)
}

fn trigger_indexing(socket: &str, since: u64, limit: Option<usize>) -> Result<String> {
    let cmd = if let Some(l) = limit {
        format!("INDEX since={} limit={}", since, l)
    } else {
        format!("INDEX since={}", since)
    };
    uds_request(socket, &cmd)
}

fn run_security_audit(socket: &str, tool: Option<&str>) -> Result<String> {
    let cmd = if let Some(t) = tool {
        format!("AUDIT TOOL tool={}", t)
    } else {
        "AUDIT FULL".to_string()
    };
    uds_request(socket, &cmd)
}

// UI rendering functions
fn ui(f: &mut Frame, app: &mut App) {
    let size = f.size();

    // Create main layout
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3), // Tabs
            Constraint::Min(1),    // Main content
            Constraint::Length(3), // Status bar
        ])
        .split(size);

    // Render tabs
    let tab_titles = vec![
        "Dashboard",
        "Logs",
        "Search",
        "Analytics",
        "Health",
        "Chat",
        "Reports",
        "Security",
        "Config",
        "Help",
    ];
    let tabs = Tabs::new(
        tab_titles
            .iter()
            .cloned()
            .map(Span::from)
            .collect::<Vec<_>>(),
    )
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title("Chimera LogMind"),
    )
    .select(app.tab_index)
    .style(Style::default().fg(Color::Cyan))
    .highlight_style(
        Style::default()
            .fg(Color::Yellow)
            .add_modifier(Modifier::BOLD),
    );
    f.render_widget(tabs, chunks[0]);

    // Render main content based on selected tab
    match app.tab_index {
        0 => render_dashboard(f, chunks[1], app),
        1 => render_logs(f, chunks[1], app),
        2 => render_search(f, chunks[1], app),
        3 => render_analytics(f, chunks[1], app),
        4 => render_health(f, chunks[1], app),
        5 => render_chat(f, chunks[1], app),
        6 => render_reports(f, chunks[1], app),
        7 => render_security(f, chunks[1], app),
        8 => render_config(f, chunks[1], app),
        9 => render_help(f, chunks[1], app),
        _ => {}
    }

    // Render status bar
    let status_text = if app.input_mode != InputMode::Normal {
        format!(
            "{} | Mode: {:?} | Input: {}",
            app.status, app.input_mode, app.input_buffer
        )
    } else {
        format!(
            "{} | Auto-refresh: {} | Last update: {:.1}s ago",
            app.status,
            if app.auto_refresh { "ON" } else { "OFF" },
            app.last_refresh.elapsed().as_secs_f64()
        )
    };

    let status_bar = Paragraph::new(status_text)
        .block(Block::default().borders(Borders::ALL))
        .style(Style::default().fg(Color::White));
    f.render_widget(status_bar, chunks[2]);

    // Render popups
    if app.show_help {
        render_help_popup(f, size);
    }

    if let Some(error) = &app.show_error {
        render_error_popup(f, size, error);
    }
}

fn render_dashboard(f: &mut Frame, area: Rect, app: &mut App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Percentage(25),
            Constraint::Percentage(50),
            Constraint::Percentage(25),
        ])
        .split(area);

    // System overview
    if let Some(health) = &app.system_health {
        let overview_chunks = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([
                Constraint::Percentage(25),
                Constraint::Percentage(25),
                Constraint::Percentage(25),
                Constraint::Percentage(25),
            ])
            .split(chunks[0]);

        let cpu_gauge = Gauge::default()
            .block(Block::default().borders(Borders::ALL).title("CPU"))
            .gauge_style(Style::default().fg(Color::Cyan))
            .percent((health.cpu_percent * 100.0) as u16);
        f.render_widget(cpu_gauge, overview_chunks[0]);

        let memory_gauge = Gauge::default()
            .block(Block::default().borders(Borders::ALL).title("Memory"))
            .gauge_style(Style::default().fg(Color::Green))
            .percent((health.memory_percent * 100.0) as u16);
        f.render_widget(memory_gauge, overview_chunks[1]);

        let disk_gauge = Gauge::default()
            .block(Block::default().borders(Borders::ALL).title("Disk"))
            .gauge_style(Style::default().fg(Color::Yellow))
            .percent((health.disk_percent * 100.0) as u16);
        f.render_widget(disk_gauge, overview_chunks[2]);

        let uptime_text = format!(
            "Uptime: {}d {}h",
            health.uptime_seconds / 86400,
            (health.uptime_seconds % 86400) / 3600
        );
        let uptime_widget = Paragraph::new(uptime_text)
            .block(Block::default().borders(Borders::ALL).title("System"))
            .alignment(Alignment::Center);
        f.render_widget(uptime_widget, overview_chunks[3]);
    }

    // Recent activity
    let activity_chunks = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
        .split(chunks[1]);

    // Recent logs
    let log_items: Vec<ListItem> = app
        .logs
        .iter()
        .take(10)
        .map(|log| {
            let severity_style = match log.severity.as_str() {
                "ERROR" | "CRITICAL" => Style::default().fg(Color::Red),
                "WARNING" => Style::default().fg(Color::Yellow),
                "INFO" => Style::default().fg(Color::Green),
                _ => Style::default().fg(Color::White),
            };
            ListItem::new(format!(
                "{} [{}] {}: {}",
                &log.ts[11..19],
                log.severity,
                log.unit,
                if log.message.len() > 40 {
                    format!("{}...", &log.message[..40])
                } else {
                    log.message.clone()
                }
            ))
            .style(severity_style)
        })
        .collect();

    let logs_list = List::new(log_items)
        .block(Block::default().borders(Borders::ALL).title("Recent Logs"))
        .highlight_style(Style::default().add_modifier(Modifier::BOLD));
    f.render_widget(logs_list, activity_chunks[0]);

    // Recent alerts
    let alert_items: Vec<ListItem> = app
        .alerts
        .iter()
        .take(10)
        .map(|alert| {
            let severity_style = match alert.severity.as_str() {
                "CRITICAL" => Style::default().fg(Color::Red),
                "HIGH" => Style::default().fg(Color::Magenta),
                "MEDIUM" => Style::default().fg(Color::Yellow),
                _ => Style::default().fg(Color::Green),
            };
            let ack_marker = if alert.acknowledged { "✓" } else { "!" };
            ListItem::new(format!(
                "{} {} [{}] {}",
                ack_marker,
                &alert.id[..8],
                alert.severity,
                if alert.message.len() > 35 {
                    format!("{}...", &alert.message[..35])
                } else {
                    alert.message.clone()
                }
            ))
            .style(severity_style)
        })
        .collect();

    let alerts_list = List::new(alert_items)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Recent Alerts"),
        )
        .highlight_style(Style::default().add_modifier(Modifier::BOLD));
    f.render_widget(alerts_list, activity_chunks[1]);

    // Quick stats
    let stats_text = format!(
        "Logs: {} | Alerts: {} | Anomalies: {} | Reports: {} | Last Refresh: {:.1}s ago",
        app.logs.len(),
        app.alerts.len(),
        app.anomalies.len(),
        app.reports.len(),
        app.last_refresh.elapsed().as_secs_f64()
    );
    let stats_widget = Paragraph::new(stats_text)
        .block(Block::default().borders(Borders::ALL).title("Statistics"))
        .alignment(Alignment::Center);
    f.render_widget(stats_widget, chunks[2]);
}

fn render_logs(f: &mut Frame, area: Rect, app: &mut App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(3), Constraint::Min(1)])
        .split(area);

    // Filter/search bar
    let filter_text = if app.input_mode == InputMode::Search {
        format!("Filter: {}_", app.input_buffer)
    } else {
        "Press '/' to filter logs, 'i' for quick ingest, 'I' for full ingest".to_string()
    };
    let filter_widget = Paragraph::new(filter_text)
        .block(Block::default().borders(Borders::ALL).title("Log Filters"));
    f.render_widget(filter_widget, chunks[0]);

    // Logs list
    let log_items: Vec<ListItem> = app
        .logs
        .iter()
        .enumerate()
        .map(|(i, log)| {
            let severity_style = match log.severity.as_str() {
                "ERROR" | "CRITICAL" => Style::default().fg(Color::Red),
                "WARNING" => Style::default().fg(Color::Yellow),
                "INFO" => Style::default().fg(Color::Green),
                "DEBUG" => Style::default().fg(Color::Blue),
                _ => Style::default().fg(Color::White),
            };

            let selected_style = if i == app.selected_log {
                severity_style
                    .add_modifier(Modifier::BOLD)
                    .bg(Color::DarkGray)
            } else {
                severity_style
            };

            ListItem::new(format!(
                "{} {} [{}] {}@{}: {}",
                &log.ts, log.severity, log.source, log.unit, log.hostname, log.message
            ))
            .style(selected_style)
        })
        .collect();

    let logs_list = List::new(log_items)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(format!("Logs ({} total)", app.logs.len())),
        )
        .highlight_style(Style::default().add_modifier(Modifier::BOLD));
    f.render_stateful_widget(logs_list, chunks[1], &mut app.log_list_state);
}

fn render_search(f: &mut Frame, area: Rect, app: &mut App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(1),
            Constraint::Length(3),
        ])
        .split(area);

    // Search input
    let search_text = if app.input_mode == InputMode::Search {
        format!("Query: {}_", app.input_buffer)
    } else {
        format!("Query: {} (Press '/' to edit)", app.search_query)
    };
    let search_widget = Paragraph::new(search_text).block(
        Block::default()
            .borders(Borders::ALL)
            .title("Semantic Search"),
    );
    f.render_widget(search_widget, chunks[0]);

    // Search results
    let result_items: Vec<ListItem> = app
        .search_results
        .iter()
        .map(|(log, similarity)| {
            let similarity_color = if *similarity > 0.8 {
                Color::Green
            } else if *similarity > 0.6 {
                Color::Yellow
            } else {
                Color::Red
            };

            ListItem::new(format!(
                "{:.3} | {} [{}] {}: {}",
                similarity,
                &log.ts,
                log.severity,
                log.unit,
                if log.message.len() > 60 {
                    format!("{}...", &log.message[..60])
                } else {
                    log.message.clone()
                }
            ))
            .style(Style::default().fg(similarity_color))
        })
        .collect();

    let results_list = List::new(result_items).block(
        Block::default()
            .borders(Borders::ALL)
            .title(format!("Results ({} found)", app.search_results.len())),
    );
    f.render_widget(results_list, chunks[1]);

    // Search controls
    let controls_text = "Enter: Search | Esc: Clear | ↑/↓: Navigate | 'n': Index embeddings";
    let controls_widget = Paragraph::new(controls_text)
        .block(Block::default().borders(Borders::ALL).title("Controls"));
    f.render_widget(controls_widget, chunks[2]);
}

fn render_analytics(f: &mut Frame, area: Rect, app: &mut App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
        .split(area);

    let top_chunks = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
        .split(chunks[0]);

    // Anomalies
    let anomaly_items: Vec<ListItem> = app
        .anomalies
        .iter()
        .map(|anomaly| {
            let score_color = if anomaly.anomaly_score > 0.8 {
                Color::Red
            } else if anomaly.anomaly_score > 0.6 {
                Color::Yellow
            } else {
                Color::Green
            };

            let timestamp_str =
                SystemTime::UNIX_EPOCH + Duration::from_secs(anomaly.timestamp as u64);
            let time_ago = timestamp_str.elapsed().unwrap_or(Duration::ZERO).as_secs();

            ListItem::new(format!(
                "{:.3} {}s ago [{}] {}: {}",
                anomaly.anomaly_score, time_ago, anomaly.severity, anomaly.unit, anomaly.message
            ))
            .style(Style::default().fg(score_color))
        })
        .collect();

    let anomalies_list = List::new(anomaly_items).block(
        Block::default()
            .borders(Borders::ALL)
            .title(format!("Anomalies ({} detected)", app.anomalies.len())),
    );
    f.render_widget(anomalies_list, top_chunks[0]);

    // Comprehensive metrics display
    let metrics_text = if app.metrics.is_empty() {
        "No metrics data available\nPress 'm' to collect metrics".to_string()
    } else {
        let cpu_metrics: Vec<_> = app
            .metrics
            .iter()
            .filter(|m| m.metric_type == "cpu_percent")
            .collect();
        let memory_metrics: Vec<_> = app
            .metrics
            .iter()
            .filter(|m| m.metric_type == "memory_percent")
            .collect();
        let disk_metrics: Vec<_> = app
            .metrics
            .iter()
            .filter(|m| m.metric_type == "disk_percent")
            .collect();

        let cpu_avg = if !cpu_metrics.is_empty() {
            cpu_metrics
                .iter()
                .map(|m| m.value)
                .fold(0.0, |acc, x| acc + x)
                / cpu_metrics.len() as f64
        } else {
            0.0
        };

        let memory_avg = if !memory_metrics.is_empty() {
            memory_metrics
                .iter()
                .map(|m| m.value)
                .fold(0.0, |acc, x| acc + x)
                / memory_metrics.len() as f64
        } else {
            0.0
        };

        let disk_avg = if !disk_metrics.is_empty() {
            disk_metrics
                .iter()
                .map(|m| m.value)
                .fold(0.0, |acc, x| acc + x)
                / disk_metrics.len() as f64
        } else {
            0.0
        };

        let latest_metric = app.metrics.first();
        let hostname = latest_metric
            .map(|m| m.hostname.as_str())
            .unwrap_or("unknown");

        let latest_timestamp = latest_metric
            .map(|m| {
                let ts = SystemTime::UNIX_EPOCH + Duration::from_secs(m.timestamp as u64);
                ts.elapsed().unwrap_or(Duration::ZERO).as_secs()
            })
            .unwrap_or(0);

        let cpu_unit = cpu_metrics.first().map(|m| m.unit.as_str()).unwrap_or("%");
        let memory_unit = memory_metrics
            .first()
            .map(|m| m.unit.as_str())
            .unwrap_or("%");
        let disk_unit = disk_metrics.first().map(|m| m.unit.as_str()).unwrap_or("%");

        format!(
            "Metrics Summary ({}s ago):\nCPU Avg: {:.1}{} | Memory Avg: {:.1}{}\nDisk Avg: {:.1}{} | Host: {}\nData Points: {} | Press 'm' to refresh",
            latest_timestamp,
            cpu_avg,
            cpu_unit,
            memory_avg,
            memory_unit,
            disk_avg,
            disk_unit,
            hostname,
            app.metrics.len()
        )
    };

    let metrics_widget = Paragraph::new(metrics_text)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Metrics Overview"),
        )
        .wrap(Wrap { trim: true });
    f.render_widget(metrics_widget, top_chunks[1]);

    // Timeline view (placeholder)
    let timeline_text = "Timeline Analysis:\n• Log patterns over time\n• Anomaly correlation\n• System events mapping\n\nUse 't' to generate timeline report";
    let timeline_widget = Paragraph::new(timeline_text)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Timeline Analysis"),
        )
        .wrap(Wrap { trim: true });
    f.render_widget(timeline_widget, chunks[1]);
}

fn render_health(f: &mut Frame, area: Rect, app: &mut App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Percentage(40), Constraint::Percentage(60)])
        .split(area);

    // System health overview
    if let Some(health) = &app.system_health {
        let health_chunks = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Percentage(60), Constraint::Percentage(40)])
            .split(chunks[0]);

        // Resource gauges
        let gauge_chunks = Layout::default()
            .direction(Direction::Vertical)
            .constraints([
                Constraint::Percentage(33),
                Constraint::Percentage(33),
                Constraint::Percentage(34),
            ])
            .split(health_chunks[0]);

        let cpu_gauge = Gauge::default()
            .block(Block::default().borders(Borders::ALL).title("CPU Usage"))
            .gauge_style(Style::default().fg(if health.cpu_percent > 0.8 {
                Color::Red
            } else if health.cpu_percent > 0.6 {
                Color::Yellow
            } else {
                Color::Green
            }))
            .percent((health.cpu_percent * 100.0) as u16);
        f.render_widget(cpu_gauge, gauge_chunks[0]);

        let memory_gauge = Gauge::default()
            .block(Block::default().borders(Borders::ALL).title("Memory Usage"))
            .gauge_style(Style::default().fg(if health.memory_percent > 0.9 {
                Color::Red
            } else if health.memory_percent > 0.7 {
                Color::Yellow
            } else {
                Color::Green
            }))
            .percent((health.memory_percent * 100.0) as u16);
        f.render_widget(memory_gauge, gauge_chunks[1]);

        let disk_gauge = Gauge::default()
            .block(Block::default().borders(Borders::ALL).title("Disk Usage"))
            .gauge_style(Style::default().fg(if health.disk_percent > 0.9 {
                Color::Red
            } else if health.disk_percent > 0.8 {
                Color::Yellow
            } else {
                Color::Green
            }))
            .percent((health.disk_percent * 100.0) as u16);
        f.render_widget(disk_gauge, gauge_chunks[2]);

        // System info
        let system_info = format!(
            "Load Average: {:.2}, {:.2}, {:.2}\nUptime: {}d {}h {}m\nNetwork Connections: {}\nServices: {} running",
            health.load_average.0, health.load_average.1, health.load_average.2,
            health.uptime_seconds / 86400,
            (health.uptime_seconds % 86400) / 3600,
            (health.uptime_seconds % 3600) / 60,
            health.network_connections,
            health.service_status.values().filter(|&&v| v).count()
        );
        let system_widget = Paragraph::new(system_info)
            .block(Block::default().borders(Borders::ALL).title("System Info"))
            .wrap(Wrap { trim: true });
        f.render_widget(system_widget, health_chunks[1]);
    }

    // Alerts
    let alert_items: Vec<ListItem> = app
        .alerts
        .iter()
        .map(|alert| {
            let severity_style = match alert.severity.as_str() {
                "CRITICAL" => Style::default().fg(Color::Red).add_modifier(Modifier::BOLD),
                "HIGH" => Style::default().fg(Color::Magenta),
                "MEDIUM" => Style::default().fg(Color::Yellow),
                "LOW" => Style::default().fg(Color::Blue),
                _ => Style::default().fg(Color::White),
            };

            let ack_marker = if alert.acknowledged { "✓" } else { "!" };
            let timestamp = SystemTime::UNIX_EPOCH + Duration::from_secs(alert.timestamp as u64);
            let datetime = timestamp.elapsed().unwrap_or(Duration::ZERO).as_secs();

            ListItem::new(format!(
                "{} {}s ago [{}] {}: {}",
                ack_marker, datetime, alert.severity, alert.source, alert.message
            ))
            .style(severity_style)
        })
        .collect();

    let alerts_list = List::new(alert_items)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(format!("Alerts ({} active)", app.alerts.len())),
        )
        .highlight_style(Style::default().add_modifier(Modifier::BOLD));
    f.render_stateful_widget(alerts_list, chunks[1], &mut app.alert_list_state);
}

fn render_chat(f: &mut Frame, area: Rect, app: &mut App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(1), Constraint::Length(3)])
        .split(area);

    // Chat history
    let chat_items: Vec<ListItem> = app
        .chat_messages
        .iter()
        .map(|msg| {
            let role_style = if msg.role == "user" {
                Style::default().fg(Color::Cyan)
            } else {
                Style::default().fg(Color::Green)
            };

            let confidence_info = if let Some(conf) = msg.confidence {
                format!(" (conf: {:.2})", conf)
            } else {
                String::new()
            };

            let sources_info = if let Some(count) = msg.sources_count {
                format!(" [{}src]", count)
            } else {
                String::new()
            };

            let timestamp_str = SystemTime::UNIX_EPOCH + Duration::from_secs(msg.timestamp as u64);
            let time_ago = timestamp_str.elapsed().unwrap_or(Duration::ZERO).as_secs();

            ListItem::new(format!(
                "[{}] {}s ago{}{}: {}",
                msg.role,
                time_ago,
                confidence_info,
                sources_info,
                if msg.content.len() > 100 {
                    format!("{}...", &msg.content[..100])
                } else {
                    msg.content.clone()
                }
            ))
            .style(role_style)
        })
        .collect();

    let chat_list = List::new(chat_items).block(Block::default().borders(Borders::ALL).title(
        format!("RAG Chat History ({} messages)", app.chat_messages.len()),
    ));
    f.render_widget(chat_list, chunks[0]);

    // Chat input
    let input_text = if app.input_mode == InputMode::Chat {
        format!("> {}_", app.input_buffer)
    } else {
        "Press 'c' to start typing a message, 'C' to clear history...".to_string()
    };
    let input_widget = Paragraph::new(input_text)
        .block(Block::default().borders(Borders::ALL).title("Chat Input"));
    f.render_widget(input_widget, chunks[1]);
}

fn render_reports(f: &mut Frame, area: Rect, app: &mut App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(1), Constraint::Length(5)])
        .split(area);

    // Reports list
    let report_items: Vec<ListItem> = app
        .reports
        .iter()
        .enumerate()
        .map(|(i, report)| {
            let selected_style = if i == app.selected_report {
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(Color::White)
            };

            ListItem::new(format!(
                "{} | {} | {} | {}KB | {}",
                report.id,
                report.title,
                report.format,
                report.size_bytes / 1024,
                report.generated_at
            ))
            .style(selected_style)
        })
        .collect();

    let reports_list = List::new(report_items)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(format!("Generated Reports ({} total)", app.reports.len())),
        )
        .highlight_style(Style::default().add_modifier(Modifier::BOLD));
    f.render_stateful_widget(reports_list, chunks[0], &mut app.report_list_state);

    // Controls
    let controls_text = "Controls:\n'g': Generate report (24h) | 'G': Generate report (7d) | 'h': Generate HTML report\n'e': Email selected report | 'v': View selected report | 'x': Delete selected report\n'r': Refresh list | Enter: View details";
    let controls_widget = Paragraph::new(controls_text)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Report Controls"),
        )
        .wrap(Wrap { trim: true });
    f.render_widget(controls_widget, chunks[1]);
}

fn render_security(f: &mut Frame, area: Rect, app: &mut App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(1), Constraint::Length(5)])
        .split(area);

    // Security audits list
    let audit_items: Vec<ListItem> = app
        .security_audits
        .iter()
        .enumerate()
        .map(|(i, audit)| {
            let selected_style = if i == app.selected_audit {
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(Color::White)
            };

            let status_color = match audit.status.as_str() {
                "COMPLETED" => Color::Green,
                "FAILED" => Color::Red,
                "RUNNING" => Color::Yellow,
                _ => Color::Gray,
            };

            let timestamp_str =
                SystemTime::UNIX_EPOCH + Duration::from_secs(audit.timestamp as u64);
            let time_ago = timestamp_str.elapsed().unwrap_or(Duration::ZERO).as_secs();

            ListItem::new(format!(
                "{} | {} | {}s ago | {} findings | {} | ID:{}",
                audit.tool,
                audit.status,
                time_ago,
                audit.findings_count,
                audit.summary,
                &audit.id[..8]
            ))
            .style(selected_style.fg(status_color))
        })
        .collect();

    let audits_list = List::new(audit_items)
        .block(Block::default().borders(Borders::ALL).title(format!(
            "Security Audits ({} total)",
            app.security_audits.len()
        )))
        .highlight_style(Style::default().add_modifier(Modifier::BOLD));
    f.render_stateful_widget(audits_list, chunks[0], &mut app.audit_list_state);

    // Controls
    let controls_text = "Security Controls:\n'f': Full security audit | 'a': Run AIDE | 'r': Run rkhunter | 'c': Run ClamAV\n'l': Run Lynis | 's': Run OpenSCAP | 'k': Run chkrootkit | Enter: View audit details\n'R': Refresh audit history";
    let controls_widget = Paragraph::new(controls_text)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Security Controls"),
        )
        .wrap(Wrap { trim: true });
    f.render_widget(controls_widget, chunks[1]);
}

fn render_config(f: &mut Frame, area: Rect, app: &mut App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(1), Constraint::Length(4)])
        .split(area);

    // Configuration sources
    let config_items: Vec<ListItem> = app
        .config_sources
        .iter()
        .enumerate()
        .map(|(i, source)| {
            let selected_style = if i == app.selected_config {
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(Color::White)
            };

            let enabled_color = if source.enabled {
                Color::Green
            } else {
                Color::Red
            };
            let enabled_marker = if source.enabled { "✓" } else { "✗" };

            ListItem::new(format!(
                "{} {} | {} | {} | {} configs",
                enabled_marker,
                source.name,
                source.source_type,
                if source.enabled {
                    "ENABLED"
                } else {
                    "DISABLED"
                },
                source.config.len()
            ))
            .style(selected_style.fg(enabled_color))
        })
        .collect();

    let config_list = List::new(config_items)
        .block(Block::default().borders(Borders::ALL).title(format!(
            "Log Sources ({} configured)",
            app.config_sources.len()
        )))
        .highlight_style(Style::default().add_modifier(Modifier::BOLD));
    f.render_stateful_widget(config_list, chunks[0], &mut app.config_list_state);

    // Controls
    let controls_text = "Configuration Controls:\n'e': Enable/disable source | 'd': Delete source | 'n': Add new source | Enter: Edit source\n'r': Refresh sources | 's': Save configuration";
    let controls_widget = Paragraph::new(controls_text)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Configuration Controls"),
        )
        .wrap(Wrap { trim: true });
    f.render_widget(controls_widget, chunks[1]);
}

fn render_help(f: &mut Frame, area: Rect, _app: &mut App) {
    let help_text = r#"Chimera LogMind TUI - Keyboard Shortcuts

GLOBAL CONTROLS:
  q, Ctrl+c    : Quit application
  h, F1        : Toggle this help screen
  ←/→, Tab     : Switch between tabs
  r, F5        : Refresh current view
  Ctrl+r       : Toggle auto-refresh

TAB-SPECIFIC CONTROLS:

Dashboard (Tab 1):
  - Real-time system overview
  - Recent logs and alerts summary

Logs (Tab 2):
  ↑/↓          : Navigate log entries
  /            : Filter logs
  i            : Quick ingest (5 minutes)
  I            : Full ingest (1 hour)
  Enter        : View log details

Search (Tab 3):
  /            : Enter search query
  Enter        : Execute semantic search
  n            : Index embeddings
  Esc          : Clear search

Analytics (Tab 4):
  m            : Collect metrics
  t            : Generate timeline report
  a            : Run anomaly detection

Health (Tab 5):
  ↑/↓          : Navigate alerts
  m            : Collect system metrics
  Enter        : Acknowledge alert

Chat (Tab 6):
  c            : Start chat input
  C            : Clear chat history
  Enter        : Send message
  Esc          : Cancel input

Reports (Tab 7):
  ↑/↓          : Navigate reports
  g            : Generate daily report
  G            : Generate weekly report
  h            : Generate HTML report
  e            : Email selected report
  v            : View selected report
  x            : Delete selected report

Security (Tab 8):
  ↑/↓          : Navigate audit results
  f            : Run full security audit
  a            : Run AIDE integrity check
  r            : Run rkhunter
  c            : Run ClamAV scan
  l            : Run Lynis audit
  s            : Run OpenSCAP scan
  k            : Run chkrootkit
  Enter        : View audit details

Config (Tab 9):
  ↑/↓          : Navigate config sources
  e            : Enable/disable source
  d            : Delete source
  n            : Add new source
  Enter        : Edit source
  s            : Save configuration

INPUT MODES:
  Normal       : Navigation and shortcuts
  Editing      : Text input for various fields
  Search       : Search query input
  Chat         : Chat message input
  Command      : Command input mode

Press 'h' or F1 to close this help screen."#;

    let help_widget = Paragraph::new(help_text)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Help - Keyboard Shortcuts"),
        )
        .wrap(Wrap { trim: true })
        .scroll((0, 0));
    f.render_widget(help_widget, area);
}

fn render_help_popup(f: &mut Frame, area: Rect) {
    let popup_area = centered_rect(80, 80, area);
    f.render_widget(Clear, popup_area);

    let help_text =
        "Quick Help:\nq: Quit | h: Help | ←/→: Switch tabs | r: Refresh\nPress 'h' again to close";
    let help_popup = Paragraph::new(help_text)
        .block(Block::default().borders(Borders::ALL).title("Quick Help"))
        .wrap(Wrap { trim: true });
    f.render_widget(help_popup, popup_area);
}

fn render_error_popup(f: &mut Frame, area: Rect, error: &str) {
    let popup_area = centered_rect(60, 20, area);
    f.render_widget(Clear, popup_area);

    let error_popup = Paragraph::new(format!("Error: {}\n\nPress any key to dismiss", error))
        .block(Block::default().borders(Borders::ALL).title("Error"))
        .style(Style::default().fg(Color::Red))
        .wrap(Wrap { trim: true });
    f.render_widget(error_popup, popup_area);
}

fn centered_rect(percent_x: u16, percent_y: u16, r: Rect) -> Rect {
    let popup_layout = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Percentage((100 - percent_y) / 2),
            Constraint::Percentage(percent_y),
            Constraint::Percentage((100 - percent_y) / 2),
        ])
        .split(r);

    Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage((100 - percent_x) / 2),
            Constraint::Percentage(percent_x),
            Constraint::Percentage((100 - percent_x) / 2),
        ])
        .split(popup_layout[1])[1]
}

// Event handling
fn handle_key_event(app: &mut App, key: event::KeyEvent, socket: &str) -> Result<()> {
    // Handle popups first
    if app.show_help {
        app.show_help = false;
        return Ok(());
    }

    if app.show_error.is_some() {
        app.show_error = None;
        return Ok(());
    }

    // Handle input modes
    match app.input_mode {
        InputMode::Search | InputMode::Chat => {
            match key.code {
                KeyCode::Enter => {
                    let input = app.input_buffer.clone();
                    app.input_buffer.clear();

                    match app.input_mode {
                        InputMode::Search => {
                            app.search_query = input.clone();
                            if !input.is_empty() {
                                match search_semantic(socket, &input, 20, Some(86400)) {
                                    Ok(results) => {
                                        app.search_results = results;
                                        app.status = "Search completed".to_string();
                                    }
                                    Err(e) => {
                                        app.show_error = Some(format!("Search failed: {}", e))
                                    }
                                }
                            }
                            app.input_mode = InputMode::Normal;
                        }
                        InputMode::Chat => {
                            if !input.is_empty() {
                                // Add user message
                                app.chat_messages.push(ChatMessage {
                                    role: "user".to_string(),
                                    content: input.clone(),
                                    timestamp: SystemTime::now()
                                        .duration_since(UNIX_EPOCH)
                                        .unwrap()
                                        .as_secs_f64(),
                                    confidence: None,
                                    sources_count: None,
                                });

                                // Send to backend and get response
                                match send_chat_message(socket, &input) {
                                    Ok(response) => {
                                        app.chat_messages.push(response);
                                        app.status = "Message sent successfully".to_string();
                                    }
                                    Err(e) => app.show_error = Some(format!("Chat failed: {}", e)),
                                }
                            }
                            app.input_mode = InputMode::Normal;
                        }
                        _ => app.input_mode = InputMode::Normal,
                    }
                }
                KeyCode::Esc => {
                    app.input_buffer.clear();
                    app.input_mode = InputMode::Normal;
                }
                KeyCode::Backspace => {
                    app.input_buffer.pop();
                }
                KeyCode::Char(c) => {
                    app.input_buffer.push(c);
                }
                _ => {}
            }
            return Ok(());
        }
        _ => {}
    }

    // Global shortcuts
    match key.code {
        KeyCode::Char('q') | KeyCode::Char('Q') => {
            app.should_quit = true;
            return Ok(());
        }
        KeyCode::Char('h') | KeyCode::F(1) => {
            app.show_help = !app.show_help;
            return Ok(());
        }
        KeyCode::Left | KeyCode::Char('\t') if key.modifiers.contains(KeyModifiers::SHIFT) => {
            app.prev_tab();
            return Ok(());
        }
        KeyCode::Right | KeyCode::Char('\t') => {
            app.next_tab();
            return Ok(());
        }
        KeyCode::Char('r') | KeyCode::F(5) => {
            refresh_data(app, socket)?;
            return Ok(());
        }
        KeyCode::Char('R') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            app.auto_refresh = !app.auto_refresh;
            app.status = format!(
                "Auto-refresh: {}",
                if app.auto_refresh { "ON" } else { "OFF" }
            );
            return Ok(());
        }
        _ => {}
    }

    // Tab-specific shortcuts
    match app.tab_index {
        1 => handle_logs_keys(app, key, socket)?,      // Logs
        2 => handle_search_keys(app, key, socket)?,    // Search
        3 => handle_analytics_keys(app, key, socket)?, // Analytics
        4 => handle_health_keys(app, key, socket)?,    // Health
        5 => handle_chat_keys(app, key, socket)?,      // Chat
        6 => handle_reports_keys(app, key, socket)?,   // Reports
        7 => handle_security_keys(app, key, socket)?,  // Security
        8 => handle_config_keys(app, key, socket)?,    // Config
        _ => {}
    }

    // Navigation keys
    match key.code {
        KeyCode::Up => app.prev_item(),
        KeyCode::Down => app.next_item(),
        _ => {}
    }

    Ok(())
}

fn handle_logs_keys(app: &mut App, key: event::KeyEvent, socket: &str) -> Result<()> {
    match key.code {
        KeyCode::Char('/') => {
            app.input_mode = InputMode::Search;
            app.input_buffer.clear();
        }
        KeyCode::Char('i') => match trigger_ingest(socket, 300, Some(500)) {
            Ok(resp) => app.status = format!("Quick ingest: {}", resp.trim()),
            Err(e) => app.show_error = Some(format!("Ingest failed: {}", e)),
        },
        KeyCode::Char('I') => match trigger_full_ingest(socket) {
            Ok(resp) => app.status = format!("Full ingest: {}", resp.trim()),
            Err(e) => app.show_error = Some(format!("Full ingest failed: {}", e)),
        },
        _ => {}
    }
    Ok(())
}

fn handle_search_keys(app: &mut App, key: event::KeyEvent, socket: &str) -> Result<()> {
    match key.code {
        KeyCode::Char('/') => {
            app.input_mode = InputMode::Search;
            app.input_buffer = app.search_query.clone();
        }
        KeyCode::Char('n') => match trigger_indexing(socket, 86400, None) {
            Ok(resp) => app.status = format!("Indexing: {}", resp.trim()),
            Err(e) => app.show_error = Some(format!("Indexing failed: {}", e)),
        },
        KeyCode::Esc => {
            app.search_results.clear();
            app.search_query.clear();
        }
        _ => {}
    }
    Ok(())
}

fn handle_analytics_keys(app: &mut App, key: event::KeyEvent, socket: &str) -> Result<()> {
    match key.code {
        KeyCode::Char('m') => match collect_metrics(socket) {
            Ok(resp) => app.status = format!("Metrics: {}", resp.trim()),
            Err(e) => app.show_error = Some(format!("Metrics collection failed: {}", e)),
        },
        KeyCode::Char('a') => match uds_request(socket, "ANOMALIES since=86400") {
            Ok(_) => app.status = "Anomaly detection completed".to_string(),
            Err(e) => app.show_error = Some(format!("Anomaly detection failed: {}", e)),
        },
        _ => {}
    }
    Ok(())
}

fn handle_health_keys(app: &mut App, key: event::KeyEvent, socket: &str) -> Result<()> {
    match key.code {
        KeyCode::Char('m') => match collect_metrics(socket) {
            Ok(resp) => app.status = format!("Metrics collected: {}", resp.trim()),
            Err(e) => app.show_error = Some(format!("Metrics collection failed: {}", e)),
        },
        _ => {}
    }
    Ok(())
}

fn handle_chat_keys(app: &mut App, key: event::KeyEvent, _socket: &str) -> Result<()> {
    match key.code {
        KeyCode::Char('c') => {
            app.input_mode = InputMode::Chat;
            app.input_buffer.clear();
        }
        KeyCode::Char('C') => {
            app.chat_messages.clear();
            app.status = "Chat history cleared".to_string();
        }
        _ => {}
    }
    Ok(())
}

fn handle_reports_keys(app: &mut App, key: event::KeyEvent, socket: &str) -> Result<()> {
    match key.code {
        KeyCode::Char('g') => match generate_report(socket, 86400, "text") {
            Ok(_resp) => app.status = "Daily report generated".to_string(),
            Err(e) => app.show_error = Some(format!("Report generation failed: {}", e)),
        },
        KeyCode::Char('G') => match generate_report(socket, 604800, "text") {
            Ok(_resp) => app.status = "Weekly report generated".to_string(),
            Err(e) => app.show_error = Some(format!("Report generation failed: {}", e)),
        },
        KeyCode::Char('h') => match generate_report(socket, 86400, "html") {
            Ok(_resp) => app.status = "HTML report generated".to_string(),
            Err(e) => app.show_error = Some(format!("HTML report generation failed: {}", e)),
        },
        _ => {}
    }
    Ok(())
}

fn handle_security_keys(app: &mut App, key: event::KeyEvent, socket: &str) -> Result<()> {
    match key.code {
        KeyCode::Char('f') => match run_security_audit(socket, None) {
            Ok(_resp) => app.status = "Full security audit started".to_string(),
            Err(e) => app.show_error = Some(format!("Security audit failed: {}", e)),
        },
        KeyCode::Char('a') => match run_security_audit(socket, Some("aide")) {
            Ok(_resp) => app.status = "AIDE audit started".to_string(),
            Err(e) => app.show_error = Some(format!("AIDE audit failed: {}", e)),
        },
        KeyCode::Char('r') => match run_security_audit(socket, Some("rkhunter")) {
            Ok(_resp) => app.status = "rkhunter audit started".to_string(),
            Err(e) => app.show_error = Some(format!("rkhunter audit failed: {}", e)),
        },
        KeyCode::Char('c') => match run_security_audit(socket, Some("clamav")) {
            Ok(_resp) => app.status = "ClamAV scan started".to_string(),
            Err(e) => app.show_error = Some(format!("ClamAV scan failed: {}", e)),
        },
        KeyCode::Char('l') => match run_security_audit(socket, Some("lynis")) {
            Ok(_resp) => app.status = "Lynis audit started".to_string(),
            Err(e) => app.show_error = Some(format!("Lynis audit failed: {}", e)),
        },
        _ => {}
    }
    Ok(())
}

fn handle_config_keys(app: &mut App, key: event::KeyEvent, socket: &str) -> Result<()> {
    match key.code {
        KeyCode::Char('e') => {
            if let Some(source) = app.config_sources.get(app.selected_config) {
                let new_enabled = !source.enabled;
                let cmd = format!(
                    "CONFIG UPDATE_SOURCE name={} enabled={}",
                    source.name, new_enabled
                );
                match uds_request(socket, &cmd) {
                    Ok(_) => {
                        app.status = format!(
                            "Source {} {}",
                            source.name,
                            if new_enabled { "enabled" } else { "disabled" }
                        )
                    }
                    Err(e) => app.show_error = Some(format!("Config update failed: {}", e)),
                }
            }
        }
        KeyCode::Char('r') => match fetch_config_sources(socket) {
            Ok(sources) => {
                app.config_sources = sources;
                app.status = "Configuration refreshed".to_string();
            }
            Err(e) => app.show_error = Some(format!("Config refresh failed: {}", e)),
        },
        _ => {}
    }
    Ok(())
}

fn refresh_data(app: &mut App, socket: &str) -> Result<()> {
    app.last_refresh = Instant::now();

    // Refresh logs
    if let Ok(logs) = fetch_logs(socket, 3600, 200) {
        app.logs = logs;
        if app.selected_log >= app.logs.len() {
            app.selected_log = app.logs.len().saturating_sub(1);
        }
        app.log_list_state.select(if app.logs.is_empty() {
            None
        } else {
            Some(app.selected_log)
        });
    }

    // Refresh based on current tab
    match app.tab_index {
        0 | 4 => {
            // Dashboard or Health
            if let Ok(health) = fetch_system_health(socket) {
                app.system_health = health;
            }
            if let Ok(alerts) = fetch_alerts(socket, 3600, None) {
                app.alerts = alerts;
                if app.selected_alert >= app.alerts.len() {
                    app.selected_alert = app.alerts.len().saturating_sub(1);
                }
                app.alert_list_state.select(if app.alerts.is_empty() {
                    None
                } else {
                    Some(app.selected_alert)
                });
            }
        }
        3 => {
            // Analytics
            if let Ok(metrics) = fetch_metrics(socket, None, 3600, 100) {
                app.metrics = metrics;
            }
            if let Ok(anomalies) = fetch_anomalies(socket, 3600) {
                app.anomalies = anomalies;
            }
        }
        6 => {
            // Reports
            if let Ok(reports) = fetch_reports(socket, 20) {
                app.reports = reports;
                if app.selected_report >= app.reports.len() {
                    app.selected_report = app.reports.len().saturating_sub(1);
                }
                app.report_list_state.select(if app.reports.is_empty() {
                    None
                } else {
                    Some(app.selected_report)
                });
            }
        }
        7 => {
            // Security
            if let Ok(audits) = fetch_security_audits(socket, 20) {
                app.security_audits = audits;
                if app.selected_audit >= app.security_audits.len() {
                    app.selected_audit = app.security_audits.len().saturating_sub(1);
                }
                app.audit_list_state
                    .select(if app.security_audits.is_empty() {
                        None
                    } else {
                        Some(app.selected_audit)
                    });
            }
        }
        8 => {
            // Config
            if let Ok(sources) = fetch_config_sources(socket) {
                app.config_sources = sources;
                if app.selected_config >= app.config_sources.len() {
                    app.selected_config = app.config_sources.len().saturating_sub(1);
                }
                app.config_list_state
                    .select(if app.config_sources.is_empty() {
                        None
                    } else {
                        Some(app.selected_config)
                    });
            }
        }
        _ => {}
    }

    app.status = "Data refreshed successfully".to_string();
    Ok(())
}

fn main() -> Result<()> {
    // Initialize
    let socket =
        std::env::var("CHIMERA_API_SOCKET").unwrap_or_else(|_| "/run/chimera/api.sock".to_string());

    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let mut app = App::new();

    // Initial data load
    let _ = refresh_data(&mut app, &socket);

    // Main event loop
    loop {
        // Auto-refresh if enabled
        if app.auto_refresh && app.last_refresh.elapsed() >= Duration::from_secs(30) {
            let _ = refresh_data(&mut app, &socket);
        }

        terminal.draw(|f| ui(f, &mut app))?;

        if app.should_quit {
            break;
        }

        // Handle events with timeout for auto-refresh
        if event::poll(Duration::from_millis(500))? {
            match event::read()? {
                Event::Key(key) => {
                    if let Err(e) = handle_key_event(&mut app, key, &socket) {
                        app.show_error = Some(format!("Error: {}", e));
                    }
                }
                Event::Mouse(_) => {}
                Event::Resize(_, _) => {}
                _ => {}
            }
        }
    }

    // Cleanup
    disable_raw_mode()?;
    execute!(
        terminal.backend_mut(),
        LeaveAlternateScreen,
        DisableMouseCapture
    )?;
    terminal.show_cursor()?;

    Ok(())
}
