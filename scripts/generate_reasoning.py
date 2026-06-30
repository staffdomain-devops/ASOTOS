#!/usr/bin/env python3
"""generate_reasoning.py — Agent 1: call Claude per contact to produce reasoning JSON.

Reads campaign_tokens.json. Calls Claude once per contact using prompt_reasoning.md.
Writes campaign_reasoning.json keyed by contact_id.
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
MAX_TOKENS = 4096
MAX_RETRIES = 2

REASONING_REQUIRED_KEYS = [
    "conversation_state", "conversation_state_evidence",
    "target_role_freshness", "target_role_handling", "deals_on_file",
    "secondary_contact_selected", "observable_signals_used",
    "company_summary", "industry_intelligence_used", "buyer_frame",
    "roles_identified", "resources_selected",
]

SYSTEM_PROMPT = (
    "You are a senior sales strategist analysing CRM data for Staff Domain, "
    "an Australian offshore staffing company. "
    "Return only a raw JSON object — no markdown, no code blocks, no explanation."
)

SCHEMA_HINT = """
Return ONLY a raw JSON object with these exact keys:
{
  "conversation_state": "STATE_A | STATE_B | STATE_C",
  "conversation_state_evidence": "...",
  "target_role_freshness": "...",
  "target_role_handling": "...",
  "deals_on_file": "...",
  "secondary_contact_selected": "name and reason, or null with reason",
  "email_1_opening": "exact opening sentence for email 1",
  "observable_signals_used": "...",
  "company_summary": "...",
  "industry_intelligence_used": "...",
  "buyer_frame": "...",
  "roles_identified": "3-5 industry-relevant roles",
  "resources_selected": {
    "email_1": "resource ID and reason",
    "email_2": "resource ID and reason",
    "email_3": "resource ID and reason",
    "email_4": "resource ID and reason",
    "email_5": "resource ID and reason",
    "email_6": "resource ID and reason",
    "email_7": "resource ID and reason",
    "email_8": "resource ID and reason"
  },
  "sequence_notes": "any special guidance for email generation"
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


def validate_reasoning(data):
    missing = [k for k in REASONING_REQUIRED_KEYS if k not in data]
    if missing:
        raise ValueError(f"Reasoning response missing keys: {missing}")
    if not isinstance(data.get("resources_selected"), dict):
        raise ValueError("resources_selected must be a dict")


def run_reasoning_for_contact(client, env, contact_id, tokens):
    template = env.get_template("prompt_reasoning.md")
    prompt = template.render(**tokens)
    print(f"[generate_reasoning] contact_id={contact_id} prompt={len(prompt):,} chars", flush=True)

    messages = [{"role": "user", "content": prompt + "\n\n" + SCHEMA_HINT}]
    last_error = None

    for attempt in range(1 + MAX_RETRIES):
        message = _call_claude(client, messages)
        raw = message.content[0].text
        print(f"[generate_reasoning] contact_id={contact_id} response={len(raw):,} chars stop_reason={message.stop_reason} attempt={attempt+1}", flush=True)

        if message.stop_reason == "max_tokens":
            raise RuntimeError(f"Claude truncated (max_tokens) for contact_id={contact_id}")

        cleaned = strip_code_fence(raw)
        try:
            data = json.loads(cleaned)
            validate_reasoning(data)
            return data
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            print(f"[generate_reasoning] schema error contact_id={contact_id} attempt={attempt+1}: {e}", file=sys.stderr)
            if attempt < MAX_RETRIES:
                messages = [{"role": "user", "content": prompt + "\n\n" + SCHEMA_HINT + f"\n\nPREVIOUS ATTEMPT FAILED: {e}\nReturn valid JSON only."}]

    raise RuntimeError(f"Reasoning failed after {1 + MAX_RETRIES} attempts for contact_id={contact_id}: {last_error}")


def main():
    contact_ids = json.loads(CONTACT_IDS)
    contact_emails_list = json.loads(CONTACT_EMAILS)
    email_map = dict(zip(contact_ids, contact_emails_list))

    tokens_batch = read_json("campaign_tokens.json")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0)
    env = Environment(loader=FileSystemLoader(str(REPO_ROOT)), undefined=StrictUndefined)

    results = {"_contact_ids": contact_ids}
    failures = []

    for contact_id in contact_ids:
        contact_email = email_map.get(contact_id, "")
        tokens = tokens_batch.get(contact_id)
        if not tokens:
            print(f"[generate_reasoning] SKIP contact_id={contact_id} — no tokens (failed earlier)", flush=True)
            continue
        try:
            reasoning = run_reasoning_for_contact(client, env, contact_id, tokens)
            results[contact_id] = reasoning
            print(f"[generate_reasoning] contact_id={contact_id} OK — state={reasoning.get('conversation_state')}", flush=True)
        except Exception as e:
            print(f"[generate_reasoning] ERROR contact_id={contact_id}: {e}", file=sys.stderr, flush=True)
            append_dlq(contact_id, contact_email, "generate_reasoning", str(e))
            failures.append(contact_id)

    write_json("campaign_reasoning.json", results)
    print(f"[generate_reasoning] campaign_reasoning.json written: {len(contact_ids) - len(failures)}/{len(contact_ids)} succeeded", flush=True)

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
                append_dlq(cid, cem, "generate_reasoning", str(e))
        except Exception:
            pass
        raise SystemExit(1)
