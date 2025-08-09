use std::io::{BufReader, Read, Write};
use std::net::Shutdown;
use std::os::unix::net::UnixStream;

use anyhow::{Context, Result};
use clap::{Parser, Subcommand, CommandFactory};

const DEFAULT_SOCKET_PATH: &str = "/run/chimera/api.sock";

#[derive(Parser, Debug)]
#[command(name = "chimera", version, about = "Chimera LogMind CLI")]
struct Cli {
    /// Path to the Unix Domain Socket for the API
    #[arg(long, global = true, env = "CHIMERA_API_SOCKET", default_value = DEFAULT_SOCKET_PATH)]
    socket: String,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Send a ping to the API and print the response
    Ping,
    /// Check API health
    Health,
    /// Show API version information
    Version,
    /// Ingest logs from journald into DuckDB
    Ingest {
        #[command(subcommand)]
        target: IngestTarget,
    },
    /// Query logs with filters and print JSONL
    Query {
        #[command(subcommand)]
        target: QueryTarget,
    },
    /// Export logs in various formats
    Export {
        #[command(subcommand)]
        target: ExportTarget,
    },
    /// Manage configuration
    Config {
        #[command(subcommand)]
        action: ConfigAction,
    },
    /// Semantic search logs
    Search {
        /// Search query
        #[arg(long)]
        query: String,
        /// Number of results (default: 10)
        #[arg(long, default_value_t = 10)]
        n_results: i64,
        /// Look back window in seconds
        #[arg(long)]
        since: Option<i64>,
        /// Filter by source
        #[arg(long)]
        source: Option<String>,
        /// Filter by unit
        #[arg(long)]
        unit: Option<String>,
        /// Filter by severity
        #[arg(long)]
        severity: Option<String>,
    },
    /// Index logs for semantic search
    Index {
        /// Look back window in seconds (default: 86400)
        #[arg(long, default_value_t = 86400)]
        since: i64,
        /// Limit number of logs to index
        #[arg(long)]
        limit: Option<i64>,
    },
    /// Detect anomalies in logs
    Anomalies {
        /// Look back window in seconds (default: 3600)
        #[arg(long, default_value_t = 3600)]
        since: i64,
    },
    /// Trigger anomaly scan and view results
    AnomalyScan {
        /// Look back window in seconds (default: 3600)
        #[arg(long, default_value_t = 3600)]
        since: i64,
        /// Output format (json, table, summary)
        #[arg(long, default_value = "summary")]
        format: String,
    },
    /// Get system metrics
    Metrics {
        /// Metric type (cpu, memory, disk, network, service, uptime)
        #[arg(long)]
        metric_type: Option<String>,
        /// Look back window in seconds (default: 3600)
        #[arg(long, default_value_t = 3600)]
        since: i64,
        /// Limit number of results
        #[arg(long, default_value_t = 1000)]
        limit: i64,
    },
    /// Collect current system metrics
    CollectMetrics,
    /// Get system alerts
    Alerts {
        /// Look back window in seconds (default: 86400)
        #[arg(long, default_value_t = 86400)]
        since: i64,
        /// Filter by severity (warning, critical)
        #[arg(long)]
        severity: Option<String>,
        /// Filter by acknowledgment status
        #[arg(long)]
        acknowledged: Option<bool>,
    },
    /// Chat with AI assistant using RAG
    Chat {
        /// Chat query
        #[arg(long)]
        query: String,
        /// Ollama model to use (default: llama3.2:3b)
        #[arg(long, default_value = "llama3.2:3b")]
        model: String,
        /// Clear conversation history before this query
        #[arg(long, default_value_t = false)]
        clear_history: bool,
    },
    /// Get chat conversation history
    ChatHistory {
        /// Number of messages to retrieve (default: 10)
        #[arg(long, default_value_t = 10)]
        limit: i64,
    },
    /// Clear chat conversation history
    ChatClear,
    /// Check Ollama health and status
    OllamaHealth,
    /// List available Ollama models
    OllamaModels,
    /// Generate shell completion script
    Completions {
        /// Shell to generate completion for
        #[arg(value_enum)]
        shell: clap_complete::Shell,
    },
    /// Show detailed help and examples
    Help {
        /// Command to show help for
        #[arg(long)]
        command: Option<String>,
    },
}

#[derive(Subcommand, Debug)]
enum IngestTarget {
    /// Ingest recent journald entries
    Journal {
        /// Look back this many seconds (default: 3600)
        #[arg(long, default_value_t = 3600)]
        seconds: i64,
        /// Optional limit on number of entries
        #[arg(long)]
        limit: Option<i64>,
    },
    /// Ingest from all enabled sources
    All,
}

#[derive(Subcommand, Debug)]
enum QueryTarget {
    /// Query logs
    Logs {
        /// Look back window in seconds (default: 3600)
        #[arg(long, default_value_t = 3600)]
        since: i64,
        /// Minimum severity (emerg, alert, crit, err, warning, notice, info, debug)
        #[arg(long)]
        min_severity: Option<String>,
        /// Filter by source (e.g., journald)
        #[arg(long)]
        source: Option<String>,
        /// Filter by systemd unit or identifier
        #[arg(long)]
        unit: Option<String>,
        /// Filter by hostname
        #[arg(long)]
        hostname: Option<String>,
        /// Substring search in message
        #[arg(long)]
        contains: Option<String>,
        /// Max rows (default: 100)
        #[arg(long, default_value_t = 100)]
        limit: i64,
        /// Order by timestamp asc|desc (default: desc)
        #[arg(long, default_value = "desc")]
        order: String,
    },
}

#[derive(Subcommand, Debug)]
enum ExportTarget {
    /// Export logs to CSV format
    Csv {
        /// Look back window in seconds (default: 3600)
        #[arg(long, default_value_t = 3600)]
        since: i64,
        /// Minimum severity (emerg, alert, crit, err, warning, notice, info, debug)
        #[arg(long)]
        min_severity: Option<String>,
        /// Filter by source (e.g., journald)
        #[arg(long)]
        source: Option<String>,
        /// Filter by systemd unit or identifier
        #[arg(long)]
        unit: Option<String>,
        /// Filter by hostname
        #[arg(long)]
        hostname: Option<String>,
        /// Substring search in message
        #[arg(long)]
        contains: Option<String>,
        /// Max rows (default: 1000)
        #[arg(long, default_value_t = 1000)]
        limit: i64,
        /// Output file path (default: stdout)
        #[arg(long)]
        output: Option<String>,
    },
    /// Export logs to JSON format
    Json {
        /// Look back window in seconds (default: 3600)
        #[arg(long, default_value_t = 3600)]
        since: i64,
        /// Minimum severity (emerg, alert, crit, err, warning, notice, info, debug)
        #[arg(long)]
        min_severity: Option<String>,
        /// Filter by source (e.g., journald)
        #[arg(long)]
        source: Option<String>,
        /// Filter by systemd unit or identifier
        #[arg(long)]
        unit: Option<String>,
        /// Filter by hostname
        #[arg(long)]
        hostname: Option<String>,
        /// Substring search in message
        #[arg(long)]
        contains: Option<String>,
        /// Max rows (default: 1000)
        #[arg(long, default_value_t = 1000)]
        limit: i64,
        /// Output file path (default: stdout)
        #[arg(long)]
        output: Option<String>,
    },
}

#[derive(Subcommand, Debug)]
enum ConfigAction {
    /// List all log sources
    List,
    /// Get full configuration
    Get,
    /// Add a new log source
    AddSource {
        /// Source name
        #[arg(long)]
        name: String,
        /// Source type (journald, file, container)
        #[arg(long)]
        source_type: String,
        /// Whether source is enabled
        #[arg(long, default_value_t = true)]
        enabled: bool,
        /// Source configuration as JSON
        #[arg(long)]
        config: Option<String>,
    },
    /// Remove a log source
    RemoveSource {
        /// Source name
        #[arg(long)]
        name: String,
    },
    /// Update a log source
    UpdateSource {
        /// Source name
        #[arg(long)]
        name: String,
        /// Whether source is enabled
        #[arg(long)]
        enabled: Option<bool>,
        /// Source configuration as JSON
        #[arg(long)]
        config: Option<String>,
    },
}

fn main() -> Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::Ping => {
            let response = send_request(&cli.socket, "PING")?;
            println!("{}", response.trim_end());
        }
        Commands::Health => {
            let response = send_request(&cli.socket, "HEALTH")?;
            println!("{}", response.trim_end());
        }
        Commands::Version => {
            let response = send_request(&cli.socket, "VERSION")?;
            println!("{}", response.trim_end());
        }
        Commands::Ingest { target } => match target {
            IngestTarget::Journal { seconds, limit } => {
                let cmd = if let Some(n) = limit {
                    format!("INGEST_JOURNAL {} {}", seconds, n)
                } else {
                    format!("INGEST_JOURNAL {}", seconds)
                };
                let response = send_request(&cli.socket, &cmd)?;
                println!("{}", response.trim_end());
            }
            IngestTarget::All => {
                let response = send_request(&cli.socket, "INGEST_ALL")?;
                println!("{}", response.trim_end());
            }
        },
        Commands::Query { target } => match target {
            QueryTarget::Logs {
                since,
                min_severity,
                source,
                unit,
                hostname,
                contains,
                limit,
                order,
            } => {
                let mut parts: Vec<String> = vec!["QUERY_LOGS".into(), format!("since={}", since)];
                if let Some(v) = min_severity { parts.push(format!("min_severity={}", v)); }
                if let Some(v) = source { parts.push(format!("source={}", v)); }
                if let Some(v) = unit { parts.push(format!("unit={}", v)); }
                if let Some(v) = hostname { parts.push(format!("hostname={}", v)); }
                if let Some(v) = contains {
                    let enc = urlencoding::encode(&v);
                    parts.push(format!("contains={}", enc));
                }
                parts.push(format!("limit={}", limit));
                parts.push(format!("order={}", order));
                let cmd = parts.join(" ");
                let response = send_request(&cli.socket, &cmd)?;
                print!("{}", response);
            }
        },
        Commands::Export { target } => match target {
            ExportTarget::Csv {
                since,
                min_severity,
                source,
                unit,
                hostname,
                contains,
                limit,
                output,
            } => {
                let mut parts: Vec<String> = vec!["QUERY_LOGS".into(), format!("since={}", since)];
                if let Some(v) = min_severity { parts.push(format!("min_severity={}", v)); }
                if let Some(v) = source { parts.push(format!("source={}", v)); }
                if let Some(v) = unit { parts.push(format!("unit={}", v)); }
                if let Some(v) = hostname { parts.push(format!("hostname={}", v)); }
                if let Some(v) = contains {
                    let enc = urlencoding::encode(&v);
                    parts.push(format!("contains={}", enc));
                }
                parts.push(format!("limit={}", limit));
                parts.push("order=asc".into());
                let cmd = parts.join(" ");
                let response = send_request(&cli.socket, &cmd)?;
                
                // Convert JSONL to CSV
                let mut csv_output = String::new();
                csv_output.push_str("timestamp,hostname,source,unit,severity,pid,message\n");
                
                for line in response.lines() {
                    if line.trim().is_empty() {
                        continue;
                    }
                    if let Ok(json) = serde_json::from_str::<serde_json::Value>(line) {
                        let ts = json["ts"].as_str().unwrap_or("");
                        let host = json["hostname"].as_str().unwrap_or("");
                        let src = json["source"].as_str().unwrap_or("");
                        let u = json["unit"].as_str().unwrap_or("");
                        let sev = json["severity"].as_str().unwrap_or("");
                        let pid = json["pid"].as_str().unwrap_or("");
                        let msg = json["message"].as_str().unwrap_or("").replace("\"", "\"\"");
                        
                        csv_output.push_str(&format!("\"{}\",\"{}\",\"{}\",\"{}\",\"{}\",\"{}\",\"{}\"\n",
                            ts, host, src, u, sev, pid, msg));
                    }
                }
                
                if let Some(output_path) = output {
                    std::fs::write(&output_path, csv_output)?;
                    println!("CSV exported to {}", output_path);
                } else {
                    print!("{}", csv_output);
                }
            }
            ExportTarget::Json {
                since,
                min_severity,
                source,
                unit,
                hostname,
                contains,
                limit,
                output,
            } => {
                let mut parts: Vec<String> = vec!["QUERY_LOGS".into(), format!("since={}", since)];
                if let Some(v) = min_severity { parts.push(format!("min_severity={}", v)); }
                if let Some(v) = source { parts.push(format!("source={}", v)); }
                if let Some(v) = unit { parts.push(format!("unit={}", v)); }
                if let Some(v) = hostname { parts.push(format!("hostname={}", v)); }
                if let Some(v) = contains {
                    let enc = urlencoding::encode(&v);
                    parts.push(format!("contains={}", enc));
                }
                parts.push(format!("limit={}", limit));
                parts.push("order=asc".into());
                let cmd = parts.join(" ");
                let response = send_request(&cli.socket, &cmd)?;
                
                // Convert JSONL to JSON array
                let mut json_array = Vec::new();
                for line in response.lines() {
                    if line.trim().is_empty() {
                        continue;
                    }
                    if let Ok(json) = serde_json::from_str::<serde_json::Value>(line) {
                        json_array.push(json);
                    }
                }
                
                let json_output = serde_json::to_string_pretty(&json_array)?;
                
                if let Some(output_path) = output {
                    std::fs::write(&output_path, json_output)?;
                    println!("JSON exported to {}", output_path);
                } else {
                    println!("{}", json_output);
                }
            }
        },
        Commands::Config { action } => match action {
            ConfigAction::List => {
                let response = send_request(&cli.socket, "CONFIG LIST")?;
                print!("{}", response);
            }
            ConfigAction::Get => {
                let response = send_request(&cli.socket, "CONFIG GET")?;
                print!("{}", response);
            }
            ConfigAction::AddSource { name, source_type, enabled, config } => {
                let mut parts = vec![
                    "CONFIG ADD_SOURCE".into(),
                    format!("name={}", name),
                    format!("type={}", source_type),
                    format!("enabled={}", enabled),
                ];
                if let Some(cfg) = config {
                    parts.push(format!("config={}", cfg));
                }
                let cmd = parts.join(" ");
                let response = send_request(&cli.socket, &cmd)?;
                println!("{}", response.trim_end());
            }
            ConfigAction::RemoveSource { name } => {
                let cmd = format!("CONFIG REMOVE_SOURCE name={}", name);
                let response = send_request(&cli.socket, &cmd)?;
                println!("{}", response.trim_end());
            }
            ConfigAction::UpdateSource { name, enabled, config } => {
                let mut parts = vec![
                    "CONFIG UPDATE_SOURCE".into(),
                    format!("name={}", name),
                ];
                if let Some(en) = enabled {
                    parts.push(format!("enabled={}", en));
                }
                if let Some(cfg) = config {
                    parts.push(format!("config={}", cfg));
                }
                let cmd = parts.join(" ");
                let response = send_request(&cli.socket, &cmd)?;
                println!("{}", response.trim_end());
            }
        },
        Commands::Search { query, n_results, since, source, unit, severity } => {
            let mut parts = vec![
                "SEARCH".into(),
                format!("query={}", urlencoding::encode(&query)),
                format!("n_results={}", n_results),
            ];
            if let Some(s) = since { parts.push(format!("since={}", s)); }
            if let Some(s) = source { parts.push(format!("source={}", s)); }
            if let Some(u) = unit { parts.push(format!("unit={}", u)); }
            if let Some(s) = severity { parts.push(format!("severity={}", s)); }
            let cmd = parts.join(" ");
            let response = send_request(&cli.socket, &cmd)?;
            print!("{}", response);
        }
        Commands::Index { since, limit } => {
            let mut parts = vec![
                "INDEX".into(),
                format!("since={}", since),
            ];
            if let Some(l) = limit { parts.push(format!("limit={}", l)); }
            let cmd = parts.join(" ");
            let response = send_request(&cli.socket, &cmd)?;
            println!("{}", response.trim_end());
        }
        Commands::Anomalies { since } => {
            let cmd = format!("ANOMALIES since={}", since);
            let response = send_request(&cli.socket, &cmd)?;
            print!("{}", response);
        }
        Commands::AnomalyScan { since, format } => {
            let cmd = format!("ANOMALIES since={}", since);
            let response = send_request(&cli.socket, &cmd)?;
            
            match format.as_str() {
                "json" => {
                    print!("{}", response);
                }
                "table" => {
                    println!("ANOMALY SCAN RESULTS (last {} seconds)", since);
                    println!("{:<20} {:<15} {:<10} {:<50}", "Timestamp", "Type", "Severity", "Description");
                    println!("{:-<95}", "");
                    
                    for line in response.lines() {
                        if line.trim().is_empty() {
                            continue;
                        }
                        if let Ok(json) = serde_json::from_str::<serde_json::Value>(line) {
                            let ts = json["timestamp"].as_str().unwrap_or("N/A");
                            let anomaly_type = json["type"].as_str().unwrap_or("unknown");
                            let severity = json["severity"].as_str().unwrap_or("info");
                            let desc = json["description"].as_str().unwrap_or("No description");
                            
                            println!("{:<20} {:<15} {:<10} {:<50}", 
                                ts, anomaly_type, severity, desc);
                        }
                    }
                }
                "summary" => {
                    let mut anomaly_count = 0;
                    let mut severity_counts = std::collections::HashMap::new();
                    
                    for line in response.lines() {
                        if line.trim().is_empty() {
                            continue;
                        }
                        if let Ok(json) = serde_json::from_str::<serde_json::Value>(line) {
                            anomaly_count += 1;
                            let severity = json["severity"].as_str().unwrap_or("unknown").to_string();
                            *severity_counts.entry(severity).or_insert(0) += 1;
                        }
                    }
                    
                    println!("ANOMALY SCAN SUMMARY (last {} seconds)", since);
                    println!("Total anomalies found: {}", anomaly_count);
                    if !severity_counts.is_empty() {
                        println!("By severity:");
                        for (severity, count) in severity_counts {
                            println!("  {}: {}", severity, count);
                        }
                    }
                    
                    if anomaly_count > 0 {
                        println!("\nDetailed results:");
                        for line in response.lines() {
                            if line.trim().is_empty() {
                                continue;
                            }
                            if let Ok(json) = serde_json::from_str::<serde_json::Value>(line) {
                                let ts = json["timestamp"].as_str().unwrap_or("N/A");
                                let anomaly_type = json["type"].as_str().unwrap_or("unknown");
                                let severity = json["severity"].as_str().unwrap_or("info");
                                let desc = json["description"].as_str().unwrap_or("No description");
                                
                                println!("[{}] {} ({}): {}", ts, anomaly_type, severity, desc);
                            }
                        }
                    }
                }
                _ => {
                    eprintln!("Unknown format: {}. Using summary format.", format);
                    // Recursive call with summary format
                    return main();
                }
            }
        }
        Commands::Metrics { metric_type, since, limit } => {
            let mut parts = vec![
                "METRICS".into(),
                format!("since={}", since),
                format!("limit={}", limit),
            ];
            if let Some(mt) = metric_type { parts.push(format!("type={}", mt)); }
            let cmd = parts.join(" ");
            let response = send_request(&cli.socket, &cmd)?;
            print!("{}", response);
        }
        Commands::CollectMetrics => {
            let response = send_request(&cli.socket, "COLLECT_METRICS")?;
            println!("{}", response.trim_end());
        }
        Commands::Alerts { since, severity, acknowledged } => {
            let mut parts = vec![
                "ALERTS".into(),
                format!("since={}", since),
            ];
            if let Some(s) = severity { parts.push(format!("severity={}", s)); }
            if let Some(a) = acknowledged { parts.push(format!("acknowledged={}", a)); }
            let cmd = parts.join(" ");
            let response = send_request(&cli.socket, &cmd)?;
            print!("{}", response);
        }
        Commands::Chat { query, model, clear_history } => {
            let mut parts = vec![
                "CHAT".into(),
                format!("query={}", urlencoding::encode(&query)),
                format!("model={}", model),
            ];
            if clear_history {
                parts.push("clear_history=true".into());
            }
            let cmd = parts.join(" ");
            let response = send_request(&cli.socket, &cmd)?;
            print!("{}", response);
        }
        Commands::ChatHistory { limit } => {
            let cmd = format!("CHAT_HISTORY limit={}", limit);
            let response = send_request(&cli.socket, &cmd)?;
            print!("{}", response);
        }
        Commands::ChatClear => {
            let response = send_request(&cli.socket, "CHAT_CLEAR")?;
            println!("{}", response.trim_end());
        }
        Commands::OllamaHealth => {
            let response = send_request(&cli.socket, "OLLAMA_HEALTH")?;
            print!("{}", response);
        }
        Commands::OllamaModels => {
            let response = send_request(&cli.socket, "OLLAMA_MODELS")?;
            print!("{}", response);
        }
        Commands::Completions { shell } => {
            clap_complete::generate(shell, &mut Cli::command(), "chimera", &mut std::io::stdout());
        }
        Commands::Help { command } => {
            match command.as_deref() {
                Some("chat") => {
                    println!("CHAT COMMAND HELP");
                    println!("================");
                    println!("Chat with AI assistant using RAG (Retrieval-Augmented Generation)");
                    println!();
                    println!("Usage:");
                    println!("  chimera chat --query \"What errors occurred in the last hour?\"");
                    println!("  chimera chat --query \"Analyze system performance\" --model llama3.2:3b");
                    println!("  chimera chat --query \"New conversation\" --clear-history");
                    println!();
                    println!("Options:");
                    println!("  --query TEXT     The question or message to send to the AI");
                    println!("  --model MODEL    Ollama model to use (default: llama3.2:3b)");
                    println!("  --clear-history  Clear conversation history before this query");
                    println!();
                    println!("Examples:");
                    println!("  # Ask about recent errors");
                    println!("  chimera chat --query \"What errors or warnings appeared in the logs recently?\"");
                    println!();
                    println!("  # Get system analysis");
                    println!("  chimera chat --query \"Analyze the current system health and identify any issues\"");
                    println!();
                    println!("  # Troubleshoot specific service");
                    println!("  chimera chat --query \"What's wrong with the nginx service?\"");
                }
                Some("search") => {
                    println!("SEARCH COMMAND HELP");
                    println!("==================");
                    println!("Semantic search through logs using AI embeddings");
                    println!();
                    println!("Usage:");
                    println!("  chimera search --query \"authentication failures\"");
                    println!("  chimera search --query \"disk space issues\" --since 86400 --n-results 20");
                    println!();
                    println!("Options:");
                    println!("  --query TEXT     Search query");
                    println!("  --n-results N    Number of results (default: 10)");
                    println!("  --since SECONDS  Look back window in seconds");
                    println!("  --source SOURCE  Filter by log source");
                    println!("  --unit UNIT      Filter by systemd unit");
                    println!("  --severity SEV   Filter by severity level");
                }
                Some("export") => {
                    println!("EXPORT COMMAND HELP");
                    println!("==================");
                    println!("Export logs in various formats");
                    println!();
                    println!("Usage:");
                    println!("  chimera export csv --since 3600 --output logs.csv");
                    println!("  chimera export json --min-severity err --limit 500 --output errors.json");
                    println!();
                    println!("Formats:");
                    println!("  csv    Export as CSV file");
                    println!("  json   Export as JSON file");
                    println!();
                    println!("Options:");
                    println!("  --since SECONDS    Look back window in seconds");
                    println!("  --min-severity SEV Minimum severity level");
                    println!("  --source SOURCE    Filter by log source");
                    println!("  --unit UNIT        Filter by systemd unit");
                    println!("  --hostname HOST    Filter by hostname");
                    println!("  --contains TEXT    Substring search in message");
                    println!("  --limit N          Maximum number of records");
                    println!("  --output FILE      Output file path (default: stdout)");
                }
                Some("anomaly-scan") => {
                    println!("ANOMALY SCAN COMMAND HELP");
                    println!("========================");
                    println!("Trigger anomaly detection and view results");
                    println!();
                    println!("Usage:");
                    println!("  chimera anomaly-scan --since 3600");
                    println!("  chimera anomaly-scan --since 86400 --format table");
                    println!();
                    println!("Options:");
                    println!("  --since SECONDS  Look back window in seconds");
                    println!("  --format FORMAT  Output format: json, table, summary (default: summary)");
                    println!();
                    println!("Formats:");
                    println!("  json     Raw JSON output");
                    println!("  table    Formatted table");
                    println!("  summary  Summary with details");
                }
                Some("ollama") => {
                    println!("OLLAMA COMMANDS HELP");
                    println!("===================");
                    println!("Manage Ollama integration for AI features");
                    println!();
                    println!("Commands:");
                    println!("  chimera ollama-health    Check Ollama service status");
                    println!("  chimera ollama-models    List available models");
                    println!();
                    println!("Setup:");
                    println!("  1. Install Ollama: https://ollama.ai");
                    println!("  2. Pull a model: ollama pull llama3.2:3b");
                    println!("  3. Test: chimera ollama-health");
                }
                _ => {
                    println!("CHIMERA LOGMIND CORE - COMPREHENSIVE HELP");
                    println!("=========================================");
                    println!();
                    println!("Core Commands:");
                    println!("  ping              Test API connectivity");
                    println!("  health            Check API health");
                    println!("  version           Show API version");
                    println!();
                    println!("Log Management:");
                    println!("  ingest journal    Ingest journald logs");
                    println!("  ingest all        Ingest from all sources");
                    println!("  query logs        Query logs with filters");
                    println!("  export csv/json   Export logs in various formats");
                    println!();
                    println!("AI & Search:");
                    println!("  search            Semantic log search");
                    println!("  index             Index logs for search");
                    println!("  chat              RAG chat with AI assistant");
                    println!("  chat-history      View chat history");
                    println!("  chat-clear        Clear chat history");
                    println!();
                    println!("Monitoring:");
                    println!("  anomalies         Detect log anomalies");
                    println!("  anomaly-scan      Enhanced anomaly scanning");
                    println!("  metrics           Get system metrics");
                    println!("  collect-metrics   Collect current metrics");
                    println!("  alerts            View system alerts");
                    println!();
                    println!("Configuration:");
                    println!("  config list       List log sources");
                    println!("  config get        Get full configuration");
                    println!("  config add-source Add new log source");
                    println!("  config remove-source Remove log source");
                    println!("  config update-source Update log source");
                    println!();
                    println!("Ollama Integration:");
                    println!("  ollama-health     Check Ollama status");
                    println!("  ollama-models     List available models");
                    println!();
                    println!("Utilities:");
                    println!("  completions       Generate shell completions");
                    println!("  help --command    Show detailed help for specific command");
                    println!();
                    println!("Examples:");
                    println!("  # Quick system check");
                    println!("  chimera health && chimera metrics --since 3600");
                    println!();
                    println!("  # Investigate issues");
                    println!("  chimera search --query \"error authentication\" --since 3600");
                    println!("  chimera chat --query \"What's causing these authentication errors?\"");
                    println!();
                    println!("  # Export for analysis");
                    println!("  chimera export csv --min-severity err --since 86400 --output errors.csv");
                    println!();
                    println!("  # Monitor anomalies");
                    println!("  chimera anomaly-scan --since 3600 --format summary");
                    println!();
                    println!("For detailed help on specific commands:");
                    println!("  chimera help --command <command-name>");
                }
            }
        }
    }

    Ok(())
}

fn send_request(socket_path: &str, command: &str) -> Result<String> {
    let mut stream = UnixStream::connect(socket_path)
        .with_context(|| format!("failed to connect to socket {}", socket_path))?;

    let mut message = command.as_bytes().to_vec();
    message.push(b'\n');
    stream
        .write_all(&message)
        .with_context(|| format!("failed to send {} request", command))?;
    let _ = stream.shutdown(Shutdown::Write);

    let mut reader = BufReader::new(stream);
    let mut response = String::new();
    reader
        .read_to_string(&mut response)
        .with_context(|| format!("failed to read {} response", command))?;
    Ok(response)
}
