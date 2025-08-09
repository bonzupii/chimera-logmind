#!/bin/bash

# Start the UDS daemon in the background
sudo python3 -m api.server &

# Start the TUI in the foreground
cargo run --bin chimera-tui --manifest-path cli/Cargo.toml
