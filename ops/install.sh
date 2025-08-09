#!/usr/bin/env bash
set -euo pipefail

# Install Chimera API service and files
PREFIX=${PREFIX:-/opt/chimera}
SERVICE_NAME=chimera-api.service
SERVICE_PATH=/etc/systemd/system/${SERVICE_NAME}

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root" >&2
  exit 1
fi

# Create chimera user/group if not exists
if ! id -u chimera >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin chimera
fi

# Ensure state directory for DuckDB exists
install -d -m 0750 -o chimera -g chimera /var/lib/chimera

# Grant chimera user read access to the system journal
if getent group systemd-journal >/dev/null 2>&1; then
  usermod -aG systemd-journal chimera || true
fi

mkdir -p "$PREFIX/api"
install -m 0755 "$(dirname "$0")/../api/server.py" "$PREFIX/api/server.py"
install -m 0644 "$(dirname "$0")/../api/db.py" "$PREFIX/api/db.py"
install -m 0644 "$(dirname "$0")/../api/ingest.py" "$PREFIX/api/ingest.py"

install -m 0644 "$(dirname "$0")/${SERVICE_NAME}" "${SERVICE_PATH}"

systemctl daemon-reload
systemctl enable --now ${SERVICE_NAME}

echo "Installed and started ${SERVICE_NAME}."