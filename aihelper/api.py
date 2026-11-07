"""Локальный HTTP API без внешних зависимостей."""
import json
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .domain import AppError, MAX_BODY, parse_json
from .service import generate_card


def make_server(provider, port=8000):
    generation_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, format, *args):
            pass

        def reply(self, status, data, content_type="application/json; charset=utf-8"):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def local_request(self):
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            if host not in allowed or (origin and origin != f"http://{host}"):
                self.reply(403, {"error": {"message": "Разрешены только локальные запросы."}})
                return False
            return True

        def do_GET(self):
            if not self.local_request():
                return
            assets = {
                "/": ("index.html", "text/html"),
                "/app.js": ("app.js", "text/javascript"),
                "/style.css": ("style.css", "text/css"),
            }
            if self.path in assets:
                name, mime = assets[self.path]
                body = (Path(__file__).parent / "static" / name).read_bytes()
                self.reply(200, body, mime + "; charset=utf-8")
            elif self.path == "/health":
                self.reply(200, {"status": "ok"})
            else:
                self.reply(404, {"error": {"message": "Метод не найден."}})

        def do_POST(self):
            if not self.local_request():
                return
            if self.path != "/generate":
                self.reply(404, {"error": {"message": "Метод не найден."}})
                return
            try:
                if self.headers.get_content_type() != "application/json":
                    raise AppError("content_type", "Нужен Content-Type: application/json.", 415)
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    raise AppError("content_length", "Некорректная длина запроса.")
                if not 0 < length <= MAX_BODY:
                    raise AppError("body_size", "Размер запроса должен быть от 1 байта до 64 КБ.", 413)
                data = parse_json(self.rfile.read(length))
                if not generation_lock.acquire(blocking=False):
                    raise AppError("busy", "Предыдущий запрос ещё выполняется.", 429)
                try:
                    card = generate_card(data, provider)
                finally:
                    generation_lock.release()
                self.reply(200, card)
            except AppError as exc:
                self.reply(exc.status, exc.as_dict())
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                return
            except Exception:
                self.reply(500, {"error": {"message": "Внутренняя ошибка сервера."}})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
