"""DLQ record writer. Called by every script on unrecovered failure before re-raising."""
import json
import os
from datetime import datetime, timezone


def _dlq_path():
    return os.path.join(os.environ.get("RUNNER_TEMP", "."), "failed_contacts.json")


def append_dlq(contact_id: str, contact_email: str, failed_step: str, error_message: str) -> None:
    """Append one failure record to the DLQ array file. Safe to call multiple times."""
    record = {
        "contact_id": str(contact_id),
        "contact_email": str(contact_email),
        "failed_step": str(failed_step),
        "error_message": str(error_message)[:2000],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    path = _dlq_path()
    existing = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
                if isinstance(data, list):
                    existing = data
        except Exception:
            pass
    existing.append(record)
    with open(path, "w") as f:
        json.dump(existing, f, indent=2)


def write_dlq(contact_id: str, contact_email: str, failed_step: str, error_message: str) -> None:
    """Backwards-compatible alias for append_dlq."""
    append_dlq(contact_id, contact_email, failed_step, error_message)
