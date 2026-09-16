from __future__ import annotations

from pathlib import Path
import json

from copilot.human_eval.metrics import compute_metrics
from copilot.human_eval.queue import create_run_from_session, export_run, run_status
from copilot.human_eval.server import DEFAULT_HOST, DEFAULT_PORT, serve
from copilot.human_eval.store import EvalStore


def handle_human_eval(args, evidence: Path) -> int:
    action = (args.eval_argv or ["status"])[0]
    run_id = args.eval_run or "session_run1"
    if action == "create":
        run = create_run_from_session(
            args.eval_run or "session_run1",
            evidence=evidence,
            seed=int(args.seed or 20260915),
        )
        payload = run_status(run.evaluation_run_id)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if action == "status":
        store = EvalStore()
        target = run_id if store.load_run(run_id) else (store.list_run_ids()[-1] if store.list_run_ids() else run_id)
        payload = run_status(target)
        if payload.get("ok"):
            payload["metrics"] = compute_metrics(target)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload.get("ok") else 2
    if action == "export":
        target = (args.eval_argv[1] if len(args.eval_argv) > 1 else run_id)
        rows = export_run(target)
        out = evidence / "human_eval" / target / "export.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else "")
        out.write_text(text, encoding="utf-8")
        print(json.dumps({"ok": True, "path": str(out), "n": len(rows)}, indent=2))
        return 0
    if action == "serve":
        store = EvalStore()
        if args.eval_run:
            run_id = args.eval_run
        elif store.list_run_ids():
            run_id = store.list_run_ids()[-1]
        else:
            created = create_run_from_session("session_run1", evidence=evidence)
            run_id = created.evaluation_run_id
        counts = run_status(run_id)
        url = f"http://{args.host}:{args.port}/"
        print(json.dumps({
            "ok": True,
            "url": url,
            "how_to_start": f"python -m copilot.cli human-eval serve --run {run_id}",
            "evaluation_run_id": run_id,
            "queue_size": counts.get("queue_size"),
            "completed": counts.get("completed"),
            "ASTRA CALLS": 0,
            "MUSICAL WRITES": 0,
        }, indent=2))
        serve(host=args.host, port=int(args.port), run_id=run_id, blocking=True)
        return 0
    print(json.dumps({"ok": False, "error": f"unknown human-eval command {action}"}))
    return 2
