"""칭찬 릴레이 서버. Python 표준 라이브러리와 SQLite만 써요.

실행: python app.py [--host 127.0.0.1] [--port 8080]
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("PRAISE_DB", BASE_DIR / "data" / "praise.db"))
INDEX_PATH = BASE_DIR / "index.html"
STATIC_DIR = BASE_DIR / "static"
STATIC_TYPES = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

MAX_BODY = 8 * 1024
LIST_LIMIT = 200
NAME_MAX = 40
MESSAGE_MAX = 500
TAG_MAX = 30


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                message TEXT NOT NULL,
                tag TEXT,
                created_at TEXT NOT NULL
            )"""
        )


def row_to_entry(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "message": row["message"],
        "tag": row["tag"],
        "created_at": row["created_at"],
    }


def list_entries(q=None):
    sql = "SELECT * FROM entries"
    params = []
    if q:
        sql += " WHERE name LIKE ? OR message LIKE ? OR tag LIKE ?"
        like = f"%{q}%"
        params = [like, like, like]
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(LIST_LIMIT)
    with connect() as conn:
        return [row_to_entry(r) for r in conn.execute(sql, params)]


def create_entry(name, message, tag):
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO entries (name, message, tag, created_at) VALUES (?, ?, ?, ?)",
            (name, message, tag, created_at),
        )
        row = conn.execute("SELECT * FROM entries WHERE id = ?", (cur.lastrowid,)).fetchone()
    return row_to_entry(row)


def validate(data):
    """검증한 (name, message, tag)를 돌려주거나 ValueError를 내요."""
    if not isinstance(data, dict):
        raise ValueError("JSON 객체를 보내 주세요.")
    name, message, tag = data.get("name"), data.get("message"), data.get("tag")
    if not isinstance(name, str) or not isinstance(message, str):
        raise ValueError("name과 message는 문자열이어야 해요.")
    name, message = name.strip(), message.strip()
    if not 1 <= len(name) <= NAME_MAX:
        raise ValueError(f"name은 1~{NAME_MAX}자로 써 주세요.")
    if not 1 <= len(message) <= MESSAGE_MAX:
        raise ValueError(f"message는 1~{MESSAGE_MAX}자로 써 주세요.")
    if tag is not None:
        if not isinstance(tag, str):
            raise ValueError("tag는 문자열이어야 해요.")
        tag = tag.strip() or None
        if tag and len(tag) > TAG_MAX:
            raise ValueError(f"tag는 {TAG_MAX}자 이하로 써 주세요.")
    return name, message, tag


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status, message):
        self.send_json(status, {"error": message})

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/entries":
            q = parse_qs(url.query).get("q", [""])[0].strip()
            self.send_json(200, {"entries": list_entries(q or None)})
        elif url.path in ("/", "/index.html") and INDEX_PATH.exists():
            self.send_file(INDEX_PATH, "text/html; charset=utf-8")
        elif url.path.startswith("/static/"):
            # static 폴더 밖의 파일은 내보내지 않아요.
            path = (STATIC_DIR / url.path[len("/static/"):]).resolve()
            content_type = STATIC_TYPES.get(path.suffix.lower())
            if content_type and path.is_file() and path.is_relative_to(STATIC_DIR.resolve()):
                self.send_file(path, content_type)
            else:
                self.send_error_json(404, "없는 주소예요.")
        else:
            self.send_error_json(404, "없는 주소예요.")

    def send_file(self, path, content_type):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != "/api/entries":
            self.send_error_json(404, "없는 주소예요.")
            return
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";")[0].strip().lower() != "application/json":
            self.send_error_json(415, "Content-Type은 application/json이어야 해요.")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self.send_error_json(400, "Content-Length가 올바르지 않아요.")
            return
        if length > MAX_BODY:
            self.send_error_json(413, "본문이 8KiB를 넘었어요.")
            return
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_error_json(400, "JSON 형식이 깨졌어요.")
            return
        try:
            name, message, tag = validate(data)
        except ValueError as e:
            self.send_error_json(400, str(e))
            return
        self.send_json(201, {"entry": create_entry(name, message, tag)})

    def do_PUT(self):
        self.send_error_json(404, "없는 주소예요.")

    do_DELETE = do_PATCH = do_PUT


def main():
    parser = argparse.ArgumentParser(description="칭찬 릴레이 서버")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    args = parser.parse_args()
    init_db()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"칭찬 릴레이 서버: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
