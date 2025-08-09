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
    hostname: String,
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
    query_time: f64,
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

fn send_chat_message(socket: &str, message: &str) -> Result<ChatResponse> {
    let cmd = format!("CHAT message={}", urlencoding::encode(message));
    let resp = uds_request(socket, &cmd)?;
    
    // Parse JSON response
    if let Ok(json_value) = serde_json::from_str::<serde_json::Value>(&resp) {
        if let (Some(response), Some(confidence), Some(query_time), Some(sources_count)) = (
            json_value.get("response").and_then(|v| v.as_str()),
            json_value.get("confidence").and_then(|v| v.as_f64()),
            json_value.get("query_time").and_then(|v| v.as_f64()),
            json_value.get("sources_count").and_then(|v| v.as_i64()),
        ) {
            return Ok(ChatResponse {
                response: response.to_string(),
                confidence,
                query_time,
                sources_count,
            });
        }
    }
    
    Err(anyhow::anyhow!("Failed to parse chat response"))
}

fn get_chat_history(socket: &str) -> Result<Vec<ChatMessage>> {
    let resp = uds_request(socket, "CHAT_HISTORY")?;
    
    if let Ok(json_value) = serde_json::from_str::<serde_json::Value>(&resp) {
        if let Some(history) = json_value.get("history").and_then(|v| v.as_array()) {
            let mut messages = Vec::new();
            for msg in history {
                if let (Some(role), Some(content), Some(timestamp)) = (
                    msg.get("role").and_then(|v| v.as_str()),
                    msg.get("content").and_then(|v| v.as_str()),
                    msg.get("timestamp").and_then(|v| v.as_f64()),
                ) {
                    messages.push(ChatMessage {
                        role: role.to_string(),
                        content: content.to_string(),
                        timestamp,
                    });
                }
            }
            return Ok(messages);
        }
    }
    
    Ok(Vec::new())
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
                1 => {
                    let help = Paragraph::new("Semantic Search: Use CLI 'chimera search --query \"text\"' to search logs semantically")
                        .block(Block::default().borders(Borders::ALL).title("Semantic Search"));
                    f.render_widget(help, chunks[1]);
                }
                2 => {
                    let help = Paragraph::new("System Health: Use CLI 'chimera metrics' and 'chimera alerts' to view system health")
                        .block(Block::default().borders(Borders::ALL).title("System Health"));
                    f.render_widget(help, chunks[1]);
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
                    let help = Paragraph::new("Reports: Use CLI 'chimera report generate' to create daily reports")
                        .block(Block::default().borders(Borders::ALL).title("Reports"));
                    f.render_widget(help, chunks[1]);
                }
                5 => {
                    let help = Paragraph::new("Security: Use CLI 'chimera audit full' to run security audits")
                        .block(Block::default().borders(Borders::ALL).title("Security Audits"));
                    f.render_widget(help, chunks[1]);
                }
                }
                _ => {
                    let help = Paragraph::new("Keys: i=ingest 5m, I=ingest 1h, r=refresh, q=quit, ←/→ tabs, ↑/↓ select, c=chat")
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
                        if chat_input_mode && tab_index == 3 {
                            chat_input.push(ch);
                        }
                    },
                    KeyCode::Backspace => {
                        if chat_input_mode && tab_index == 3 {
                            chat_input.pop();
                        }
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
                    },
                    KeyCode::Esc => {
                        if chat_input_mode {
                            chat_input_mode = false;
                            chat_input.clear();
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
