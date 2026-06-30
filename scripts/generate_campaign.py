#!/usr/bin/env python3
"""generate_campaign.py — Phase 4: assemble prompt, call Claude, validate output, write campaign_output.json."""
import json
import os
import re
import sys
from pathlib import Path

import anthropic
from jinja2 import Environment, FileSystemLoader, StrictUndefined

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from api_client import anthropic_retry
from dlq_writer import write_dlq
from file_io import read_json, write_json

CONTACT_ID = os.environ.get("CONTACT_ID", "")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")
REPO_ROOT = Path(__file__).parent.parent
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 16000
EMAIL_BODY_CAP = 3000
MAX_RETRIES = 2

JSON_SCHEMA_HINT = """
Return ONLY a raw JSON object — no markdown, no code fences, no explanation.
The object must have a "reasoning" key and exactly 8 email keys (email_1 through email_8),
each with "subject" (string) and "body" (string).
"""

REQUIRED_KEYS = ["reasoning", "email_1", "email_2", "email_3", "email_4",
                 "email_5", "email_6", "email_7", "email_8"]


@anthropic_retry
def _call_claude(client, system, messages):
    return client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=messages,
    )


def strip_code_fence(text):
    text = text.strip()
    match = re.match(r'^```(?:json)?\s*([\s\S]*?)```\s*$', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r'(\{[\s\S]*\})', text)
    if match:
        return match.group(1).strip()
    return text


def clean_text(text):
    if not isinstance(text, str):
        return text
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"  +", " ", text)
    return text.strip()


def clean_emails(output):
    for i in range(1, 9):
        key = f"email_{i}"
        if key in output and isinstance(output[key], dict):
            email = output[key]
            if "subject" in email:
                email["subject"] = clean_text(email["subject"])
            if "body" in email:
                email["body"] = clean_text(email["body"])
    return output


def validate_schema(output):
    missing = [k for k in REQUIRED_KEYS if k not in output]
    if missing:
        raise ValueError(f"Response missing required keys: {missing}")
    for i in range(1, 9):
        key = f"email_{i}"
        if not isinstance(output.get(key), dict):
            raise ValueError(f"{key} must be a dict with subject and body")
        if "subject" not in output[key] or "body" not in output[key]:
            raise ValueError(f"{key} missing subject or body field")


def main():
    runner_temp = os.environ["RUNNER_TEMP"]

    write_json("campaign_output.json", {"stub": True})

    tokens = read_json("campaign_tokens.json")

    env = Environment(
        loader=FileSystemLoader(str(REPO_ROOT)),
        undefined=StrictUndefined,
    )
    template = env.get_template("prompt_template.md")
    prompt = template.render(**tokens)

    print(f"[generate_campaign] Prompt assembled: {len(prompt):,} chars")
    print(f"[generate_campaign] Contact: {tokens.get('contact', {}).get('first_name')} {tokens.get('contact', {}).get('last_name')} @ {tokens.get('contact', {}).get('company')}")
    print(f"[generate_campaign] Calling {MODEL} (max_tokens={MAX_TOKENS})...")

    system = (
        "You are writing sales emails in strict Australian English. "
        "NEVER use em dashes (—), en dashes (–), or hyphens as sentence separators. "
        "Use commas, colons, or full stops instead. Compound adjectives (no-lock-in, all-in) are fine. "
        "Never use the words 'offshoring' or 'outsourcing'. "
        "Return only the raw JSON object — no markdown, no code blocks."
    )

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0)

    messages = [{"role": "user", "content": prompt + "\n\n" + JSON_SCHEMA_HINT}]
    last_error = None

    for attempt in range(1 + MAX_RETRIES):
        try:
            message = _call_claude(client, system, messages)
            raw = message.content[0].text
            print(f"[generate_campaign] Response: {len(raw):,} chars | stop_reason={message.stop_reason} | attempt={attempt + 1}")

            raw_path = os.path.join(runner_temp, "campaign_output_raw.txt")
            with open(raw_path, "w", encoding="utf-8") as f:
                f.write(raw)

            if message.stop_reason == "max_tokens":
                raise RuntimeError("Claude response truncated (max_tokens) — increase MAX_TOKENS or shorten prompt")

            cleaned = strip_code_fence(raw)
            output = json.loads(cleaned)
            validate_schema(output)
            output = clean_emails(output)

            write_json("campaign_output.json", output)
            print(f"[generate_campaign] campaign_output.json written with reasoning + 8 emails")
            return

        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            print(f"[generate_campaign] Schema validation failed (attempt {attempt + 1}): {e}", file=sys.stderr)
            if attempt < MAX_RETRIES:
                error_injection = f"\n\nPREVIOUS ATTEMPT FAILED: {e}\nFix the issue and return valid JSON conforming to the required schema."
                messages = [{"role": "user", "content": prompt + "\n\n" + JSON_SCHEMA_HINT + error_injection}]

    write_dlq(CONTACT_ID, CONTACT_EMAIL, "generate_campaign", str(last_error))
    raise RuntimeError(f"Campaign generation failed after {1 + MAX_RETRIES} attempts: {last_error}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        write_dlq(CONTACT_ID, CONTACT_EMAIL, "generate_campaign", str(exc))
        raise SystemExit(1)
