# Chimera LogMind Core

Offline-first, single-host forensic and log analytics. Rust CLI + Python UDS backend + DuckDB. Ingests journald, queries with filters, and ships a minimal TUI.

## Features
- Rust CLI (`chimera`) speaking a simple line protocol over a Unix domain socket
  - `ping`, `health`, `version`
  - `ingest journal --seconds [--limit]`
  - `query logs` with filters: `since`, `min_severity`, `source`, `unit`, `hostname`, `contains`, `limit`, `order`
- Python backend (`api/server.py`) listening on `/run/chimera/api.sock` (or `CHIMERA_API_SOCKET`)
  - Concurrency via threads
  - DuckDB storage + schema initialization
  - Journald ingestion via `journalctl -o json`
  - Cursor-based incremental ingest (persists `__CURSOR` in `ingest_state`)
  - Dedup via unique `cursor` and a message `fingerprint`
  - `DISCOVER` command for units/hostnames/sources/severities with counts
- Minimal TUI (`chimera-tui`) to trigger ingest and view recent logs
- Ops: installer and systemd unit for production use

## Quickstart (development)
Use a user-writable socket to avoid root requirements during development.

```bash
# Terminal 1 (backend)
python3 -m venv .venv && source .venv/bin/activate  # optional
export CHIMERA_API_SOCKET=/tmp/chimera/api.sock
export CHIMERA_DB_PATH=/tmp/chimera.duckdb
python3 api/server.py
```

```bash
# Terminal 2 (CLI)
export CHIMERA_API_SOCKET=/tmp/chimera/api.sock
cargo run --manifest-path cli/Cargo.toml -- ping
cargo run --manifest-path cli/Cargo.toml -- ingest journal --seconds 300 --limit 100
cargo run --manifest-path cli/Cargo.toml -- query logs --since 600 --limit 20
```

### TUI
```bash
# Build once
cargo build --manifest-path cli/Cargo.toml
# Run the TUI (uses CHIMERA_API_SOCKET or defaults to /run/chimera/api.sock)
./cli/target/debug/chimera-tui
```
Keys: q quit, ←/→ switch tabs, ↑/↓ select, r refresh, i ingest 5m, I ingest 1h.

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
- `QUERY_LOGS since=<sec> [min_severity=…] [source=…] [unit=…] [hostname=…] [contains=…] [limit=N] [order=asc|desc]` → NDJSON
- `DISCOVER UNITS|HOSTNAMES|SOURCES|SEVERITIES [since=<sec>] [limit=N]` → NDJSON of `{value,count}`

## Environment variables
- `CHIMERA_API_SOCKET` (default `/run/chimera/api.sock`)
- `CHIMERA_DB_PATH` (default `/var/lib/chimera/chimera.duckdb` in service; `data/chimera.duckdb` when run ad-hoc)

## Repo layout
- `cli/` — Rust CLI and TUI
- `api/` — Python backend, ingestion, DB schema
- `ops/` — Installer scripts and systemd unit
- `docs/` — OpenAPI sketch and docs

## Development notes
- The backend opens a fresh DuckDB connection per request
- `contains` uses a simple `ILIKE` filter; for large-scale search, consider DuckDB FTS in a future phase
- Cursor-based ingest avoids duplicates and reprocessing

## License
TBD
