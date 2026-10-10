"""
Audit trail: an append-only log of every agent action.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "audit_trail.jsonl"


def log_event(run_id, actor, action, inputs=None, outputs=None, model=None):
    """Append one event to the audit trail."""
    LOG_FILE.parent.mkdir(exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": run_id,
        "actor": actor,          # agent name, or a person's name in Phase 6
        "action": action,
        "model": model,          # which AI model was used, if any
        "inputs": inputs,
        "outputs": outputs,
    }
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")