"""DLQ record writer. Called by every script on unrecovered failure before re-raising."""
import json
import os
from datetime import datetime, timezone


def write_dlq(contact_id: str, contact_email: str, failed_step: str, error_message: str) -> None:
    record = {
        "contact_id": str(contact_id),
        "contact_email": str(contact_email),
        "failed_step": str(failed_step),
        "error_message": str(error_message)[:2000],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    path = os.path.join(os.environ.get("RUNNER_TEMP", "."), "failed_contacts.json")
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
