#!/usr/bin/env python3
"""generate_emails.py — Agent 2: generate 8 emails per contact using reasoning JSON.

Reads campaign_tokens.json + campaign_reasoning.json.
Calls Claude once per contact using prompt_emails.md + reasoning JSON.
Writes campaign_output.json keyed by contact_id.
"""
import json
import os
import re
import sys
from pathlib import Path

import anthropic
from jinja2 import Environment, FileSystemLoader, StrictUndefined

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from api_client import anthropic_retry
from dlq_writer import append_dlq
from file_io import read_json, write_json

CONTACT_IDS = os.environ.get("CONTACT_IDS", "[]")
CONTACT_EMAILS = os.environ.get("CONTACT_EMAILS", "[]")
REPO_ROOT = Path(__file__).parent.parent
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 16000
MAX_RETRIES = 2

EMAIL_KEYS = [f"email_{i}" for i in range(1, 9)]

SYSTEM_PROMPT = (
    "You are writing sales emails in strict Australian English for Staff Domain, "
    "an offshore staffing company. "
    "NEVER use em dashes (—), en dashes (–), or hyphens as sentence separators. "
    "Use commas, colons, or full stops instead. "
    "Never use 'offshoring' or 'outsourcing'. "
    "Return only the raw JSON object — no markdown, no code blocks."
)

SCHEMA_HINT = """
Return ONLY a raw JSON object with exactly 8 email keys:
{
  "email_1": {"subject": "...", "body": "..."},
  "email_2": {"subject": "...", "body": "..."},
  "email_3": {"subject": "...", "body": "..."},
  "email_4": {"subject": "...", "body": "..."},
  "email_5": {"subject": "...", "body": "..."},
  "email_6": {"subject": "...", "body": "..."},
  "email_7": {"subject": "...", "body": "..."},
  "email_8": {"subject": "...", "body": "..."}
}
"""


@anthropic_retry
def _call_claude(client, messages):
    return client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=messages,
    )


def strip_code_fence(text):
    text = text.strip()
    m = re.match(r'^```(?:json)?\s*([\s\S]*?)```\s*$', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r'(\{[\s\S]*\})', text)
    if m:
        return m.group(1).strip()
    return text


def clean_text(text):
    if not isinstance(text, str):
        return text
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"  +", " ", text)
    return text.strip()


def clean_emails(output):
    for key in EMAIL_KEYS:
        if key in output and isinstance(output[key], dict):
            if "subject" in output[key]:
                output[key]["subject"] = clean_text(output[key]["subject"])
            if "body" in output[key]:
                output[key]["body"] = clean_text(output[key]["body"])
    return output


def validate_emails(data):
    missing = [k for k in EMAIL_KEYS if k not in data]
    if missing:
        raise ValueError(f"Response missing email keys: {missing}")
    for key in EMAIL_KEYS:
        if not isinstance(data.get(key), dict):
            raise ValueError(f"{key} must be a dict")
        if "subject" not in data[key] or "body" not in data[key]:
            raise ValueError(f"{key} missing subject or body")


def run_emails_for_contact(client, env, contact_id, tokens, reasoning):
    template = env.get_template("prompt_emails.md")

    # Build the email render context: contact summary + serialised reasoning
    render_ctx = {
        "contact": tokens["contact"],
        "reasoning_json": json.dumps(reasoning, indent=2),
    }
    prompt = template.render(**render_ctx)
    print(f"[generate_emails] contact_id={contact_id} prompt={len(prompt):,} chars", flush=True)

    messages = [{"role": "user", "content": prompt + "\n\n" + SCHEMA_HINT}]
    last_error = None

    for attempt in range(1 + MAX_RETRIES):
        message = _call_claude(client, messages)
        raw = message.content[0].text
        print(f"[generate_emails] contact_id={contact_id} response={len(raw):,} chars stop_reason={message.stop_reason} attempt={attempt+1}", flush=True)

        if message.stop_reason == "max_tokens":
            raise RuntimeError(f"Claude truncated (max_tokens) for contact_id={contact_id}")

        cleaned = strip_code_fence(raw)
        try:
            data = json.loads(cleaned)
            validate_emails(data)
            return clean_emails(data)
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            print(f"[generate_emails] schema error contact_id={contact_id} attempt={attempt+1}: {e}", file=sys.stderr)
            if attempt < MAX_RETRIES:
                messages = [{"role": "user", "content": prompt + "\n\n" + SCHEMA_HINT + f"\n\nPREVIOUS ATTEMPT FAILED: {e}\nReturn valid JSON only."}]

    raise RuntimeError(f"Email generation failed after {1 + MAX_RETRIES} attempts for contact_id={contact_id}: {last_error}")


def main():
    contact_ids = json.loads(CONTACT_IDS)
    contact_emails_list = json.loads(CONTACT_EMAILS)
    email_map = dict(zip(contact_ids, contact_emails_list))

    tokens_batch = read_json("campaign_tokens.json")
    reasoning_batch = read_json("campaign_reasoning.json")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0)
    env = Environment(loader=FileSystemLoader(str(REPO_ROOT)), undefined=StrictUndefined)

    results = {"_contact_ids": contact_ids}
    failures = []

    for contact_id in contact_ids:
        contact_email = email_map.get(contact_id, "")
        tokens = tokens_batch.get(contact_id)
        reasoning = reasoning_batch.get(contact_id)

        if not tokens or not reasoning:
            print(f"[generate_emails] SKIP contact_id={contact_id} — missing tokens or reasoning (failed earlier)", flush=True)
            continue

        try:
            emails = run_emails_for_contact(client, env, contact_id, tokens, reasoning)
            results[contact_id] = emails
            print(f"[generate_emails] contact_id={contact_id} OK — generated {len(EMAIL_KEYS)} emails", flush=True)
        except Exception as e:
            print(f"[generate_emails] ERROR contact_id={contact_id}: {e}", file=sys.stderr, flush=True)
            append_dlq(contact_id, contact_email, "generate_emails", str(e))
            failures.append(contact_id)

    write_json("campaign_output.json", results)
    print(f"[generate_emails] campaign_output.json written: {len(contact_ids) - len(failures)}/{len(contact_ids)} succeeded", flush=True)

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
                append_dlq(cid, cem, "generate_emails", str(e))
        except Exception:
            pass
        raise SystemExit(1)
