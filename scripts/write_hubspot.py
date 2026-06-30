#!/usr/bin/env python3
"""write_hubspot.py — Phase 5: write 8 email subjects, bodies, generated date, and CRM note to HubSpot."""
import json
import os
import sys
from datetime import datetime, timezone, date

import hubspot
from hubspot.crm.contacts import SimplePublicObjectInput, ApiException
import requests as req_lib
from tenacity import retry, retry_if_exception

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from api_client import hubspot_retry
from dlq_writer import write_dlq, append_dlq
from file_io import read_json, write_json

CONTACT_IDS = os.environ.get("CONTACT_IDS", "[]")
CONTACT_EMAILS = os.environ.get("CONTACT_EMAILS", "[]")

EMAIL_PROPS = {i: (f"subject_{i}", f"email_{i}") for i in range(1, 9)}

_req_retry = retry(
    retry=retry_if_exception(lambda exc: (
        hasattr(exc, "response") and exc.response is not None
        and exc.response.status_code in (429, 500, 502, 503, 504)
    )),
    reraise=True,
)


def safe_truncate(text, max_chars):
    if not isinstance(text, str):
        return text
    return text[:max_chars] if len(text) > max_chars else text


@hubspot_retry
def _hs_update(client, contact_id, input_obj):
    return client.crm.contacts.basic_api.update(
        contact_id=contact_id,
        simple_public_object_input=input_obj,
    )


@_req_retry
def _hs_post_note(headers, payload):
    resp = req_lib.post(
        "https://api.hubapi.com/crm/v3/objects/notes",
        headers=headers,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp


def h(tag, text):
    return f"<{tag}>{text}</{tag}>"


def build_note_body(output):
    generated = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    parts = [
        h("h3", "Staff Domain ASOTOS Campaign") +
        f"<div>Generated {generated}</div>"
    ]

    reasoning = output.get("reasoning")
    if reasoning and isinstance(reasoning, dict):
        state = reasoning.get("conversation_state", "")
        if state:
            parts.append(h("p", f"<strong>Conversation state:</strong> {state}"))
        signals = reasoning.get("observable_signals_used", "")
        if signals:
            parts.append(h("p", f"<strong>Observable signals:</strong> {signals}"))

    labels = {
        1: "Email 1 — Day 0 — Cold open",
        2: "Email 2 — Day 6 — Peer reframe",
        3: "Email 3 — Day 12 — Short direct",
        4: "Email 4 — Day 19 — Commercial case",
        5: "Email 5 — Day 26 — Proof",
        6: "Email 6 — Day 33 — Quality/security",
        7: "Email 7 — Day 40 — Specific scenario",
        8: "Email 8 — Day 50 — Soft exit",
    }
    email_lines = []
    for i in range(1, 9):
        email = output.get(f"email_{i}")
        if email and email.get("subject"):
            email_lines.append(f"<li><strong>{labels[i]}:</strong> {email['subject']}</li>")
    if email_lines:
        parts.append(h("h4", "Email Sequence") + f"<ul>{''.join(email_lines)}</ul>")

    return "<br><br>".join(parts)


def main():
    contact_ids = json.loads(os.environ.get("CONTACT_IDS", "[]"))
    contact_emails_list = json.loads(os.environ.get("CONTACT_EMAILS", "[]"))
    email_map = dict(zip(contact_ids, contact_emails_list))

    output_batch = read_json("campaign_output.json")

    client = hubspot.Client.create(access_token=os.environ["HUBSPOT_API_KEY"])
    headers = {"Authorization": f"Bearer {os.environ['HUBSPOT_API_KEY']}"}

    failures = []

    for contact_id in contact_ids:
        contact_email = email_map.get(contact_id, "")
        output = output_batch.get(contact_id)
        if not output:
            print(f"[write_hubspot] SKIP contact_id={contact_id} — no output in campaign_output.json (may have failed earlier)", flush=True)
            continue

        try:
            properties = {}
            for i, (subj_prop, body_prop) in EMAIL_PROPS.items():
                email = output.get(f"email_{i}")
                if not email:
                    continue
                if isinstance(email, dict):
                    properties[subj_prop] = safe_truncate(email.get("subject") or "", 1024)
                    body = (email.get("body") or "").replace("\n", "<br>")
                    properties[body_prop] = safe_truncate(body, 65000)
                else:
                    properties[body_prop] = safe_truncate(str(email), 65000)
            properties["asotos_generated_date"] = date.today().isoformat()

            _hs_update(client, contact_id, SimplePublicObjectInput(properties=properties))
            print(f"[write_hubspot] contact_id={contact_id} updated ({len(properties)} fields)", flush=True)

            try:
                note_body = build_note_body(output)
                note_payload = {
                    "properties": {
                        "hs_note_body": note_body,
                        "hs_timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
                    },
                    "associations": [{
                        "to": {"id": str(contact_id)},
                        "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}],
                    }],
                }
                note_resp = _hs_post_note(headers, note_payload)
                print(f"[write_hubspot] Note created for contact_id={contact_id}: ID {note_resp.json().get('id')}", flush=True)
            except Exception as e:
                print(f"[write_hubspot] WARNING: note creation failed for contact_id={contact_id} (properties written): {e}", file=sys.stderr)

        except Exception as e:
            print(f"[write_hubspot] ERROR contact_id={contact_id}: {e}", file=sys.stderr, flush=True)
            append_dlq(contact_id, contact_email, "write_hubspot", str(e))
            failures.append(contact_id)

    print(f"[write_hubspot] done: {len(contact_ids) - len(failures)}/{len(contact_ids)} succeeded", flush=True)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        try:
            for cid, cem in zip(json.loads(CONTACT_IDS), json.loads(CONTACT_EMAILS)):
                append_dlq(cid, cem, "write_hubspot", str(e))
        except Exception:
            pass
        raise SystemExit(1)
