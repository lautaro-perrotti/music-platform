from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DurableJournal:
    """Append-only JSONL with flush + fsync. Incomplete last lines are skipped."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = self._max_seq()

    def _max_seq(self) -> int:
        seq = 0
        for record in self.read_all():
            seq = max(seq, int(record.get("seq", 0)))
        return seq

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        self._seq += 1
        payload = {
            **record,
            "seq": self._seq,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        line = json.dumps(payload, sort_keys=True, default=str)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return payload

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append(item)
        return records
