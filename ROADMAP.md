# Chimera LogMind Core — Development Roadmap

## High-Level Goals
Build **Chimera LogMind Core** — an offline-first, air-gapped, single-machine forensic and log analytics system featuring:  
- **Rust CLI** for command shell interface  
- **Python backend API** over Unix Domain Sockets (UDS)  
- **DuckDB** for analytics  
- **ChromaDB** for semantic search  
- **Ollama** for local LLM integration  
- **Bash/Systemd** for service orchestration and system integration  
- Integration with **auditd**, **AIDE**, **rkhunter**, **chkrootkit**, **ClamAV**, **OpenSCAP**, and **Lynis** for comprehensive host integrity and compliance monitoring  

---

## Non-Functional Constraints
- 100% offline / controlled network environment by default  
- No Docker runtime; native systemd-managed services  
- Strict socket file permissions for API UDS (`/run/chimera/api.sock`)  
- Secure default configs and least privilege principles  
- Designed for a single host forensic/monitor use case with optional minimal off-host export  

---

## MVP Scope
- Reliable ingestion from journald, `/var/log/`, container logs, remote SSH logs, common network logs (firewall, DNS, DHCP, VPN, etc.)  
- Rust CLI shell with commands for querying logs, anomaly detection, LLM chat, and integrity scan management  
- Python UDS API backend handling ingestion, storage, querying, AI orchestration  
- DuckDB + ChromaDB for data storage and semantic search  
- Basic integration and scheduling of auditd, AIDE, rkhunter, chkrootkit, ClamAV, OpenSCAP, and Lynis scans  
- Daily report generation and local delivery via Exim4  

---

## Phases

### Phase 0 — Project Setup & API Contract
- Monorepo with subdirectories: `/cli` (Rust), `/api` (Python), `/ops` (bash/systemd), `/docs`  
- Define UDS socket path and permissions  
- OpenAPI spec for API over UDS  
- Initial systemd units and install scripts skeleton  
- Security & threat model checklist  

---

### Phase 1 — Core Log Ingestion & Storage
- Python ingestion service from journald, `/var/log/`, container logs, remote SSH logs, common network logs (firewall, DNS, DHCP, VPN)  
- Vector agent integration (optional)  
- Store parsed logs in DuckDB and embeddings in ChromaDB  
- Unit tests for ingestion and storage  
- Rust CLI skeleton: basic connectivity and commands  
- API UDS socket security hardening  

---

### Phase 1.5 — Configurable & Flexible Log Ingestion
- Design a modular ingestion framework supporting:  
  - User-configurable log sources (paths, journald units, SSH logs, container logs, network logs)  
  - Dynamic discovery of new sources (e.g., new containers started/stopped)  
  - Flexible parsing with pluggable parsers or regex/rule-based configurations  
- CLI commands and API endpoints to add/remove/list ingestion sources at runtime  
- Support for log rotation and file retention policies  
- Tests covering ingestion config changes and source discovery  
- Documentation for ingestion configuration and extension  

---

### Phase 2 — Semantic Search & Anomaly Detection
- Implement embedding pipeline using Ollama models  
- Populate and maintain ChromaDB vectors  
- Simple anomaly detection engine in Python  
- CLI commands to run searches and view anomalies  
- Tests for search accuracy and anomaly flags  

---

### Phase 2.5 — System Health & Resource Monitoring
- Implement backend metric collection for CPU, memory, disk, network, uptime, and service status  
- Store metrics time-series in DuckDB  
- Extend API with endpoints for system health data retrieval  
- CLI-style TUI panel/tab for live and historical system health visualization  
- Alerts and threshold monitoring integrated with anomaly detection engine  
- CLI commands to query and export system health reports  

---

### Phase 3 — Rust CLI Feature Expansion ✅ COMPLETED
- ✅ Command syntax for log querying, filtering, and exporting  
- ✅ Trigger anomaly scans and view results  
- ✅ Initiate RAG chat sessions via CLI with LLM backend  
- ✅ CLI auto-completion, history, and help system  

---

### Phase 4 — Reporting & Delivery
- Daily scheduled reports combining DuckDB analytics and semantic results  
- Exim4 integration for local mail delivery  
- CLI commands to generate and manage reports  

---

### Phase 5 — Host Integrity, Malware & Security Auditing Integration
- Schedule auditd, AIDE, rkhunter, ClamAV, chkrootkit, OpenSCAP, and Lynis scans via systemd timers  
- Parse and normalize scan results into DuckDB for unified historical queries and alerts  
- CLI commands to view scan results, integrity status, and compliance reports  
- Support triage, alerting, and report generation for security audits  

---

### Phase 6 — Hardening, Packaging & Documentation
- Harden service accounts and UDS socket permissions  
- SELinux/AppArmor policy templates  
- Comprehensive test suites (unit, integration, end-to-end)  
- Packaging: Debian `.deb` and tarball installers with systemd integration  
- Admin runbooks, API docs, and CLI usage guides  

---

### Phase 7 — Optional Secure Log Shipping
- Implement configurable log export to a remote storage server via SCP  
- Integrate log shipping with daily report scheduler or manual CLI trigger  
- Support encryption, signing, and integrity verification of shipped logs  
- CLI commands to manage, monitor, and troubleshoot log sync  
- Documentation for secure offline-to-storage log transfer best practices  

---

## Risks & Mitigations
- **LLM resource constraints** — limit model sizes and concurrency  
- **Data growth in vector DB** — retention and pruning policies  
- **Socket permission misconfigurations** — install-time checks and monitoring  
- **False positives in integrity and compliance scans** — tune baselines carefully  

---

## Short-Term Next Actions
1. ✅ Scaffold Rust CLI project with basic UDS client connectivity  
2. ✅ Prototype Python API server exposing minimal ingestion endpoints over UDS  
3. ✅ Write systemd unit files and bash install scripts for API and scan schedulers  
4. ✅ Define DuckDB schema and begin journald ingestion prototype  
5. ✅ Test local Ollama embedding integration with sample logs
6. **NEXT: Begin Phase 4 - Reporting & Delivery system**
7. **NEXT: Implement daily scheduled reports with Exim4 integration**
8. **NEXT: Add CLI commands for report generation and management**  

---
