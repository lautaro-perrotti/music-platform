from __future__ import annotations

import json
import os
import uuid

import websocket


SONIQ_WS_URL = os.getenv("SONIQ_WS_URL", "ws://127.0.0.1:9123")


def _rpc(ws, method: str, params: dict):
    rid = str(uuid.uuid4())
    ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
    while True:
        data = json.loads(ws.recv())
        if data.get("id") != rid:
            continue
        if data.get("error"):
            raise RuntimeError(data["error"])
        return data.get("result")


def run() -> dict:
    ws = websocket.create_connection(SONIQ_WS_URL, timeout=5)
    try:
        hello = _rpc(ws, "soniq.hello", {"version": "0.1.0"})
        full = _rpc(ws, "soniq.vst.schema", {"includeMidiPassthrough": True})
        filtered = _rpc(ws, "soniq.vst.schema", {})
        before = _rpc(ws, "soniq.vst.read", {"indices": [205]})
        target = 0.61 if float(before[0]["value"]) < 0.6 else 0.41
        wr = _rpc(ws, "soniq.vst.write", {"writes": [{"index": 205, "value": target}]})
        after = _rpc(ws, "soniq.vst.read", {"indices": [205]})
        rep = {
            "ok": True,
            "ws_url": SONIQ_WS_URL,
            "hello": hello,
            "schema_full_count": int(full.get("paramCount", 0)),
            "schema_filtered_count": int(filtered.get("paramCount", 0)),
            "write_index": 205,
            "before": before[0],
            "write": wr,
            "after": after[0],
        }
        rep["ok"] = bool(rep["schema_full_count"] >= 2000 and abs(float(after[0]["value"]) - float(target)) < 1e-6)
        return rep
    finally:
        ws.close()


if __name__ == "__main__":
    out = run()
    print(json.dumps(out, ensure_ascii=False, indent=2))
