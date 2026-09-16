from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import json
import mimetypes
import threading

import numpy as np
import soundfile as sf

from copilot.audio.file_hash import sha256_file
from copilot.human_eval.blind import assert_blind, public_case
from copilot.human_eval.queue import export_run, run_status
from copilot.human_eval.schema import (
    CaseStatus,
    EvalMode,
    HumanResponse,
    LabelRevision,
    SkipReason,
)
from copilot.human_eval.store import EvalStore, label_hash, now_iso

STATIC_DIR = Path(__file__).resolve().parent / "static"
FATIGUE_EVERY = 20
DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"


class EvalState:
    def __init__(self) -> None:
        self.store = EvalStore()
        self.lock = threading.Lock()
        self.session_completes: dict[str, int] = {}
        self.autoplay: bool = True
        self.active_run_id: str | None = None


STATE = EvalState()


def _json(handler: BaseHTTPRequestHandler, code: int, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _active_run() -> str:
    if STATE.active_run_id:
        return STATE.active_run_id
    ids = STATE.store.list_run_ids()
    if not ids:
        raise FileNotFoundError("no eval run")
    return ids[-1]


def _ordered(run_id: str):
    return STATE.store.load_cases(run_id)


def _index_of(cases, case_id: str) -> int:
    for i, case in enumerate(cases):
        if case.case_id == case_id:
            return i
    return -1


def _remaining(cases) -> int:
    return sum(
        1
        for case in cases
        if case.status in {CaseStatus.PENDING, CaseStatus.IN_PROGRESS}
    )


def playback_wav_bytes(path: Path) -> bytes:
    """Listen-ready stereo. Silent-right (or silent-left) taps play in both ears.

    Does not rewrite capture files. Hash binding stays on the original WAV.
    """
    data, samplerate = sf.read(str(path), always_2d=True)
    if data.shape[1] >= 2:
        left = data[:, 0]
        right = data[:, 1]
        left_rms = float(np.sqrt(np.mean(np.square(left.astype(np.float64)))))
        right_rms = float(np.sqrt(np.mean(np.square(right.astype(np.float64)))))
        if left_rms > 1e-8 and right_rms <= 1e-8:
            data = np.column_stack((left, left))
        elif right_rms > 1e-8 and left_rms <= 1e-8:
            data = np.column_stack((right, right))
    buf = BytesIO()
    sf.write(buf, data, samplerate, format="WAV")
    return buf.getvalue()


def _audio_mismatch(case) -> str | None:
    if not case.stimulus.assets:
        return "AUDIO_MISSING"
    wav = Path(case.stimulus.assets[0].path)
    digest = sha256_file(wav)
    if not digest:
        return "AUDIO_MISSING"
    if digest != case.audio_sha256:
        return "AUDIO_HASH_MISMATCH"
    return None


def _public(case, cases):
    index = _index_of(cases, case.case_id) + 1
    payload = public_case(
        case,
        index=index,
        total=len(cases),
        remaining=_remaining(cases),
    )
    assert_blind(payload)
    return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "CopilotHumanEval/1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path in {"/", "/index.html"}:
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path.removeprefix("/static/"))
            if path == "/api/status":
                return self._status()
            if path == "/api/next":
                return self._next()
            if path.startswith("/api/runs/") and path.endswith("/audio"):
                return self._audio(path)
            if path.startswith("/api/runs/") and "/cases/" in path:
                return self._get_case(path)
            self.send_error(404)
        except Exception as exc:
            _json(self, 500, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            body = _body(self)
            if path == "/api/draft":
                return self._draft(body)
            if path == "/api/save":
                return self._save(body)
            if path == "/api/skip":
                return self._skip(body)
            if path == "/api/lock-case":
                return self._lock_case(body)
            if path == "/api/lock-batch":
                return self._lock_batch(body)
            if path == "/api/settings":
                return self._settings(body)
            if path == "/api/session":
                return self._session(body)
            if path == "/api/go":
                return self._go(body)
            self.send_error(404)
        except Exception as exc:
            _json(self, 500, {"ok": False, "error": str(exc)})

    def _static(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        if target.parent != STATIC_DIR.resolve():
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _status(self) -> None:
        try:
            run_id = _active_run()
        except FileNotFoundError:
            return _json(self, 404, {"ok": False, "error": "no eval run"})
        payload = run_status(run_id, store=STATE.store)
        payload["autoplay"] = STATE.autoplay
        payload["url"] = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/"
        _json(self, 200, payload)

    def _next(self) -> None:
        with STATE.lock:
            run_id = _active_run()
            cases = _ordered(run_id)
            current = STATE.store.first_incomplete(run_id)
            if current is None:
                counts = STATE.store.counts(run_id)
                return _json(
                    self,
                    200,
                    {
                        "done": True,
                        "queue_size": counts["total"],
                        "completed": counts["labeled"],
                        "skipped": counts["skipped"],
                        "locked": counts["locked"],
                    },
                )
            if current.status == CaseStatus.PENDING:
                current.status = CaseStatus.IN_PROGRESS
                current.started_at = current.started_at or now_iso()
                STATE.store.save_case(current)
            run = STATE.store.load_run(run_id)
            payload = _public(current, cases)
            payload["done"] = False
            payload["mode"] = (run.default_mode.value if run else "QUICK")
            payload["autoplay"] = STATE.autoplay
            payload["fatigue"] = False
            _json(self, 200, payload)

    def _parse_case(self, path: str) -> tuple[str, str]:
        # /api/runs/{run}/cases/{id}[/audio]
        parts = path.strip("/").split("/")
        return parts[2], parts[4]

    def _get_case(self, path: str) -> None:
        run_id, case_id = self._parse_case(path)
        case = STATE.store.load_case(run_id, case_id)
        if case is None:
            return _json(self, 404, {"ok": False, "error": "missing case"})
        cases = _ordered(run_id)
        payload = _public(case, cases)
        payload["mode"] = (STATE.store.load_run(run_id).default_mode.value if STATE.store.load_run(run_id) else "QUICK")
        payload["autoplay"] = STATE.autoplay
        _json(self, 200, payload)

    def _audio(self, path: str) -> None:
        run_id, case_id = self._parse_case(path)
        case = STATE.store.load_case(run_id, case_id)
        if case is None or not case.stimulus.assets:
            self.send_error(404)
            return
        wav = Path(case.stimulus.assets[0].path)
        if not wav.is_file():
            self.send_error(404)
            return
        digest = sha256_file(wav)
        if digest != case.audio_sha256:
            return _json(self, 409, {"ok": False, "error": "AUDIO_HASH_MISMATCH"})
        data = playback_wav_bytes(wav)
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _draft(self, body: dict[str, Any]) -> None:
        with STATE.lock:
            run_id = str(body.get("evaluation_run_id") or _active_run())
            case = STATE.store.load_case(run_id, str(body["case_id"]))
            if case is None:
                return _json(self, 404, {"ok": False})
            if case.status == CaseStatus.LOCKED:
                return _json(self, 409, {"ok": False, "error": "LOCKED"})
            case.draft = HumanResponse.model_validate(body.get("response") or {})
            case.draft.updated_at = now_iso()
            if case.status == CaseStatus.PENDING:
                case.status = CaseStatus.IN_PROGRESS
                case.started_at = case.started_at or now_iso()
            STATE.store.save_case(case)
            _json(self, 200, {"ok": True, "autosaved": True})

    def _save(self, body: dict[str, Any]) -> None:
        with STATE.lock:
            run_id = str(body.get("evaluation_run_id") or _active_run())
            case = STATE.store.load_case(run_id, str(body["case_id"]))
            if case is None:
                return _json(self, 404, {"ok": False})
            if case.status == CaseStatus.LOCKED:
                return _json(self, 409, {"ok": False, "error": "LOCKED"})
            response = HumanResponse.model_validate(body.get("response") or {})
            if response.overall_feel is None or response.would_change is None:
                return _json(
                    self,
                    400,
                    {"ok": False, "error": "overall_feel and would_change required"},
                )
            response.updated_at = now_iso()
            case.response = response
            case.draft = response
            case.status = CaseStatus.COMPLETED
            case.completed_at = now_iso()
            STATE.store.save_case(case)
            session_id = str(body.get("session_id") or "anon")
            STATE.session_completes[session_id] = STATE.session_completes.get(session_id, 0) + 1
            STATE.store.append_session(
                run_id,
                {
                    "event": "completed",
                    "case_id": case.case_id,
                    "session_id": session_id,
                    "n": STATE.session_completes[session_id],
                },
            )
            fatigue = STATE.session_completes[session_id] > 0 and STATE.session_completes[session_id] % FATIGUE_EVERY == 0
            cases = _ordered(run_id)
            nxt = STATE.store.first_incomplete(run_id)
            payload: dict[str, Any] = {
                "ok": True,
                "saved": True,
                "fatigue": fatigue,
                "completed_in_session": STATE.session_completes[session_id],
            }
            if nxt is None:
                payload["done"] = True
            else:
                if nxt.status == CaseStatus.PENDING:
                    nxt.status = CaseStatus.IN_PROGRESS
                    nxt.started_at = nxt.started_at or now_iso()
                    STATE.store.save_case(nxt)
                payload["done"] = False
                payload["next"] = _public(nxt, cases)
                payload["next"]["mode"] = (
                    STATE.store.load_run(run_id).default_mode.value
                    if STATE.store.load_run(run_id)
                    else "QUICK"
                )
                payload["next"]["autoplay"] = STATE.autoplay
            _json(self, 200, payload)

    def _skip(self, body: dict[str, Any]) -> None:
        with STATE.lock:
            run_id = str(body.get("evaluation_run_id") or _active_run())
            case = STATE.store.load_case(run_id, str(body["case_id"]))
            if case is None:
                return _json(self, 404, {"ok": False})
            if case.status == CaseStatus.LOCKED:
                return _json(self, 409, {"ok": False, "error": "LOCKED"})
            case.status = CaseStatus.SKIPPED
            reason = body.get("reason") or "OTHER"
            case.skip_reason = SkipReason(reason)
            case.skip_notes = str(body.get("notes") or "")
            case.completed_at = now_iso()
            STATE.store.save_case(case)
            STATE.store.append_session(
                run_id, {"event": "skipped", "case_id": case.case_id, "reason": reason}
            )
            nxt = STATE.store.first_incomplete(run_id)
            cases = _ordered(run_id)
            if nxt is None:
                return _json(self, 200, {"ok": True, "done": True})
            if nxt.status == CaseStatus.PENDING:
                nxt.status = CaseStatus.IN_PROGRESS
                nxt.started_at = nxt.started_at or now_iso()
                STATE.store.save_case(nxt)
            payload = {"ok": True, "done": False, "next": _public(nxt, cases)}
            payload["next"]["autoplay"] = STATE.autoplay
            _json(self, 200, payload)

    def _lock_case(self, body: dict[str, Any]) -> None:
        with STATE.lock:
            run_id = str(body.get("evaluation_run_id") or _active_run())
            case = STATE.store.load_case(run_id, str(body["case_id"]))
            if case is None:
                return _json(self, 404, {"ok": False, "error": "missing case"})
            if body.get("response"):
                case.response = HumanResponse.model_validate(body["response"])
                case.draft = case.response
                case.completed_at = case.completed_at or now_iso()
            if case.response is None:
                return _json(self, 400, {"ok": False, "error": "no response"})
            mismatch = _audio_mismatch(case)
            if mismatch:
                return _json(self, 409, {"ok": False, "error": mismatch})
            digest = label_hash(response=case.response, audio_sha256=case.audio_sha256)
            revision = len(case.revisions) + 1
            stamp = now_iso()
            case.revisions.append(
                LabelRevision(
                    revision=revision,
                    human_label_hash=digest,
                    audio_sha256=case.audio_sha256,
                    created_at=case.completed_at or stamp,
                    locked_at=stamp,
                    response=case.response,
                )
            )
            case.human_label_hash = digest
            case.locked_at = stamp
            case.status = CaseStatus.LOCKED
            STATE.store.save_case(case)
            _json(self, 200, {"ok": True, "human_label_hash": digest, "revision": revision})

    def _lock_batch(self, body: dict[str, Any]) -> None:
        with STATE.lock:
            run_id = str(body.get("evaluation_run_id") or _active_run())
            run = STATE.store.load_run(run_id)
            locked = 0
            for case in STATE.store.load_cases(run_id):
                if case.status == CaseStatus.COMPLETED and case.response is not None:
                    if _audio_mismatch(case):
                        continue
                    digest = label_hash(response=case.response, audio_sha256=case.audio_sha256)
                    stamp = now_iso()
                    case.revisions.append(
                        LabelRevision(
                            revision=len(case.revisions) + 1,
                            human_label_hash=digest,
                            audio_sha256=case.audio_sha256,
                            created_at=case.completed_at or stamp,
                            locked_at=stamp,
                            response=case.response,
                        )
                    )
                    case.human_label_hash = digest
                    case.locked_at = stamp
                    case.status = CaseStatus.LOCKED
                    STATE.store.save_case(case)
                    locked += 1
            if run is not None:
                run.locked_at = now_iso()
                STATE.store.save_run(run)
            _json(self, 200, {"ok": True, "locked": locked, "all_required_locked": STATE.store.all_required_locked(run_id)})

    def _settings(self, body: dict[str, Any]) -> None:
        if "autoplay" in body:
            STATE.autoplay = bool(body["autoplay"])
        _json(self, 200, {"ok": True, "autoplay": STATE.autoplay})

    def _session(self, body: dict[str, Any]) -> None:
        action = str(body.get("action") or "continue")
        session_id = str(body.get("session_id") or "anon")
        if action == "break":
            STATE.store.append_session(_active_run(), {"event": "break", "session_id": session_id})
        elif action == "continue":
            STATE.session_completes[session_id] = 0
            STATE.store.append_session(_active_run(), {"event": "continue", "session_id": session_id})
        _json(self, 200, {"ok": True})

    def _go(self, body: dict[str, Any]) -> None:
        direction = str(body.get("direction") or "next")
        run_id = str(body.get("evaluation_run_id") or _active_run())
        case_id = str(body.get("case_id") or "")
        cases = _ordered(run_id)
        idx = _index_of(cases, case_id)
        if direction == "prev":
            idx = max(0, idx - 1)
        else:
            idx = min(len(cases) - 1, idx + 1)
        target = cases[idx]
        payload = _public(target, cases)
        payload["mode"] = (STATE.store.load_run(run_id).default_mode.value if STATE.store.load_run(run_id) else "QUICK")
        payload["autoplay"] = STATE.autoplay
        _json(self, 200, payload)


class EvalHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False


def serve(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    run_id: str | None = None,
    blocking: bool = True,
) -> ThreadingHTTPServer:
    if run_id:
        STATE.active_run_id = run_id
    httpd = EvalHTTPServer((host, port), Handler)
    if blocking:
        httpd.serve_forever()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd
