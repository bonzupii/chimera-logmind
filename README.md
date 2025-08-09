# Chimera LogMind

**Offline-first, single-host forensic and log analytics platform**

A powerful log analysis and forensic investigation tool combining Rust CLI performance with Python backend intelligence. Features real-time log ingestion, semantic search, AI-powered analysis, anomaly detection, and comprehensive security auditing.

![License](https://img.shields.io/badge/license-GPL--3.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![Rust](https://img.shields.io/badge/rust-1.70%2B-orange.svg)

## 🚀 Recent Improvements

**Major Refactoring (Latest Release):**
- ✅ **Cyclomatic Complexity Fixed**: Reduced from 204 to compliant levels using command dispatcher pattern
- ✅ **Zero Errors/Warnings**: All 241+ diagnostic issues resolved
- ✅ **Enhanced Maintainability**: Modular architecture with focused, single-responsibility functions
- ✅ **Improved Testability**: Individual command handlers can be easily unit tested
- ✅ **Better Error Handling**: Comprehensive error management across all components

## 🏗️ Architecture

```
┌─────────────────┐    Unix Socket     ┌──────────────────┐
│   Rust CLI      │◄──────────────────►│  Python Backend  │
│   & TUI         │   Line Protocol    │     (UDS API)    │
└─────────────────┘                    └──────────────────┘
                                                │
                                       ┌────────▼────────┐
                                       │     DuckDB      │
                                       │   + ChromaDB    │
                                       │   + Ollama LLM  │
                                       └─────────────────┘
```

## ✨ Features

### Core Functionality
- **Fast Log Ingestion**: Journald, log files, container logs with cursor-based incremental processing
- **Powerful Search**: Text search with filters, semantic search with embeddings
- **AI-Powered Analysis**: RAG (Retrieval-Augmented Generation) chat with local LLMs
- **Anomaly Detection**: ML-based log pattern analysis
- **System Monitoring**: Real-time metrics, health checks, and alerting

### Security & Forensics
- **Comprehensive Security Auditing**: Integration with auditd, aide, rkhunter, chkrootkit, clamav, openscap, lynis
- **Forensic Timeline**: Detailed log correlation and timeline analysis
- **Incident Response**: Automated report generation and delivery

### User Interfaces
- **Command Line**: Fast Rust CLI with comprehensive command set
- **Terminal UI**: Interactive TUI with tabs for logs, search, health, chat, reports, and security
- **Scripting**: Line protocol over Unix domain socket for automation

### Data Management
- **Deduplication**: Fingerprint-based with deterministic 64-bit IDs
- **Persistence**: Cursor tracking prevents reprocessing
- **Export/Import**: Multiple formats including JSON, HTML, and plain text

## 🛠️ Installation

### Development Setup

```bash
# Clone repository
git clone https://github.com/bonzupii/chimera-logmind.git
cd chimera-logmind

# Python backend setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Rust CLI/TUI setup
cd cli
cargo build --release
cd ..
```

### Production Installation (systemd)

```bash
# Prerequisites
sudo apt-get update
sudo apt-get install -y python3-duckdb python3-pip

# Install system service
sudo ./ops/install.sh

# Add user to chimera group (optional)
sudo usermod -aG chimera $USER
newgrp chimera

# Start service
sudo systemctl enable --now chimera-logmind
```

## 🚀 Quick Start

### Development Mode

```bash
# Terminal 1: Start Python backend
export CHIMERA_API_SOCKET=/tmp/chimera/api.sock
export CHIMERA_DB_PATH=/tmp/chimera.duckdb
export CHIMERA_LOG_LEVEL=DEBUG
export PYTHONPATH=.
python3 -m api.server

# Terminal 2: Use CLI
export CHIMERA_API_SOCKET=/tmp/chimera/api.sock
./cli/target/release/chimera ping
./cli/target/release/chimera ingest journal --seconds 300 --limit 100
./cli/target/release/chimera query logs --since 600 --limit 20
```

### TUI Interface

```bash
# Launch interactive terminal UI
./cli/target/release/chimera-tui
```

**TUI Controls:**
- `q` - Quit
- `←/→` - Switch tabs
- `↑/↓` - Navigate items
- `r` - Refresh current view
- `i` - Quick ingest (5 minutes)
- `I` - Extended ingest (1 hour)
- `c` - Chat mode (in chat tab)

## 📋 Commands Reference

### Core Commands

| Command | Description | Example |
|---------|-------------|---------|
| `ping` | Health check | `chimera ping` |
| `health` | System status | `chimera health` |
| `version` | Version info | `chimera version` |

### Log Management

| Command | Description | Parameters |
|---------|-------------|------------|
| `ingest journal` | Ingest from journald | `--seconds N`, `--limit N` |
| `ingest all` | Ingest from all sources | - |
| `query logs` | Search logs | `--since`, `--min-severity`, `--source`, etc. |
| `discover` | Find unique values | `units`, `hostnames`, `sources`, `severities` |

### AI & Analytics

| Command | Description | Parameters |
|---------|-------------|------------|
| `search` | Semantic search | `--query "text"`, `--n-results N` |
| `index` | Build embeddings | `--since SEC`, `--limit N` |
| `chat query` | RAG-powered analysis | `--query "text"`, `--context-size N` |
| `chat` | Simple AI chat | `--message "text"` |
| `anomalies` | Detect anomalies | `--since SEC` |

### Monitoring & Health

| Command | Description | Parameters |
|---------|-------------|------------|
| `metrics` | System metrics | `--type TYPE`, `--since SEC` |
| `collect-metrics` | Gather metrics | - |
| `alerts` | View alerts | `--severity LEVEL`, `--acknowledged BOOL` |

### Security & Auditing

| Command | Description | Parameters |
|---------|-------------|------------|
| `audit full` | Comprehensive audit | - |
| `audit tool` | Specific security tool | `--tool aide\|rkhunter\|clamav\|lynis` |
| `audit history` | Audit history | `--tool TOOL`, `--limit N` |

### Configuration & Reports

| Command | Description | Parameters |
|---------|-------------|------------|
| `config get` | View configuration | - |
| `config list` | List log sources | - |
| `report generate` | Generate reports | `--format text\|html\|json`, `--output PATH` |
| `report send` | Email reports | `--to EMAIL`, `--subject TEXT` |

## ⚙️ Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CHIMERA_API_SOCKET` | `/run/chimera/api.sock` | Unix socket path |
| `CHIMERA_DB_PATH` | `/var/lib/chimera/chimera.duckdb` | Database location |
| `CHIMERA_CONFIG_PATH` | `/etc/chimera/config.json` | Configuration file |
| `CHIMERA_LOG_LEVEL` | `DEBUG` | Logging verbosity |
| `CHIMERA_LOG_FILE` | `/var/log/chimera/api.log` | Log file path |

### Configuration File

```json
{
  "log_sources": [
    {
      "name": "journald",
      "type": "journald",
      "enabled": true,
      "config": {
        "units": ["sshd", "nginx"],
        "exclude_units": ["systemd-*"]
      }
    }
  ],
  "embedding_model": "nomic-embed-text",
  "chat_model": "llama2",
  "max_log_age_days": 30
}
```

## 🧪 Testing

```bash
# Install test dependencies
pip install -r requirements-dev.txt

# Run test suite
PYTHONPATH=. pytest -v

# Run with coverage
PYTHONPATH=. pytest --cov=api tests/

# Test specific components
pytest tests/test_server.py -v
```

## 📁 Project Structure

```
chimera-logmind/
├── api/                    # Python backend
│   ├── server.py          # Main API server (refactored)
│   ├── ingestion.py       # Log ingestion framework
│   ├── embeddings.py      # Semantic search engine
│   ├── chat.py            # RAG chat engine
│   ├── anomaly.py         # Anomaly detection
│   ├── system_health.py   # Health monitoring
│   ├── security_audit.py  # Security auditing
│   └── reporting.py       # Report generation
├── cli/                   # Rust CLI and TUI
│   ├── src/
│   │   ├── main.rs        # CLI entry point
│   │   ├── tui.rs         # Terminal UI
│   │   └── client.rs      # Socket client
│   └── Cargo.toml
├── tests/                 # Test suite
├── ops/                   # Deployment scripts
│   ├── install.sh         # System installer
│   └── chimera-logmind.service
├── docs/                  # Documentation
└── requirements.txt       # Python dependencies
```

## 🔌 Protocol Reference

The system uses a simple line-based protocol over Unix domain sockets:

### Request Format
```
COMMAND [param=value] [param=value]...
```

### Response Formats
- **Simple**: `OK` or `ERR message`
- **NDJSON**: One JSON object per line
- **Raw**: Plain text response

### Example Session
```bash
$ echo "PING" | nc -U /run/chimera/api.sock
PONG

$ echo "QUERY_LOGS since=3600 limit=5" | nc -U /run/chimera/api.sock
{"ts":"2024-01-15 10:30:00","hostname":"server","source":"sshd",...}
{"ts":"2024-01-15 10:29:45","hostname":"server","source":"nginx",...}
...
```

## 🤝 Contributing

We welcome contributions! Here's how to get started:

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/amazing-feature`)
3. **Make** your changes following our coding standards
4. **Add** tests for new functionality
5. **Ensure** all tests pass (`pytest`)
6. **Run** code quality checks
7. **Commit** your changes (`git commit -m 'Add amazing feature'`)
8. **Push** to the branch (`git push origin feature/amazing-feature`)
9. **Open** a Pull Request

### Code Quality Standards

- **Python**: Follow PEP 8, use type hints, maintain test coverage
- **Rust**: Follow rustfmt, handle all `Result` types appropriately
- **Documentation**: Update README and inline docs for new features
- **Testing**: Add tests for all new functionality

## 🏁 Roadmap

- [ ] **Web Dashboard**: Browser-based interface for remote access
- [ ] **Multi-Host Support**: Distributed log collection and analysis
- [ ] **Plugin System**: Extensible architecture for custom analyzers
- [ ] **Advanced ML**: More sophisticated anomaly detection models
- [ ] **Integration APIs**: REST API for external tool integration
- [ ] **Cloud Storage**: S3/Azure blob storage backends
- [ ] **Performance Optimization**: Query optimization and caching

## 📄 License

This project is licensed under the **GNU General Public License v3.0** - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **DuckDB** for high-performance analytics
- **ChromaDB** for vector storage and similarity search
- **Ollama** for local LLM integration
- **Rust Community** for excellent CLI/TUI libraries
- **Python Ecosystem** for robust backend libraries

---

**Built with ❤️ for system administrators, security engineers, and DevOps professionals.**

For questions, issues, or feature requests, please visit our [GitHub Issues](https://github.com/yourusername/chimera-logmind/issues) page.
