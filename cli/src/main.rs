use std::io::{BufReader, Read, Write};
use std::net::Shutdown;
use std::os::unix::net::UnixStream;

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};

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
    /// Manage configuration
    Config {
        #[command(subcommand)]
        action: ConfigAction,
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
