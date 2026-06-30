#!/usr/bin/env python3
"""fetch_chorus.py — Phase 2: extract Chorus AI transcript IDs from meeting notes and fetch transcripts."""
import os
import sys
import re
import logging

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from api_client import chorus_retry
from dlq_writer import write_dlq
from file_io import write_json, read_json

CONTACT_ID = os.environ.get("CONTACT_ID", "")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")
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
    print(f"[fetch_chorus] processing contact_id={CONTACT_ID}", flush=True)
    hubspot_data = read_json("hubspot_contact.json")
    meetings = hubspot_data.get("meeting_engagements", [])
    chorus_ids = extract_chorus_ids(meetings)
    print(f"[fetch_chorus] found {len(chorus_ids)} Chorus IDs", flush=True)
    if not chorus_ids:
        write_json("chorus_transcripts.json", SENTINEL)
        print("[fetch_chorus] no Chorus IDs — wrote sentinel", flush=True)
        return
    token = os.environ.get("CHORUS_API_TOKEN", "")
    if not token:
        print("[fetch_chorus] WARNING: CHORUS_API_TOKEN not set — writing sentinel", flush=True)
        write_json("chorus_transcripts.json", SENTINEL)
        return
    result = fetch_chorus_transcripts(chorus_ids, token)
    write_json("chorus_transcripts.json", result)
    print(f"[fetch_chorus] transcript_available={result['transcript_available']}, conversations={len(result['conversations'])}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        write_dlq(CONTACT_ID, CONTACT_EMAIL, "fetch_chorus", str(e))
        write_json("chorus_transcripts.json", SENTINEL)
        raise SystemExit(1)
