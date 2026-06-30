#!/usr/bin/env python3
"""write_hubspot.py — Phase 5: write 8 email subjects, bodies, generated date, and CRM note to HubSpot."""
import os
import sys
from datetime import datetime, timezone, date

import hubspot
from hubspot.crm.contacts import SimplePublicObjectInput, ApiException
import requests as req_lib
from tenacity import retry, retry_if_exception

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from api_client import hubspot_retry
from dlq_writer import write_dlq
from file_io import read_json

CONTACT_ID = os.environ.get("CONTACT_ID", "")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")

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
    write_dlq(CONTACT_ID, CONTACT_EMAIL, "write_hubspot", "Script started")

    output = read_json("campaign_output.json")
    print(f"[write_hubspot] campaign_output.json loaded: keys={list(output.keys())}")

    client = hubspot.Client.create(access_token=os.environ["HUBSPOT_API_KEY"])
    headers = {"Authorization": f"Bearer {os.environ['HUBSPOT_API_KEY']}"}

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

    print(f"[write_hubspot] Properties to write ({len(properties)}): {list(properties.keys())}")

    try:
        _hs_update(client, CONTACT_ID, SimplePublicObjectInput(properties=properties))
        print(f"[write_hubspot] Contact {CONTACT_ID} updated ({len(properties)} fields)")

        try:
            note_body = build_note_body(output)
            note_payload = {
                "properties": {
                    "hs_note_body": note_body,
                    "hs_timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
                },
                "associations": [
                    {
                        "to": {"id": str(CONTACT_ID)},
                        "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}],
                    }
                ],
            }
            note_resp = _hs_post_note(headers, note_payload)
            print(f"[write_hubspot] Note created: ID {note_resp.json().get('id')}")
        except Exception as e:
            print(f"[write_hubspot] WARNING: note creation failed (properties already written): {e}", file=sys.stderr)

        print(f"[write_hubspot] Write-back complete: {len(properties)} properties + note on contact {CONTACT_ID}")

    except ApiException as exc:
        write_dlq(CONTACT_ID, CONTACT_EMAIL, "write_hubspot", str(exc))
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        write_dlq(CONTACT_ID, CONTACT_EMAIL, "write_hubspot", str(exc))
        raise SystemExit(1)
