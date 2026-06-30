#!/usr/bin/env python3
"""fetch_chorus.py — Phase 2: extract Chorus AI transcript IDs from meeting notes and fetch transcripts."""
import json
import os
import sys
import re
import logging

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from api_client import chorus_retry
from dlq_writer import write_dlq, append_dlq
from file_io import write_json, read_json

CONTACT_IDS = os.environ.get("CONTACT_IDS", "[]")
CONTACT_EMAILS = os.environ.get("CONTACT_EMAILS", "[]")
SENTINEL = {"transcript_available": False, "conversations": []}
CHORUS_ID_PATTERN = re.compile(r"chorus\.ai/meeting/([\w-]+)")


def extract_chorus_ids(meeting_engagements: list) -> list:
    ids = []
    for meeting in meeting_engagements:
        props = meeting if isinstance(meeting, dict) else (meeting.properties if hasattr(meeting, "properties") else {})
        for field in ["hs_meeting_body", "hs_internal_meeting_notes"]:
            text = props.get(field) or ""
            ids.extend(CHORUS_ID_PATTERN.findall(text))
    if not ids:
        logging.debug("[fetch_chorus] no Chorus IDs found in meeting notes")
    return list(dict.fromkeys(ids))


@chorus_retry
def _fetch_single_transcript(conversation_id: str, token: str) -> dict:
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
    }
    url = f"https://chorus.ai/api/v3/engagements/{conversation_id}"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_chorus_transcripts(chorus_ids: list, token: str) -> dict:
    if not chorus_ids:
        return SENTINEL
    conversations = []
    any_success = False
    for cid in chorus_ids:
        try:
            data = _fetch_single_transcript(cid, token)
            conversations.append({
                "conversation_id": cid,
                "meeting_title": data.get("title") or data.get("meeting_title") or "",
                "date_time": data.get("date_time") or data.get("dateTime") or "",
                "transcript": data.get("transcript") or "",
                "available": True,
            })
            any_success = True
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            if status == 401:
                print(f"[fetch_chorus] WARNING: Chorus auth failure (401) for conversation_id={cid} — token may be invalid", flush=True)
            elif status == 404:
                print(f"[fetch_chorus] INFO: Chorus conversation {cid} not found (404) — skipping", flush=True)
            else:
                print(f"[fetch_chorus] INFO: Chorus HTTP error {status} for conversation_id={cid} — skipping", flush=True)
            conversations.append({"conversation_id": cid, "available": False, "error_status": status})
        except requests.exceptions.Timeout:
            print(f"[fetch_chorus] INFO: Chorus request timeout for conversation_id={cid} — skipping", flush=True)
            conversations.append({"conversation_id": cid, "available": False, "error_status": "timeout"})
        except Exception as e:
            print(f"[fetch_chorus] INFO: Chorus connection error for conversation_id={cid} — skipping", flush=True)
            conversations.append({"conversation_id": cid, "available": False, "error_status": "connection_error"})
    return {"transcript_available": any_success, "conversations": conversations}


def main():
    contact_ids = json.loads(os.environ.get("CONTACT_IDS", "[]"))
    contact_emails_list = json.loads(os.environ.get("CONTACT_EMAILS", "[]"))
    email_map = dict(zip(contact_ids, contact_emails_list))

    hubspot_data = read_json("hubspot_contacts.json")
    token = os.environ.get("CHORUS_API_TOKEN", "")

    results = {"_contact_ids": contact_ids}

    for contact_id in contact_ids:
        contact_data = hubspot_data.get(contact_id, {})
        meetings = contact_data.get("meeting_engagements", [])
        chorus_ids = extract_chorus_ids(meetings)
        print(f"[fetch_chorus] contact_id={contact_id}: found {len(chorus_ids)} Chorus IDs", flush=True)

        if not chorus_ids or not token:
            if not token and chorus_ids:
                print(f"[fetch_chorus] WARNING: CHORUS_API_TOKEN not set — writing sentinel for {contact_id}", flush=True)
            results[contact_id] = SENTINEL
        else:
            results[contact_id] = fetch_chorus_transcripts(chorus_ids, token)

    write_json("chorus_transcripts.json", results)
    print(f"[fetch_chorus] chorus_transcripts.json written for {len(contact_ids)} contacts", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Write sentinel for all contacts on catastrophic failure
        try:
            contact_ids = json.loads(os.environ.get("CONTACT_IDS", "[]"))
            contact_emails_list = json.loads(os.environ.get("CONTACT_EMAILS", "[]"))
            for cid, cem in zip(contact_ids, contact_emails_list):
                append_dlq(cid, cem, "fetch_chorus", str(e))
        except Exception:
            pass
        raise SystemExit(1)
