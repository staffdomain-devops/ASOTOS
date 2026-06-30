#!/usr/bin/env python3
"""compute_campaign_tokens.py — Phase 3: Token Computation.

Reads hubspot_contacts.json and chorus_transcripts.json from RUNNER_TEMP.
Computes all prompt tokens with freshness tiers and secondary contact selection.
Validates tokens via Jinja2 StrictUndefined test render (result discarded).
Writes campaign_tokens.json to RUNNER_TEMP (batch dict keyed by contact_id).
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from file_io import read_json, write_json
from dlq_writer import write_dlq, append_dlq

from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError

CONTACT_IDS = os.environ.get("CONTACT_IDS", "[]")
CONTACT_EMAILS = os.environ.get("CONTACT_EMAILS", "[]")
REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SENTINEL_CHORUS = {"transcript_available": False, "conversations": []}

_EXCLUDED_TITLE_KEYWORDS = [
    "executive assistant", "personal assistant", " pa ", "pa,", "pa)",
    "admin", "administrator", "coordinator", "receptionist",
    "office manager", " ea ", "ea,", "ea)", "secretary",
]
_CSUITE_KEYWORDS = [
    "ceo", "coo", "cfo", "cto", "cmo", "founder", "co-founder",
    "managing director", " md ", "md,", "md)", "president", "owner",
    "partner", "principal",
]
_DIRECTOR_KEYWORDS = [
    "director", "head of", "vp", "vice president", "general manager",
    " gm ", "gm,", "gm)",
]


def _parse_iso(ts: str):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _age_days(iso_ts: str) -> int:
    dt = _parse_iso(iso_ts)
    if dt is None:
        return -1
    return (datetime.now(timezone.utc) - dt).days


def _freshness_label(iso_ts: str) -> str:
    age = _age_days(iso_ts)
    if age < 0:
        return "unknown date"
    if age < 60:
        return "recently"
    if age < 183:
        return "a few months ago"
    if age < 365:
        return "earlier this year"
    return "over a year ago"


def format_timestamp_with_age(iso_ts: str) -> str:
    if not iso_ts:
        return "unknown"
    age = _age_days(iso_ts)
    date_part = iso_ts[:10]
    if age < 0:
        return "unknown"
    if age < 60:
        return f"{date_part} ({age} days ago)"
    if age < 365:
        months = round(age / 30)
        return f"{date_part} ({months} months ago)"
    years = round(age / 365.25, 1)
    return f"{date_part} ({years} years ago)"


def compute_years_in_crm(contact_created_at: str) -> str:
    if not contact_created_at:
        return "unknown"
    dt = _parse_iso(contact_created_at)
    if dt is None:
        return "unknown"
    days = (datetime.now(timezone.utc) - dt).days
    return str(round(days / 365.25, 1))


def compute_outreach_count(email_engagements: list, meeting_engagements: list) -> str:
    outbound = [e for e in email_engagements if e.get("hs_email_direction") != "INCOMING_EMAIL"]
    return str(len(outbound) + len(meeting_engagements))


def format_related_contacts(related: list) -> str:
    if not related:
        return "None"
    parts = []
    for c in related:
        name = f"{c.get('firstname', '')} {c.get('lastname', '')}".strip() or "Unknown"
        title = c.get("jobtitle") or "Unknown title"
        status = c.get("hs_lead_status") or ""
        entry = f"{name} ({title})"
        if status:
            entry += f" [{status}]"
        parts.append(entry)
    return "; ".join(parts)


def _classify_seniority(title: str) -> str:
    if not title:
        return "unknown"
    t = title.lower()
    if any(kw in t for kw in _CSUITE_KEYWORDS):
        return "C-suite"
    if any(kw in t for kw in _DIRECTOR_KEYWORDS):
        return "Director"
    return "Other"


def _is_excluded_title(title: str) -> bool:
    if not title:
        return True
    t = title.lower()
    return any(kw in t for kw in _EXCLUDED_TITLE_KEYWORDS)


def _parse_recency(contact: dict) -> int:
    ts = contact.get("updated_at", "")
    if not ts:
        return 0
    dt = _parse_iso(ts)
    return int(dt.timestamp()) if dt else 0


def select_secondary_contact(primary_jobtitle: str, related_contacts: list) -> str:
    primary_seniority = _classify_seniority(primary_jobtitle)
    candidates = []
    for c in related_contacts:
        if c.get("hs_lead_status") == "UNSUBSCRIBED":
            continue
        if _is_excluded_title(c.get("jobtitle", "")):
            continue
        seniority = _classify_seniority(c.get("jobtitle", ""))
        if primary_seniority == "C-suite" and seniority not in ("C-suite", "Director"):
            continue
        if primary_seniority == "Other" and seniority not in ("Other", "Director"):
            continue
        candidates.append(c)
    if not candidates:
        return "null"
    candidates.sort(key=lambda x: (
        0 if x.get("hs_lead_status") == "CONNECTED" else 1,
        -_parse_recency(x),
    ))
    first_name = candidates[0].get("firstname", "").strip()
    return first_name if first_name else "null"


def assemble_activity_history(email_engagements: list, meeting_engagements: list, chorus_data: dict) -> str:
    lines = []

    lines.append("=== EMAIL HISTORY ===")
    if email_engagements:
        sorted_emails = sorted(email_engagements, key=lambda e: e.get("hs_timestamp") or "")
        for email in sorted_emails:
            ts = (email.get("hs_timestamp") or "")
            date_part = ts[:10] if ts else "unknown date"
            freshness = _freshness_label(ts) if ts else "unknown date"
            direction = "OUTBOUND" if email.get("hs_email_direction") != "INCOMING_EMAIL" else "INBOUND"
            subject = email.get("hs_email_subject") or "(no subject)"
            body = (email.get("hs_email_text") or "")[:500]
            lines.append(f"[{date_part}] ({freshness}) {direction} EMAIL")
            lines.append(f"Subject: {subject}")
            if body:
                lines.append(f"Body: {body}")
            lines.append("")
    else:
        lines.append("No email history.")
        lines.append("")

    lines.append("=== MEETING HISTORY ===")
    if meeting_engagements:
        sorted_meetings = sorted(meeting_engagements, key=lambda m: m.get("hs_timestamp") or "")
        for meeting in sorted_meetings:
            ts = (meeting.get("hs_timestamp") or "")
            date_part = ts[:10] if ts else "unknown date"
            freshness = _freshness_label(ts) if ts else "unknown date"
            title = meeting.get("hs_meeting_title") or "Meeting"
            body = (meeting.get("hs_meeting_body") or "")[:500]
            internal = (meeting.get("hs_internal_meeting_notes") or "")[:200]
            lines.append(f"[{date_part}] ({freshness}) MEETING — {title}")
            if body:
                lines.append(f"Notes: {body}")
            if internal:
                lines.append(f"Internal notes: {internal}")
            lines.append("")
    else:
        lines.append("No meeting history.")
        lines.append("")

    lines.append("=== CHORUS TRANSCRIPTS ===")
    transcripts = chorus_data.get("transcripts")
    conversations = chorus_data.get("conversations")
    if transcripts:
        for t in transcripts:
            ts = (t.get("date") or "")
            date_part = ts[:10] if ts else "unknown date"
            freshness = _freshness_label(ts) if ts else "unknown date"
            title = t.get("title") or "Transcript"
            text = (t.get("transcript") or "")[:2000]
            lines.append(f"[{date_part}] ({freshness}) TRANSCRIPT — {title}")
            if text:
                lines.append(text)
            lines.append("")
    elif conversations:
        for t in conversations:
            if not t.get("available", False):
                continue
            ts = (t.get("date_time") or "")
            date_part = ts[:10] if ts else "unknown date"
            freshness = _freshness_label(ts) if ts else "unknown date"
            title = t.get("meeting_title") or "Transcript"
            text = (t.get("transcript") or "")[:2000]
            lines.append(f"[{date_part}] ({freshness}) TRANSCRIPT — {title}")
            if text:
                lines.append(text)
            lines.append("")
    elif chorus_data.get("transcript_available") is False:
        status = chorus_data.get("transcript_status") or "unavailable"
        lines.append(f"No Chorus transcripts available ({status}).")
        lines.append("Do not reference call content — base STATE classification on email and meeting history only.")
    else:
        lines.append("No Chorus transcripts.")

    return "\n".join(lines)


def format_deals(deals: list) -> str:
    if not deals:
        return "No deal history."
    parts = []
    for d in deals:
        name = d.get("dealname") or "Unnamed deal"
        stage = d.get("dealstage") or "unknown stage"
        amount = d.get("amount") or ""
        closedate = (d.get("closedate") or "")[:10]
        line = f"- {name} [{stage}]"
        if amount:
            line += f" (AUD {amount})"
        if closedate:
            line += f" closed {closedate}"
        parts.append(line)
    return "\n".join(parts)


def validate_required_tokens(tokens: dict) -> None:
    REQUIRED = [
        ("contact", "first_name"),
        ("contact", "last_name"),
        ("contact", "company"),
        ("contact", "jobtitle"),
        ("contact", "years_in_crm"),
        ("contact", "outreach_attempt_count"),
        ("crm", "full_activity_history"),
    ]
    missing = []
    for namespace, key in REQUIRED:
        val = tokens.get(namespace, {}).get(key)
        if val is None or (isinstance(val, str) and val.strip() == ""):
            missing.append(f"{namespace}.{key}")
    if missing:
        raise ValueError(f"Required tokens missing or empty: {', '.join(missing)}")


def main():
    contact_ids = json.loads(os.environ.get("CONTACT_IDS", "[]"))
    contact_emails_list = json.loads(os.environ.get("CONTACT_EMAILS", "[]"))
    email_map = dict(zip(contact_ids, contact_emails_list))

    hubspot_batch = read_json("hubspot_contacts.json")
    chorus_batch = read_json("chorus_transcripts.json")

    env = Environment(loader=FileSystemLoader(REPO_ROOT), undefined=StrictUndefined)
    template = env.get_template("prompt_reasoning.md")

    results = {"_contact_ids": contact_ids}
    failures = []

    for contact_id in contact_ids:
        contact_email = email_map.get(contact_id, "")
        try:
            contact = hubspot_batch.get(contact_id, {})
            chorus = chorus_batch.get(contact_id, SENTINEL_CHORUS)

            props = contact.get("properties", {})
            props_updated_at = contact.get("properties_updated_at", {})

            tokens = {
                "contact": {
                    "first_name": props.get("firstname") or "",
                    "last_name": props.get("lastname") or "",
                    "company": props.get("company") or "",
                    "industry": props.get("industry") or "",
                    "jobtitle": props.get("jobtitle") or "",
                    "website": props.get("website") or "",
                    "numberofemployees": props.get("numberofemployees") or "",
                    "company_locations": props.get("company_locations") or "",
                    "years_in_crm": compute_years_in_crm(contact.get("contact_created_at") or ""),
                    "outreach_attempt_count": compute_outreach_count(
                        contact.get("email_engagements", []),
                        contact.get("meeting_engagements", []),
                    ),
                    "related_contacts": format_related_contacts(contact.get("related_contacts_detail", [])),
                    "secondary_contact_name": select_secondary_contact(
                        primary_jobtitle=props.get("jobtitle") or "",
                        related_contacts=contact.get("related_contacts_detail", []),
                    ),
                    "name_of_target_role": props.get("name_of_target_role") or "Not specified",
                    "name_of_target_role_last_updated": format_timestamp_with_age(
                        props_updated_at.get("name_of_target_role") or ""
                    ),
                },
                "crm": {
                    "full_activity_history": assemble_activity_history(
                        email_engagements=contact.get("email_engagements", []),
                        meeting_engagements=contact.get("meeting_engagements", []),
                        chorus_data=chorus,
                    ),
                    "deals_history": format_deals(contact.get("deals", [])),
                },
                "industry": {
                    "market_intelligence": props.get("industry_market_intelligence") or "",
                },
                "company": {
                    "observable_signals": props.get("company_observable_signals") or "",
                },
            }

            validate_required_tokens(tokens)

            try:
                template.render(**tokens)
            except UndefinedError as e:
                print(f"[compute_campaign_tokens] VALIDATION FAILED contact_id={contact_id}: {e}", file=sys.stderr)
                raise

            results[contact_id] = tokens
            print(f"[compute_campaign_tokens] contact_id={contact_id} OK — secondary={tokens['contact']['secondary_contact_name']}", flush=True)

        except Exception as e:
            print(f"[compute_campaign_tokens] ERROR contact_id={contact_id}: {e}", file=sys.stderr, flush=True)
            append_dlq(contact_id, contact_email, "compute_campaign_tokens", str(e))
            failures.append(contact_id)

    write_json("campaign_tokens.json", results)
    print(f"[compute_campaign_tokens] campaign_tokens.json written: {len(contact_ids) - len(failures)}/{len(contact_ids)} succeeded", flush=True)

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
                append_dlq(cid, cem, "compute_campaign_tokens", str(e))
        except Exception:
            pass
        raise SystemExit(1)
