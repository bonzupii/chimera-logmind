# Chimera LogMind Core

Offline-first, single-host forensic and log analytics. Rust CLI + Python UDS backend + DuckDB. Ingests journald, queries with filters, and ships a minimal TUI.

## Features
- Rust CLI (`chimera`) speaking a simple line protocol over a Unix domain socket
  - `ping`, `health`, `version`
  - `ingest journal --seconds [--limit]` and `ingest all` for multi-source ingestion
  - `query logs` with filters: `since`, `min_severity`, `source`, `unit`, `hostname`, `contains`, `limit`, `order`
  - `search --query "text"` for semantic log search
  - `index` for embedding generation
  - `anomalies` for log anomaly detection
  - `metrics` and `alerts` for system health monitoring
  - `config` commands for log source management
  - `chat query --query "text"` for RAG-powered log analysis with local LLM
  - `chat --message "text"` for simple AI chat (legacy)
  - `chat-history`, `chat-clear`, and `chat-stats` for chat management
  - `report generate` for comprehensive daily reports
  - `report send --to email` for email delivery
  - `audit full` for comprehensive security auditing
  - `audit tool --tool aide|rkhunter|clamav|lynis` for specific security tools
- Python backend (`api/server.py`) listening on `/run/chimera/api.sock` (or `CHIMERA_API_SOCKET`)
  - Concurrency via threads
  - DuckDB storage + schema initialization
  - Multi-source ingestion: journald, log files, container logs
  - Cursor-based incremental ingest (persists `__CURSOR` in `ingest_state`)
  - Dedup via unique `cursor` and a message `fingerprint`, with a deterministic 64-bit `id`
  - Semantic search with Ollama embeddings and ChromaDB
  - Anomaly detection for log patterns
  - System health monitoring with metrics and alerts
  - Configuration management for log sources
  - RAG (Retrieval-Augmented Generation) chat for intelligent log analysis
  - Simple AI chat for basic interactions
- Minimal TUI (`chimera-tui`) with tabs for logs, search, health, chat, reports, security, and actions
- Ops: installer and systemd unit for production use

## Quickstart (development)
Use a user-writable socket to avoid root requirements during development. If `/run/chimera` is not writable, the server falls back to a per-user runtime dir under `/tmp`.

```bash
# Terminal 1 (backend)
python3 -m venv .venv && source .venv/bin/activate  # optional
export CHIMERA_API_SOCKET=/tmp/chimera/api.sock
export CHIMERA_DB_PATH=/tmp/chimera.duckdb
export CHIMERA_LOG_LEVEL=DEBUG
export CHIMERA_LOG_FILE=/tmp/chimera/api.log  # optional
export PYTHONPATH=.
python3 -m api.server
```

```bash
# Terminal 2 (CLI)
export CHIMERA_API_SOCKET=/tmp/chimera/api.sock
cargo run --manifest-path cli/Cargo.toml --bin cli -- ping
cargo run --manifest-path cli/Cargo.toml --bin cli -- ingest journal --seconds 300 --limit 100
cargo run --manifest-path cli/Cargo.toml --bin cli -- query logs --since 600 --limit 20
```
### Testing

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
PYTHONPATH=. pytest -q
```

### TUI
```bash
# Build once
cargo build --manifest-path cli/Cargo.toml
# Run the TUI (uses CHIMERA_API_SOCKET or defaults to /run/chimera/api.sock)
./cli/target/debug/chimera-tui
```
Keys: q quit, ←/→ switch tabs, ↑/↓ select, r refresh, i ingest 5m, I ingest 1h, c chat (in chat tab).

## Production install (systemd)
Prereqs: `journalctl` access and DuckDB for Python.

```bash
sudo apt-get update && sudo apt-get install -y python3-duckdb
sudo ops/install.sh
# The service listens on /run/chimera/api.sock and writes DB to /var/lib/chimera/chimera.duckdb
```
Grant your user access to the socket (optional):
```bash
sudo usermod -aG chimera $USER
newgrp chimera
```

## Commands and protocol (overview)
- `PING` → `PONG`
- `HEALTH` → `OK`
- `VERSION` → `0.1.0`
- `INGEST_JOURNAL <seconds> [limit]` → `OK inserted=N total=M`
- `INGEST_ALL` → `OK inserted=N sources=M`
- `QUERY_LOGS since=<sec> [min_severity=…] [source=…] [unit=…] [hostname=…] [contains=…] [limit=N] [order=asc|desc]` → NDJSON
- `DISCOVER UNITS|HOSTNAMES|SOURCES|SEVERITIES [since=<sec>] [limit=N]` → NDJSON of `{value,count}`
- `SEARCH query="text" [n_results=N] [since=SEC] [source=…] [unit=…] [severity=…]` → NDJSON
- `INDEX [since=SEC] [limit=N]` → `OK indexed=N total=M`
- `ANOMALIES [since=SEC]` → NDJSON
- `METRICS [type=TYPE] [since=SEC] [limit=N]` → NDJSON
- `COLLECT_METRICS` → `OK collected=N`
- `ALERTS [since=SEC] [severity=…] [acknowledged=BOOL]` → NDJSON
- `CHAT message="text"` → JSON
- `CHAT_HISTORY` → JSON
- `CHAT_CLEAR` → `OK history-cleared`
- `CHAT_STATS` → JSON
- `CONFIG GET|LIST|ADD_SOURCE|REMOVE_SOURCE|UPDATE_SOURCE` → JSON/OK
- `CHAT query="text" [context_size=N] [since=SEC]` → JSON
- `REPORT GENERATE [since=SEC] [format=text|html|json] [output=PATH]` → text/html/json
- `REPORT SEND to=EMAIL [since=SEC] [subject=SUBJECT]` → OK/ERR
- `REPORT LIST [limit=N]` → NDJSON
- `AUDIT FULL` → JSON
- `AUDIT TOOL tool=TOOL_NAME` → JSON
- `AUDIT HISTORY [tool=TOOL] [limit=N]` → NDJSON
- `AUDIT DETAILS id=ID` → JSON

## Environment variables
- `CHIMERA_API_SOCKET` (default `/run/chimera/api.sock`)
- `CHIMERA_DB_PATH` (default `/var/lib/chimera/chimera.duckdb` in service; `data/chimera.duckdb` when run ad-hoc)
- `CHIMERA_CONFIG_PATH` (default `/etc/chimera/config.json`)
- `CHIMERA_LOG_LEVEL` (default `DEBUG`; affects both console and file handlers)
- `CHIMERA_LOG_FILE` (optional; default `/var/log/chimera/api.log` when writable)

## Repo layout
- `cli/` — Rust CLI and TUI
- `api/` — Python backend, ingestion, DB schema
- `ops/` — Installer scripts and systemd unit
- `docs/` — OpenAPI sketch and docs

## Development notes
- The backend opens a fresh DuckDB connection per request
- `contains` uses a simple `ILIKE` filter; for large-scale search, consider DuckDB FTS in a future phase
- Cursor-based ingest avoids duplicates and reprocessing
- Semantic search requires Ollama with nomic-embed-text model
- RAG chat requires Ollama with llama2 or compatible LLM model
- System health monitoring requires psutil and systemd access
- ChromaDB stores embeddings in `/var/lib/chimera/chromadb`
- Security auditing requires installation of auditd, aide, rkhunter, chkrootkit, clamav, openscap, and lynis
- Report delivery requires Exim4 or similar mail server

### Ingestion configuration notes
- Journald source supports `units: ["unit1", ...]` and `exclude_units: ["systemd-*", ...]` patterns.
- Deterministic `logs.id` is a 64-bit integer derived from message fingerprint; existing deployments are auto-migrated.
- DuckDB currently does not support ON DELETE CASCADE in foreign keys; `log_embeddings` references `logs(id)` without cascade.

## License
TBD
