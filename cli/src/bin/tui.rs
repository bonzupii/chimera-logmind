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
use urlencoding;

#[derive(Debug, Clone)]
struct LogItem {
    ts: String,
    unit: String,
    severity: String,
    message: String,
}

#[derive(Debug, Clone)]
struct ChatMessage {
    role: String,
    content: String,
    timestamp: f64,
}

#[derive(Debug, Clone)]
struct ChatResponse {
    response: String,
    confidence: f64,
    sources_count: i64,
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
                let unit = v.get("unit").and_then(|x| x.as_str()).unwrap_or("").to_string();
                let severity = v.get("severity").and_then(|x| x.as_str()).unwrap_or("").to_string();
                let message = v.get("message").and_then(|x| x.as_str()).unwrap_or("").to_string();
                out.push(LogItem { ts, unit, severity, message });
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

fn send_chat_message(socket: &str, message: &str) -> Result<ChatResponse> {
    let cmd = format!("CHAT message={}", urlencoding::encode(message));
    let resp = uds_request(socket, &cmd)?;
    // Parse JSON response (support minimal and extended forms)
    if let Ok(json_value) = serde_json::from_str::<serde_json::Value>(&resp) {
        if let Some(response) = json_value.get("response").and_then(|v| v.as_str()) {
            let confidence = json_value.get("confidence").and_then(|v| v.as_f64()).unwrap_or(0.0);
            let sources_count = json_value.get("sources_count").and_then(|v| v.as_i64()).unwrap_or(0);
            return Ok(ChatResponse { response: response.to_string(), confidence, sources_count });
        }
    }
    // Fallback: raw text
    Ok(ChatResponse { response: resp.trim().to_string(), confidence: 0.0, sources_count: 0 })
}

fn search_logs_semantic(socket: &str, query: &str, n_results: i64, since: Option<i64>) -> Result<Vec<(LogItem, f64)>> {
    let mut cmd = format!("SEARCH query={} n_results={}", urlencoding::encode(query), n_results);
    if let Some(sec) = since { cmd.push_str(&format!(" since={}", sec)); }
    let resp = uds_request(socket, &cmd)?;
    let mut out = Vec::new();
    for line in resp.lines() {
        if line.starts_with("ERR ") || line.trim().is_empty() { continue; }
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
            let ts = v.get("ts").and_then(|x| x.as_str()).unwrap_or("").to_string();
            let unit = v.get("unit").and_then(|x| x.as_str()).unwrap_or("").to_string();
            let severity = v.get("severity").and_then(|x| x.as_str()).unwrap_or("").to_string();
            let message = v.get("message").and_then(|x| x.as_str()).unwrap_or("").to_string();
            let similarity = v.get("similarity").and_then(|x| x.as_f64()).unwrap_or(0.0);
            out.push((LogItem { ts, unit, severity, message }, similarity));
        }
    }
    Ok(out)
}

fn fetch_metrics(socket: &str, metric_type: Option<&str>, since: i64, limit: i64) -> Result<Vec<String>> {
    let mut cmd = format!("METRICS since={} limit={}", since, limit);
    if let Some(t) = metric_type { cmd.push_str(&format!(" type={}", t)); }
    let resp = uds_request(socket, &cmd)?;
    Ok(resp.lines().filter(|l| !l.trim().is_empty()).map(|s| s.to_string()).collect())
}

fn fetch_alerts(socket: &str, since: i64, severity: Option<&str>, acknowledged: Option<bool>) -> Result<Vec<String>> {
    let mut cmd = format!("ALERTS since={}", since);
    if let Some(sev) = severity { cmd.push_str(&format!(" severity={}", sev)); }
    if let Some(ack) = acknowledged { cmd.push_str(&format!(" acknowledged={}", ack)); }
    let resp = uds_request(socket, &cmd)?;
    Ok(resp.lines().filter(|l| !l.trim().is_empty()).map(|s| s.to_string()).collect())
}

fn list_reports(socket: &str, limit: i64) -> Result<Vec<String>> {
    let cmd = format!("REPORT LIST limit={}", limit);
    let resp = uds_request(socket, &cmd)?;
    Ok(resp.lines().filter(|l| !l.trim().is_empty()).map(|s| s.to_string()).collect())
}

fn generate_report(socket: &str, since: i64, format: &str) -> Result<String> {
    let cmd = format!("REPORT GENERATE since={} format={}", since, format);
    uds_request(socket, &cmd)
}

fn index_embeddings(socket: &str, since: i64) -> Result<String> {
    let cmd = format!("INDEX since={}", since);
    uds_request(socket, &cmd)
}



fn main() -> Result<()> {
    let socket = std::env::var("CHIMERA_API_SOCKET").unwrap_or_else(|_| "/run/chimera/api.sock".to_string());

    enable_raw_mode()?;
    let mut stdout = stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let mut tab_index = 0usize; // 0: Logs, 1: Search, 2: Health, 3: Chat, 4: Reports, 5: Security, 6: Actions
    let titles = vec!["Logs", "Search", "Health", "Chat", "Reports", "Security", "Actions"];
    let mut logs: Vec<LogItem> = Vec::new();
    let mut status = String::new();
    let mut selected = 0usize;
    
    // Chat state
    let mut chat_messages: Vec<ChatMessage> = Vec::new();
    let mut chat_input = String::new();
    let mut chat_input_mode = false;

    // Search state
    let mut search_query = String::new();
    let mut search_input_mode = false;
    let mut search_results: Vec<(LogItem, f64)> = Vec::new();

    // Health
    let mut metrics_lines: Vec<String> = Vec::new();
    let mut alerts_lines: Vec<String> = Vec::new();

    'mainloop: loop {
        if let Ok(new_logs) = fetch_logs(&socket, 3600, 200) {
            logs = new_logs;
            if selected >= logs.len() { selected = logs.len().saturating_sub(1); }
        }
        if tab_index == 2 {
            if let Ok(m) = fetch_metrics(&socket, None, 3600, 100) {
                metrics_lines = m;
            }
            if let Ok(a) = fetch_alerts(&socket, 3600, None, None) {
                alerts_lines = a;
            }
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
                1 => {
                    let search_chunks = Layout::default()
                        .direction(Direction::Vertical)
                        .constraints([
                            Constraint::Length(3),
                            Constraint::Min(1),
                        ])
                        .split(chunks[1]);

                    // Search input
                    let input_text = if search_input_mode { format!("Query: {}", search_query) } else { "Press '/' to enter a search query".to_string() };
                    let input = Paragraph::new(input_text)
                        .block(Block::default().borders(Borders::ALL).title("Semantic Search"));
                    f.render_widget(input, search_chunks[0]);

                    // Results
                    let mut items: Vec<ListItem> = Vec::new();
                    for (li, sim) in &search_results {
                        let line = format!("{:.2} [{}] {}: {}", sim, li.severity, li.unit, li.message);
                        items.push(ListItem::new(line));
                    }
                    let list = List::new(items).block(Block::default().borders(Borders::ALL).title("Results"));
                    f.render_widget(list, search_chunks[1]);
                }
                2 => {
                    let health_chunks = Layout::default()
                        .direction(Direction::Horizontal)
                        .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
                        .split(chunks[1]);
                    let metrics_p = Paragraph::new(metrics_lines.join("\n")).block(Block::default().borders(Borders::ALL).title("Metrics"));
                    let alerts_p = Paragraph::new(alerts_lines.join("\n")).block(Block::default().borders(Borders::ALL).title("Alerts"));
                    f.render_widget(metrics_p, health_chunks[0]);
                    f.render_widget(alerts_p, health_chunks[1]);
                }
                3 => {
                    // Chat tab with interactive chat interface
                    let chat_chunks = Layout::default()
                        .direction(Direction::Vertical)
                        .constraints([
                            Constraint::Min(1),
                            Constraint::Length(3),
                        ])
                        .split(chunks[1]);
                    
                    // Chat messages
                    let mut chat_items = Vec::new();
                    for msg in &chat_messages {
                        let role_style = if msg.role == "user" {
                            Style::default().fg(Color::Cyan)
                        } else {
                            Style::default().fg(Color::Green)
                        };
                        
                        let content = if msg.content.len() > 80 {
                            format!("{}...", &msg.content[..80])
                        } else {
                            msg.content.clone()
                        };
                        
                        let text = format!("[{}] {}: {}", msg.role, msg.timestamp, content);
                        chat_items.push(ListItem::new(text).style(role_style));
                    }
                    
                    let chat_list = List::new(chat_items)
                        .block(Block::default().borders(Borders::ALL).title("Chat History"));
                    f.render_widget(chat_list, chat_chunks[0]);
                    
                    // Chat input
                    let input_text = if chat_input_mode {
                        format!("> {}", chat_input)
                    } else {
                        "Press 'c' to start typing a message...".to_string()
                    };
                    
                    let input_para = Paragraph::new(input_text)
                        .block(Block::default().borders(Borders::ALL).title("Chat Input"));
                    f.render_widget(input_para, chat_chunks[1]);
                }
                4 => {
                    let help = Paragraph::new("Press 'g' to generate a report (last 24h), 'L' to list saved reports, 'n' to (re)index embeddings")
                        .block(Block::default().borders(Borders::ALL).title("Reports & Indexing"));
                    f.render_widget(help, chunks[1]);
                }
                5 => {
                    let help = Paragraph::new("Security: Use CLI 'chimera audit full' to run security audits")
                        .block(Block::default().borders(Borders::ALL).title("Security Audits"));
                    f.render_widget(help, chunks[1]);
                }
                6 => {
                    let help = Paragraph::new("Keys: i=ingest 5m, I=ingest 1h, r=refresh, /=search, c=chat, g=report, L=list reports, n=index embeddings, q=quit, ←/→ tabs, ↑/↓ select")
                        .block(Block::default().borders(Borders::ALL).title("Actions"));
                    f.render_widget(help, chunks[1]);
                }
                _ => {}
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
                    KeyCode::Char('/') => {
                        if tab_index == 1 {
                            search_input_mode = !search_input_mode;
                            if !search_input_mode && !search_query.is_empty() {
                                match search_logs_semantic(&socket, &search_query, 20, Some(86400)) {
                                    Ok(results) => { search_results = results; status = "Search OK".to_string(); },
                                    Err(e) => status = format!("Search error: {}", e),
                                }
                                search_query.clear();
                            }
                        }
                    },
                    KeyCode::Char('g') => {
                        if tab_index == 4 {
                            match generate_report(&socket, 86400, "text") { Ok(r) => status = format!("Report generated (preview may be long): {}", r.lines().next().unwrap_or("ok")), Err(e) => status = format!("ERR {}", e) }
                        }
                    },
                    KeyCode::Char('L') => {
                        if tab_index == 4 {
                            match list_reports(&socket, 10) { Ok(lines) => { status = "Listed reports".to_string(); /* could show in a future panel */ let _ = lines; }, Err(e) => status = format!("ERR {}", e) }
                        }
                    },
                    KeyCode::Char('n') => {
                        if tab_index == 4 {
                            match index_embeddings(&socket, 86400) { Ok(s) => status = s.trim().to_string(), Err(e) => status = format!("ERR {}", e) }
                        }
                    },
                    KeyCode::Char('c') => {
                        if tab_index == 3 { // Chat tab
                            chat_input_mode = !chat_input_mode;
                            if !chat_input_mode && !chat_input.is_empty() {
                                // Send chat message
                                match send_chat_message(&socket, &chat_input) {
                                    Ok(response) => {
                                        // Add user message to history
                                        chat_messages.push(ChatMessage {
                                            role: "user".to_string(),
                                            content: chat_input.clone(),
                                            timestamp: std::time::SystemTime::now()
                                                .duration_since(std::time::UNIX_EPOCH)
                                                .unwrap_or_default()
                                                .as_secs_f64(),
                                        });
                                        
                                        // Add assistant response to history
                                        chat_messages.push(ChatMessage {
                                            role: "assistant".to_string(),
                                            content: response.response,
                                            timestamp: std::time::SystemTime::now()
                                                .duration_since(std::time::UNIX_EPOCH)
                                                .unwrap_or_default()
                                                .as_secs_f64(),
                                        });
                                        
                                        status = format!("Chat: {} (confidence: {:.2})", 
                                            if response.sources_count > 0 { "Found relevant logs" } else { "No relevant logs" },
                                            response.confidence);
                                    }
                                    Err(e) => {
                                        status = format!("Chat error: {}", e);
                                    }
                                }
                                chat_input.clear();
                            }
                        }
                    },
                    KeyCode::Char(ch) => {
                        if chat_input_mode && tab_index == 3 { chat_input.push(ch); }
                        if search_input_mode && tab_index == 1 { search_query.push(ch); }
                    },
                    KeyCode::Backspace => {
                        if chat_input_mode && tab_index == 3 { chat_input.pop(); }
                        if search_input_mode && tab_index == 1 { search_query.pop(); }
                    },
                    KeyCode::Enter => {
                        if chat_input_mode && tab_index == 3 {
                            chat_input_mode = false;
                            if !chat_input.is_empty() {
                                // Send chat message
                                match send_chat_message(&socket, &chat_input) {
                                    Ok(response) => {
                                        // Add user message to history
                                        chat_messages.push(ChatMessage {
                                            role: "user".to_string(),
                                            content: chat_input.clone(),
                                            timestamp: std::time::SystemTime::now()
                                                .duration_since(std::time::UNIX_EPOCH)
                                                .unwrap_or_default()
                                                .as_secs_f64(),
                                        });
                                        
                                        // Add assistant response to history
                                        chat_messages.push(ChatMessage {
                                            role: "assistant".to_string(),
                                            content: response.response,
                                            timestamp: std::time::SystemTime::now()
                                                .duration_since(std::time::UNIX_EPOCH)
                                                .unwrap_or_default()
                                                .as_secs_f64(),
                                        });
                                        
                                        status = format!("Chat: {} (confidence: {:.2})", 
                                            if response.sources_count > 0 { "Found relevant logs" } else { "No relevant logs" },
                                            response.confidence);
                                    }
                                    Err(e) => {
                                        status = format!("Chat error: {}", e);
                                    }
                                }
                                chat_input.clear();
                            }
                        }
                        if search_input_mode && tab_index == 1 {
                            search_input_mode = false;
                            if !search_query.is_empty() {
                                match search_logs_semantic(&socket, &search_query, 20, Some(86400)) {
                                    Ok(results) => { search_results = results; status = "Search OK".to_string(); },
                                    Err(e) => status = format!("Search error: {}", e),
                                }
                                search_query.clear();
                            }
                        }
                    },
                    KeyCode::Esc => {
                        if chat_input_mode {
                            chat_input_mode = false;
                            chat_input.clear();
                        }
                        if search_input_mode {
                            search_input_mode = false;
                            search_query.clear();
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
