from __future__ import annotations

import argparse
import json
import mimetypes
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from copilot.studio.service import GenerationBackendNotAvailable, ProduceExecutionBlocked, StudioService


def _default_data_dir() -> Path:
    configured = os.environ.get("COPILOT_STUDIO_DATA_DIR")
    return Path(configured) if configured else Path("runtime") / "music-studio"


class StudioHandler(BaseHTTPRequestHandler):
    service: StudioService
    static_root: Path

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[music-studio] {fmt % args}", flush=True)

    def _headers(self, status: int = 200, content_type: str = "application/json; charset=utf-8", length: int | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:8765")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Idempotency-Key, Last-Event-ID")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def _json(self, payload: object, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._headers(status, length=len(data))
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_OPTIONS(self) -> None:
        self._headers(204, length=0)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        try:
            if path == "/":
                return self._serve_static()
            if path == "/ui" or path.startswith("/ui/"):
                return self._serve_ui(path)
            if path == "/api/health":
                return self._json({"ok": True, "service": "music-studio", "mode": self.service.mode, "musical_writes": 0})
            if path == "/api/projects":
                return self._json({"projects": [p.model_dump(mode="json") for p in self.service.list_projects()]})
            if path == "/api/ableton/status":
                return self._json(self.service.ableton_status())
            if path == "/api/produce/capabilities":
                return self._json(self.service.produce_capabilities())
            parts = [part for part in path.split("/") if part]
            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "variations":
                return self._json(self.service.list_variations(parts[2]))
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "projects":
                project_id = parts[2]
                return self._json(self.service.project_snapshot(project_id))
            if len(parts) == 4 and parts[:3] == ["api", "projects", parts[2]] and parts[3] == "workspace":
                return self._json(self.service.workspace_snapshot(parts[2]))
            if len(parts) == 4 and parts[:3] == ["api", "projects", parts[2]] and parts[3] in {"jobs", "versions"}:
                project_id = parts[2]
                snapshot = self.service.project_snapshot(project_id)
                return self._json({parts[3]: snapshot[parts[3]]})
            if len(parts) >= 3 and parts[:2] == ["api", "jobs"]:
                job_id = parts[2]
                if len(parts) == 3:
                    return self._json(self.service.get_job(job_id))
                if len(parts) == 4 and parts[3] == "events":
                    after = int((query.get("after") or ["0"])[0])
                    return self._json({"events": [e.model_dump(mode="json") for e in self.service.store.events_since(job_id, after)]})
                if len(parts) == 5 and parts[3] == "events" and parts[4] == "stream":
                    return self._stream_events(job_id, int((query.get("after") or ["0"])[0]))
                if len(parts) == 4 and parts[3] in {"cancel", "retry"}:
                    return self._json(self.service.cancel_job(job_id) if parts[3] == "cancel" else self.service.retry_job(job_id))
            if len(parts) == 4 and parts[:2] == ["api", "artifacts"] and parts[3] == "audio":
                return self._serve_audio(parts[2])
            self._json({"error": "NOT_FOUND"}, 404)
        except KeyError as exc:
            self._json({"error": "NOT_FOUND", "detail": str(exc)}, 404)
        except Exception as exc:
            self._json({"error": type(exc).__name__, "detail": str(exc)}, 400)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/api/projects":
                body = self._body()
                project = self.service.create_project(str(body.get("name") or "Untitled project"))
                return self._json(project.model_dump(mode="json"), 201)
            parts = [part for part in path.split("/") if part]
            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "generations":
                body = self._body()
                key = self.headers.get("Idempotency-Key") or body.get("idempotency_key")
                job, created = self.service.submit_generation(parts[2], body, idempotency_key=key)
                return self._json({"job": job.model_dump(mode="json"), "created": created}, 202 if created else 200)
            if len(parts) == 4 and parts[:2] == ["api", "candidates"] and parts[3] == "keep":
                version = self.service.keep_candidate(parts[2])
                return self._json(version.model_dump(mode="json"), 201)
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] in {"cancel", "retry"}:
                # Job lifecycle mutations are explicit POSTs.  Keep the operation
                # behind the service so the browser cannot invent a second state
                # machine or mutate the durable store directly.
                result = self.service.cancel_job(parts[2]) if parts[3] == "cancel" else self.service.retry_job(parts[2])
                return self._json(result)
            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "produce":
                return self._json(self.service.produce_generate(parts[2], self._body()), 202)
            if len(parts) == 4 and parts[:2] == ["api", "variations"]:
                return self._json(self.service.variation_action(parts[2], parts[3]))
            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "actions":
                body = self._body()
                return self._json(self.service.workspace_action(parts[2], str(body.get("action") or ""), body.get("payload") or {}), 200)
            self._json({"error": "NOT_FOUND"}, 404)
        except KeyError as exc:
            self._json({"error": "NOT_FOUND", "detail": str(exc)}, 404)
        except GenerationBackendNotAvailable as exc:
            self._json(exc.to_dict(), 501)
        except ProduceExecutionBlocked as exc:
            self._json(exc.to_dict(), 409)
        except ValueError as exc:
            self._json({"error": str(exc)}, 422)
        except Exception as exc:
            self._json({"error": type(exc).__name__, "detail": str(exc)}, 500)

    def _serve_static(self) -> None:
        path = self.static_root / "index.html"
        data = path.read_bytes()
        self._headers(200, "text/html; charset=utf-8", len(data))
        self.wfile.write(data)

    def _serve_ui(self, request_path: str) -> None:
        relative = unquote(request_path.removeprefix("/ui/") or "catalog.html")
        root = (self.static_root / "ui").resolve()
        target = (root / relative).resolve()
        if root not in target.parents and target != root:
            return self._json({"error": "NOT_FOUND"}, 404)
        if target.is_dir():
            target = target / "catalog.html"
        if not target.is_file():
            return self._json({"error": "NOT_FOUND"}, 404)
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type == "text/javascript":
            content_type = "text/javascript; charset=utf-8"
        elif content_type.startswith("text/"):
            content_type = f"{content_type}; charset=utf-8"
        data = target.read_bytes()
        self._headers(200, content_type, len(data))
        self.wfile.write(data)

    def _serve_audio(self, artifact_id: str) -> None:
        artifact = self.service.store.get_artifact(artifact_id)
        if artifact is None:
            return self._json({"error": "NOT_FOUND"}, 404)
        path = (self.service.data_dir / artifact.relative_path).resolve()
        if self.service.store.artifacts_root not in path.parents or not path.is_file():
            return self._json({"error": "ARTIFACT_UNAVAILABLE"}, 404)
        total = path.stat().st_size
        start, end = 0, total - 1
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            value = range_header[6:].split(",", 1)[0]
            left, _, right = value.partition("-")
            if left:
                start = int(left)
            if right:
                end = int(right)
            else:
                end = min(total - 1, start + 1024 * 1024 - 1)
            if start >= total or start > end:
                self._headers(416, length=0)
                return
        length = end - start + 1
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Type", artifact.mime_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _stream_events(self, job_id: str, after: int) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        deadline = time.monotonic() + 25.0
        cursor = after
        while time.monotonic() < deadline:
            events = self.service.store.events_since(job_id, cursor)
            if events:
                for event in events:
                    self.wfile.write(f"id: {event.sequence}\nevent: {event.event_type}\ndata: {event.model_dump_json()}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    cursor = event.sequence
            else:
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
            time.sleep(0.5)


def run_server(*, host: str = "127.0.0.1", port: int = 8765, data_dir: Path | None = None) -> int:
    service = StudioService(data_dir or _default_data_dir())
    handler = type("BoundStudioHandler", (StudioHandler,), {"service": service, "static_root": Path(__file__).parent / "static"})
    server = ThreadingHTTPServer((host, port), handler)
    print(json.dumps({"ok": True, "url": f"http://{host}:{port}/", "data_dir": str(service.data_dir), "musical_writes": 0}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Music Studio vertical slice")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args()
    return run_server(host=args.host, port=args.port, data_dir=args.data_dir)


if __name__ == "__main__":
    raise SystemExit(main())
