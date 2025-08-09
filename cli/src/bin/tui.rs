use anyhow::{Context, Result};
use crossterm::event::{self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode};
use crossterm::execute;
use crossterm::terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen};
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::Span;
use ratatui::widgets::{Block, Borders, List, ListItem, Paragraph, Tabs};
use ratatui::Terminal;
use std::io::{stdout, Write};
use std::os::unix::net::UnixStream;
use std::time::Duration;

#[derive(Debug, Clone)]
struct LogItem {
    ts: String,
    hostname: String,
    unit: String,
    severity: String,
    message: String,
}

fn uds_request(socket: &str, command: &str) -> Result<String> {
    let mut stream = UnixStream::connect(socket)
        .with_context(|| format!("failed to connect to socket {}", socket))?;
    let mut message = command.as_bytes().to_vec();
    message.push(b'\n');
    stream.write_all(&message)?;
    let _ = stream.shutdown(std::net::Shutdown::Write);
    let mut resp = String::new();
    std::io::Read::read_to_string(&mut std::io::BufReader::new(stream), &mut resp)?;
    Ok(resp)
}

fn fetch_logs(socket: &str, since: i64, limit: i64) -> Result<Vec<LogItem>> {
    let cmd = format!("QUERY_LOGS since={} limit={} order=desc", since, limit);
    let resp = uds_request(socket, &cmd)?;
    let mut out = Vec::new();
    for line in resp.lines() {
        if line.starts_with("ERR ") {
            continue;
        }
        if line.trim().is_empty() {
            continue;
        }
        match serde_json::from_str::<serde_json::Value>(line) {
            Ok(v) => {
                let ts = v.get("ts").and_then(|x| x.as_str()).unwrap_or("").to_string();
                let hostname = v.get("hostname").and_then(|x| x.as_str()).unwrap_or("").to_string();
                let unit = v.get("unit").and_then(|x| x.as_str()).unwrap_or("").to_string();
                let severity = v.get("severity").and_then(|x| x.as_str()).unwrap_or("").to_string();
                let message = v.get("message").and_then(|x| x.as_str()).unwrap_or("").to_string();
                out.push(LogItem { ts, hostname, unit, severity, message });
            }
            Err(_) => {}
        }
    }
    Ok(out)
}

fn trigger_ingest(socket: &str, seconds: i64, limit: Option<i64>) -> Result<String> {
    let cmd = match limit { Some(n) => format!("INGEST_JOURNAL {} {}", seconds, n), None => format!("INGEST_JOURNAL {}", seconds) };
    uds_request(socket, &cmd)
}

fn main() -> Result<()> {
    let socket = std::env::var("CHIMERA_API_SOCKET").unwrap_or_else(|_| "/run/chimera/api.sock".to_string());

    enable_raw_mode()?;
    let mut stdout = stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let mut tab_index = 0usize; // 0: Logs, 1: Actions
    let titles = vec!["Logs", "Actions"]; 
    let mut logs: Vec<LogItem> = Vec::new();
    let mut status = String::new();
    let mut selected = 0usize;

    'mainloop: loop {
        if let Ok(new_logs) = fetch_logs(&socket, 3600, 200) {
            logs = new_logs;
            if selected >= logs.len() { selected = logs.len().saturating_sub(1); }
        }

        terminal.draw(|f| {
            let size = f.size();
            let chunks = Layout::default()
                .direction(Direction::Vertical)
                .constraints([
                    Constraint::Length(3),
                    Constraint::Min(1),
                    Constraint::Length(3),
                ])
                .split(size);

            let titles_spans: Vec<Span> = titles.iter().map(|t| Span::raw(*t)).collect();
            let tabs = Tabs::new(titles_spans)
                .block(Block::default().borders(Borders::ALL).title("Chimera"))
                .select(tab_index)
                .style(Style::default().fg(Color::Cyan))
                .highlight_style(Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD));
            f.render_widget(tabs, chunks[0]);

            match tab_index {
                0 => {
                    let items: Vec<ListItem> = logs.iter().enumerate().map(|(i, l)| {
                        let text = format!("{} [{}] {}: {}", l.ts, l.severity, l.unit, l.message);
                        let mut li = ListItem::new(text);
                        if i == selected {
                            li = li.style(Style::default().fg(Color::Yellow));
                        }
                        li
                    }).collect();
                    let list = List::new(items)
                        .block(Block::default().borders(Borders::ALL).title("Recent Logs"));
                    f.render_widget(list, chunks[1]);
                }
                _ => {
                    let help = Paragraph::new("Keys: i=ingest 5m, I=ingest 1h, r=refresh, q=quit, ←/→ tabs, ↑/↓ select")
                        .block(Block::default().borders(Borders::ALL).title("Actions"));
                    f.render_widget(help, chunks[1]);
                }
            }

            let status_p = Paragraph::new(status.clone())
                .block(Block::default().borders(Borders::ALL).title("Status"));
            f.render_widget(status_p, chunks[2]);
        })?;

        if event::poll(Duration::from_millis(500))? {
            if let Event::Key(key) = event::read()? {
                match key.code {
                    KeyCode::Char('q') => break 'mainloop,
                    KeyCode::Left => { if tab_index > 0 { tab_index -= 1; } },
                    KeyCode::Right => { if tab_index < titles.len() - 1 { tab_index += 1; } },
                    KeyCode::Up => { selected = selected.saturating_sub(1); },
                    KeyCode::Down => { if !logs.is_empty() { selected = (selected + 1).min(logs.len().saturating_sub(1)); } },
                    KeyCode::Char('r') => { /* auto refresh */ },
                    KeyCode::Char('i') => {
                        match trigger_ingest(&socket, 300, Some(500)) {
                            Ok(resp) => status = resp.trim().to_string(),
                            Err(e) => status = format!("ERR {}", e),
                        }
                    },
                    KeyCode::Char('I') => {
                        match trigger_ingest(&socket, 3600, Some(2000)) {
                            Ok(resp) => status = resp.trim().to_string(),
                            Err(e) => status = format!("ERR {}", e),
                        }
                    },
                    _ => {}
                }
            }
        }
    }

    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen, DisableMouseCapture)?;
    terminal.show_cursor()?;
    Ok(())
}
