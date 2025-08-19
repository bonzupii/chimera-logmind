# CRUSH.md

This file provides guidelines and commands for agentic coding agents operating in this repository.

## Build/Lint/Test Commands

### Python (api/)
- **Install dependencies:** `pip install -r requirements.txt` and `pip install -r requirements-dev.txt`
- **Run tests:** `pytest tests/`
- **Run a single test:** `pytest tests/<file_name>.py::<test_name>` (e.g., `pytest tests/test_config.py::test_load_config`)
- **Linting:** Adhere to PEP 8. While no explicit linter command is in `run.sh`, tools like `ruff` or `flake8` are standard for Python projects of this style. Prefer `ruff check .` for linting and `black .` for formatting.

### Rust (cli/)
- **Build:** `cargo build`
- **Run TUI:** `cargo run --bin chimera-tui --manifest-path cli/Cargo.toml` or `./target/release/chimera-tui`
- **Run CLI:** `cargo run --bin chimera --manifest-path cli/Cargo.toml`
- **Test:** `cargo test`
- **Run a single test:** `cargo test <test_name>` (e.g., `cargo test test_some_function`)
- **Linting:** `cargo clippy` (recommended for idiomatic Rust code analysis)
- **Formatting:** `cargo fmt` (enforced by `rustfmt`)

## Code Style Guidelines

### General
- Adhere to existing code style, formatting, and naming conventions within each language.
- Use explicit imports, grouping standard library, third-party, and project-specific imports where applicable.

### Python
- **Formatting:** Follow PEP 8 (indentation, spaces around operators, etc.).
- **Types:** Use type hints extensively for function arguments, return values, and variables (`Optional`, `list`, `dict`, `str`, `int`, etc.).
- **Naming Conventions:** `snake_case` for functions, variables, and modules. `PascalCase` for classes. Constants should be `SCREAMING_SNAKE_CASE`. Private-like internal functions should start with a single underscore (`_function_name`).
- **Error Handling:** Use `try-except` blocks for anticipated errors (e.g., `ValueError`, file not found, database issues). Return meaningful error messages or propagate exceptions appropriately. For API endpoints, send `ERR` responses.
- **Logging:** Use the `logging` module (`logger.info`, `logger.warning`, `logger.error`, `logger.debug`). Ensure clear and concise log messages.

### Rust
- **Formatting:** Code must be formatted with `rustfmt`. Run `cargo fmt` to apply consistent formatting.
- **Error Handling:** Use `anyhow::Result` for error propagation. Employ the `?` operator for concise error handling. Add context to errors using `.with_context(|| "...")` when appropriate.
- **Naming Conventions:** `snake_case` for functions, variables, and modules. `PascalCase` for structs, enums, and traits. `SCREAMING_SNAKE_CASE` for constants.
- **Documentation:** Use Rustdoc comments (`///`) for public functions, structs, and enums to explain their purpose and usage.
- **Logging:** Use the `log` crate macros (`info!`, `warn!`, `error!`, `debug!`) for logging within the application. Configure `env_logger` for runtime log level control.
