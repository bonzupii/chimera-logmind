import os
import socket
import threading
import time
import duckdb


from api.server import main as server_main


def start_server_in_thread():
    t = threading.Thread(target=server_main, daemon=True)
    t.start()
    return t


def wait_for_socket(path, timeout=5.0):
    start = time.time()
    while time.time() - start < timeout:
        if os.path.exists(path):
            return True
        time.sleep(0.05)
    return False


def send_cmd(sock_path, line: str) -> bytes:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(sock_path)
        s.sendall(line.encode())
        s.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        return data


def test_server_ping_and_query(temp_env_paths, temp_socket_path, monkeypatch):
    # Make server bind to temp socket instead of /run/chimera
    monkeypatch.setenv("CHIMERA_API_SOCKET", temp_socket_path)
    start_server_in_thread()
    assert wait_for_socket(temp_socket_path)

    # PING
    resp = send_cmd(temp_socket_path, "PING\n")
    assert b"PONG" in resp

    # Insert a dummy log directly
    conn = duckdb.connect(os.environ["CHIMERA_DB_PATH"], read_only=False)
    try:
        from api.db import initialize_schema
        initialize_schema(conn)
        # Use deterministic id for test
        conn.execute("INSERT INTO logs (id, ts, hostname, source, unit, severity, message) VALUES (1, CURRENT_TIMESTAMP, 'h', 'test', 'u', 'info', 'hello world')")
    finally:
        conn.close()

    # Query it
    resp = send_cmd(temp_socket_path, "QUERY_LOGS since=86400 limit=10\n")
    assert b"hello world" in resp
