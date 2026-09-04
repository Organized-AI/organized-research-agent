"""Bounded local API/control-plane fixture for the prototype; standard-library only."""
import json
import sqlite3
import sys
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
DEFAULT_DB = ROOT / "data" / "research.db"
TENANT = "demo-tenant"

def connect(db_path=DEFAULT_DB):
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL, demo INTEGER NOT NULL)")
    conn.execute("INSERT OR IGNORE INTO workspaces VALUES (?, ?, ?)", (TENANT, "Fictional merchant research workspace", 1))
    conn.commit()
    return conn

def workspace_for(conn, tenant_id):
    if tenant_id != TENANT:
        return None
    return conn.execute("SELECT id, name, demo FROM workspaces WHERE id = ?", (tenant_id,)).fetchone()

class Api(BaseHTTPRequestHandler):
    def do_GET(self):
        tenant = self.headers.get("X-Demo-Tenant", TENANT)
        if self.path == "/health":
            return self.reply(HTTPStatus.OK, {"status": "ok", "mode": "local-demo", "storage": "sqlite-control-plane"})
        if self.path == "/workspace":
            with connect() as conn:
                row = workspace_for(conn, tenant)
            if not row:
                return self.reply(HTTPStatus.NOT_FOUND, {"error": "tenant not found"})
            return self.reply(HTTPStatus.OK, {"workspace": dict(row), "tenantIsolation": "demonstrated fixture contract"})
        return self.reply(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        return

def self_test():
    with tempfile.TemporaryDirectory() as temp:
        with connect(Path(temp) / "control.db") as conn:
            allowed = workspace_for(conn, TENANT)
            denied = workspace_for(conn, "other-tenant")
        assert allowed["demo"] == 1 and denied is None
    print("API_SELF_TEST_PASS tenant-isolation fixture contract")

if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        print("Local-only API: http://127.0.0.1:8000/health")
        ThreadingHTTPServer(("127.0.0.1", 8000), Api).serve_forever()
