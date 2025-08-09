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

# Add the user running the script to the chimera group
if [ -n "$SUDO_USER" ]; then
    echo "Adding user '$SUDO_USER' to the 'chimera' group..."
    usermod -aG chimera "$SUDO_USER"
fi

# Create a Python virtual environment
python3 -m venv "$PREFIX/venv"
source "$PREFIX/venv/bin/activate"

# Install Python dependencies
if command -v pip &> /dev/null; then
    pip install -r "$(dirname "$0")/../requirements.txt"
    if [ -f "$(dirname "$0")/../requirements-dev.txt" ]; then
        pip install -r "$(dirname "$0")/../requirements-dev.txt"
    fi
else
    echo "Warning: pip not found. Skipping Python dependency installation." >&2
fi

deactivate

# Build and install the CLI
if [ -z "$SUDO_USER" ]; then
    echo "Error: This script must be run with sudo." >&2
    exit 1
fi

CARGO_PATH="/home/$SUDO_USER/.cargo/bin"
SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
CLI_PROJECT_PATH="$SCRIPT_DIR/../cli"

if ! [ -d "$CARGO_PATH" ]; then
    echo "Warning: Cargo not found for user '$SUDO_USER' in '$CARGO_PATH'." >&2
    echo "Please install rustup for your user (https://rustup.rs/) and run this script again." >&2
else
    echo "Building CLI as user '$SUDO_USER'..."
    
    # Fix permissions on the target directory before building
    if [ -d "$CLI_PROJECT_PATH/target" ]; then
        chown -R "$SUDO_USER:$SUDO_USER" "$CLI_PROJECT_PATH/target"
    fi

    # Run build commands as the original user
    BUILD_LOG=$(mktemp)
    if sudo -u "$SUDO_USER" PATH="$CARGO_PATH:$PATH" bash -c "
        set -e
        echo '--- Updating Rust toolchain ---'
        rustup update
        echo '--- Building chimera CLI ---'
        cargo build --release --bins --manifest-path '$CLI_PROJECT_PATH/Cargo.toml'
    " > "$BUILD_LOG" 2>&1; then
        echo "Build successful."
        cat "$BUILD_LOG"
    else
        echo "------------------------------------------------" >&2
        echo "ERROR: Cargo build failed. Log output:" >&2
        cat "$BUILD_LOG" >&2
        echo "------------------------------------------------" >&2
        rm -f "$BUILD_LOG"
        exit 1
    fi
    rm -f "$BUILD_LOG"

    # The binary is at target/release/cli, but we install it as 'chimera'
    CLI_BINARY_PATH="$CLI_PROJECT_PATH/target/release/cli"
    TUI_BINARY_PATH="$CLI_PROJECT_PATH/target/release/chimera-tui"

    if [ -f "$CLI_BINARY_PATH" ]; then
        echo "Found binary at: $CLI_BINARY_PATH"
        echo "Installing CLI to /usr/local/bin/chimera..."
        install -m 0755 "$CLI_BINARY_PATH" /usr/local/bin/chimera
    else
        echo "------------------------------------------------" >&2
        echo "ERROR: Could not find the compiled binary after a successful build." >&2
        echo "Looked for: '$CLI_BINARY_PATH'" >&2
        echo "------------------------------------------------" >&2
        exit 1
    fi

    if [ -f "$TUI_BINARY_PATH" ]; then
        echo "Found TUI binary at: $TUI_BINARY_PATH"
        echo "Installing TUI to /usr/local/bin/chimera-tui..."
        install -m 0755 "$TUI_BINARY_PATH" /usr/local/bin/chimera-tui
    else
        echo "------------------------------------------------" >&2
        echo "ERROR: Could not find the compiled TUI binary after a successful build." >&2
        echo "Looked for: '$TUI_BINARY_PATH'" >&2
        echo "------------------------------------------------" >&2
        exit 1
    fi
fi

# Ensure state directory for DuckDB exists
install -d -m 0750 -o chimera -g chimera /var/lib/chimera
install -d -m 0750 -o chimera -g chimera /var/lib/chimera/chromadb
install -d -m 0750 -o chimera -g chimera /var/log/chimera
install -d -m 0750 -o chimera -g chimera /run/chimera

# Grant chimera user read access to the system journal
if getent group systemd-journal >/dev/null 2>&1; then
  usermod -aG systemd-journal chimera || true
  # Verify if the chimera user is now in the systemd-journal group
  if ! id -nG chimera | grep -qw systemd-journal; then
    echo "Warning: User 'chimera' is not a member of 'systemd-journal' group. Log ingestion may fail." >&2
    echo "Please ensure 'chimera' user has read access to system journal." >&2
  fi
fi

mkdir -p "$PREFIX/api"
install -m 0755 "$(dirname "$0")/../api/"*.py "$PREFIX/api/"

install -m 0644 "$(dirname "$0")/${SERVICE_NAME}" "${SERVICE_PATH}"

# Update service file with correct paths
sed -i "s|/opt/chimera|$PREFIX|g" "${SERVICE_PATH}"

systemctl daemon-reload
systemctl enable --now ${SERVICE_NAME}

echo "Installed and started ${SERVICE_NAME}."
echo "The chimera CLI is available as /usr/local/bin/chimera"
