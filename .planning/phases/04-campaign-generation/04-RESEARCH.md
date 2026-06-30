# Phase 4: Campaign Generation — Research

**Researched:** 2026-06-12
**Domain:** Anthropic Python SDK, Pydantic v2 validation, retry-on-validation-failure loop, em-dash post-processing
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| GEN-01 | Call Claude API (`claude-sonnet-4-6`) with assembled prompt; `max_tokens=16000`; system prompt enforces Australian English, no em/en dashes as separators, raw JSON output only | messages.create signature verified via Context7; system as list of TextBlockParam with cache_control confirmed |
| GEN-02 | Validate JSON response against schema: `reasoning` block + `email_1`–`email_8` each with `subject` and `body` fields | Pydantic v2 nested BaseModel + model_validate pattern confirmed via Context7 |
| GEN-03 | Check `stop_reason` — raise error if `max_tokens` (truncated response returns HTTP 200 but is invalid) | stop_reason field and all valid values confirmed via Context7 SDK source |
| GEN-04 | On validation failure, retry with error message injected (up to 2 retries); DLQ on exhaustion | Manual retry loop pattern with error injection documented; distinct from tenacity (which handles API-level failures) |
| GEN-05 | Post-process output: strip em dashes and en dashes from all email bodies and subjects | Python str.replace() approach confirmed; Unicode codepoints documented |
| GEN-06 | Write `campaign_output.json` to `$RUNNER_TEMP` | file_io.write_json() from lib — already built in Phase 1 |
| REL-02 | Anthropic SDK initialised with `max_retries=0` to prevent double-retry with tenacity | DEFAULT_MAX_RETRIES=2 confirmed via Context7 SDK source |
</phase_requirements>

---

## Summary

Phase 4 is the cognitive core of the pipeline. `generate_campaign.py` reads `campaign_tokens.json` from `$RUNNER_TEMP` (written by Phase 3), assembles and renders the prompt, calls `claude-sonnet-4-6`, validates the response against a Pydantic schema, post-processes the output, and writes `campaign_output.json`. There are no new external API dependencies to add — the Anthropic SDK, Pydantic v2, and the shared lib modules are all installed in Phase 1.

The script has two distinct retry layers that must not be confused. The **tenacity `anthropic_retry` decorator** (from `lib/api_client.py`) handles API-level transient failures: 429, 5xx, connection errors. The **manual validation retry loop** handles semantic failures: JSON parse errors and Pydantic schema violations. These are orthogonal: a 200-OK response with invalid JSON triggers the manual loop; a 429 triggers tenacity. The manual loop injects the specific error message as a follow-up user turn so Claude can correct its output.

The Phase 3 → Phase 4 interface is `$RUNNER_TEMP/campaign_tokens.json`. Phase 4 does NOT re-render the Jinja2 template — that is Phase 3's responsibility. Phase 3 writes the rendered prompt string into `campaign_tokens.json` as a top-level key (`"rendered_prompt"`). Phase 4 reads this pre-rendered string and passes it directly to the Claude API as the user message. This keeps the prompt assembly logic in Phase 3 and the API call logic in Phase 4.

**Primary recommendation:** Implement generate_campaign.py as a manual validation retry loop (3 total attempts) that wraps a `@anthropic_retry`-decorated inner function; handle stop_reason before JSON parsing; validate with Pydantic `model_validate_json()`; post-process with str.replace(); write output via `file_io.write_json()`.

---

## Project Constraints (from CLAUDE.md)

- `Anthropic(api_key=..., max_retries=0)` — mandatory; SDK DEFAULT_MAX_RETRIES=2 causes up to 12 API calls without this
- `max_tokens=16000` for the 8-email generation call
- Check `stop_reason` on every response — `max_tokens` returns HTTP 200 but is truncated/invalid
- Retry ONLY on: 429, 5xx, connection errors; fail immediately on 400, 401, 403, 422, other 4xx
- Tenacity config: `stop_after_attempt(6)`, `stop_after_delay(60)`, `wait_random_exponential(min=1, max=60)`
- DLQ on exhaustion: `write_dlq()` then `raise SystemExit(1)`
- System prompt must enforce Australian English, no em/en dashes as separators, raw JSON output only (from GEN-01)
- Post-process: strip em dashes (U+2014) and en dashes (U+2013) from all subject and body strings (GEN-05)

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Prompt rendering (Jinja2 template) | `compute_campaign_tokens.py` (Phase 3) | — | Phase 3 owns all token computation and template rendering; Phase 4 only consumes the pre-rendered string |
| Claude API call | `generate_campaign.py` | `lib/api_client.py` (anthropic_retry decorator) | Single API call per contact; retry decoration handles transient failures |
| JSON response extraction | `generate_campaign.py` | — | `response.content[0].text` is the raw text; JSON parsing is next step |
| Schema validation | `generate_campaign.py` | Pydantic v2 | CampaignOutput model with 8 nested EmailOutput models |
| Validation retry with error injection | `generate_campaign.py` | — | Manual loop (not tenacity) — error semantics require injecting previous error into messages list |
| Em/en-dash post-processing | `generate_campaign.py` | — | Simple str.replace() pass over all subject/body strings after validation |
| Output persistence | `generate_campaign.py` → `lib/file_io.py` | — | `file_io.write_json("campaign_output.json", ...)` |
| DLQ on exhaustion | `generate_campaign.py` → `lib/dlq_writer.py` | — | After 3 total attempts fail, write_dlq() then SystemExit(1) |

---

## Standard Stack

### Core (all already in requirements.txt from Phase 1)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| anthropic | ==0.109.1 | Claude API client | [VERIFIED: STACK.md, PyPI 2026-06-12] — `messages.create()`, `stop_reason`, error types |
| pydantic | ==2.13.4 | Response schema validation | [VERIFIED: Phase 1 RESEARCH.md, PyPI 2026-06-12] — `BaseModel`, `model_validate_json()`, `ValidationError` |
| tenacity | ==9.1.4 | API-level retry (via lib/api_client.py `anthropic_retry`) | [VERIFIED: STACK.md] — already wired; Phase 4 just uses the decorator |

### Supporting (already installed)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| lib/api_client.py | — | `anthropic_retry` tenacity decorator | Wraps the inner Claude API call function |
| lib/dlq_writer.py | — | DLQ record on exhaustion | Called in the outer except block after 3 attempts fail |
| lib/file_io.py | — | `read_json` / `write_json` | Reading campaign_tokens.json; writing campaign_output.json |

No new packages required for Phase 4.

---

## Architecture Patterns

### System Architecture Diagram

```
$RUNNER_TEMP/campaign_tokens.json
  (written by Phase 3 — contains "rendered_prompt" key)
        |
        v
generate_campaign.py
        |
        +-- [read] file_io.read_json("campaign_tokens.json")
        |       extract tokens["rendered_prompt"]  (pre-rendered user message)
        |
        +-- [build] system_prompt string
        |       (Australian English, no dashes, raw JSON only rules)
        |
        +-- [init] Anthropic(api_key=..., max_retries=0)
        |
        +-- VALIDATION RETRY LOOP (up to 3 total attempts)
        |       |
        |       +-- messages list starts as [{"role": "user", "content": rendered_prompt}]
        |       |   on retry: append {"role": "assistant", "content": prev_raw_text}
        |       |             append {"role": "user", "content": error_correction_msg}
        |       |
        |       +-- @anthropic_retry  <--- tenacity handles 429/5xx/connection errors
        |       |   client.messages.create(
        |       |       model="claude-sonnet-4-6",
        |       |       max_tokens=16000,
        |       |       system=[{...cached system prompt...}],
        |       |       messages=messages_list
        |       |   )
        |       |
        |       +-- CHECK: response.stop_reason == "max_tokens"?
        |       |       YES --> raise StopReasonError (triggers retry with error injection)
        |       |       NO  --> continue
        |       |
        |       +-- EXTRACT: raw_text = response.content[0].text
        |       |
        |       +-- PARSE: json.loads(raw_text)
        |       |       JSONDecodeError --> inject error, retry (up to 2 more times)
        |       |
        |       +-- VALIDATE: CampaignOutput.model_validate_json(raw_text)
        |               ValidationError --> inject error, retry (up to 2 more times)
        |               OK --> break loop
        |
        +-- POST-PROCESS: strip em/en dashes from all subject + body strings
        |
        +-- WRITE: file_io.write_json("campaign_output.json", validated_output.model_dump())
        |
        v
$RUNNER_TEMP/campaign_output.json
  {
    "reasoning": "...",
    "email_1": {"subject": "...", "body": "..."},
    ...
    "email_8": {"subject": "...", "body": "..."}
  }

ON EXHAUSTION (3 attempts, all failed):
  write_dlq(contact_id, contact_email, "generate_campaign", last_error_message)
  raise SystemExit(1)
```

### Recommended Project Structure
```
scripts/
├── lib/
│   ├── __init__.py
│   ├── api_client.py         # anthropic_retry already defined here
│   ├── dlq_writer.py
│   └── file_io.py
└── generate_campaign.py      # Phase 4: the only new file
```

### Pattern 1: Anthropic Client Initialization (REL-02)
**What:** Initialize with `max_retries=0` to prevent SDK's internal retry doubling tenacity
**When to use:** Only once, at module level (or inside main())

```python
# Source: CLAUDE.md (canonical) + Context7 /anthropics/anthropic-sdk-python
import os
from anthropic import Anthropic

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0)
```

**Critical:** The SDK's `DEFAULT_MAX_RETRIES = 2`. Without `max_retries=0`, a tenacity attempt that hits a 429 triggers the SDK to retry internally 2× before tenacity sees the exception — up to 6 × 2 = 12 actual API calls. [VERIFIED: Context7, `/anthropics/anthropic-sdk-python`, SDK source `_base_client.py`]

### Pattern 2: messages.create Call with System Prompt (GEN-01)
**What:** Non-streaming call with system as a list of TextBlockParam (enables cache_control)
**When to use:** The core Claude API call inside the `@anthropic_retry`-decorated function

```python
# Source: Context7 /anthropics/anthropic-sdk-python — test_messages.py, api.md
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=16000,
    system=[
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},  # v2 deferred (OPT-01) but harmless now
        }
    ],
    messages=messages_list,  # list of {"role": ..., "content": ...} dicts
)
```

**Note on system prompt string format:** The system parameter accepts either a plain string or a list of TextBlockParam dicts. The list form is required for `cache_control`. Use the list form from day one — it is backward compatible and enables OPT-01 (prompt caching) in v2 without a code change. [VERIFIED: Context7, SDK `MessageCreateParamsBase`]

### Pattern 3: stop_reason Check (GEN-03)
**What:** Check before any parsing — HTTP 200 with stop_reason="max_tokens" is an invalid response
**When to use:** Immediately after messages.create() returns, before accessing .content

```python
# Source: Context7 /anthropics/anthropic-sdk-python — message.py
if response.stop_reason == "max_tokens":
    raise ValueError(
        f"Claude response truncated (stop_reason=max_tokens). "
        f"Output token count: {response.usage.output_tokens}. "
        f"Increase max_tokens or reduce prompt size."
    )
# Only safe to proceed after this check passes
```

Valid `stop_reason` values: `"end_turn"` (normal), `"max_tokens"` (truncated), `"stop_sequence"`, `"tool_use"`, `"pause_turn"`, `"refusal"`. Only `"end_turn"` is acceptable for this pipeline. [VERIFIED: Context7, SDK `message.py` stop_reason docstring]

### Pattern 4: Text Extraction from Response Content
**What:** Extract the raw text from the first content block
**When to use:** After stop_reason check passes

```python
# Source: Context7 /anthropics/anthropic-sdk-python
raw_text = response.content[0].text
```

For a standard non-tool-use response, `response.content` is a list with one `TextBlock`. `content[0].text` is the raw string. [VERIFIED: Context7, SDK README example]

### Pattern 5: Pydantic v2 CampaignOutput Model (GEN-02)
**What:** Nested Pydantic model that validates the full Claude JSON response
**When to use:** Define at module level; call `CampaignOutput.model_validate_json(raw_text)` after extracting text

```python
# Source: Context7 /pydantic/pydantic — models.md
from pydantic import BaseModel, ValidationError


class EmailOutput(BaseModel):
    subject: str
    body: str


class CampaignOutput(BaseModel):
    reasoning: str
    email_1: EmailOutput
    email_2: EmailOutput
    email_3: EmailOutput
    email_4: EmailOutput
    email_5: EmailOutput
    email_6: EmailOutput
    email_7: EmailOutput
    email_8: EmailOutput
```

**Calling model_validate_json:**
```python
# Source: Context7 /pydantic/pydantic — models.md
try:
    output = CampaignOutput.model_validate_json(raw_text)
except ValidationError as e:
    # e.errors() returns a list of dicts with loc, msg, type
    error_summary = str(e)  # human-readable multi-line string
    # inject error_summary into next attempt's user message
```

**Why `model_validate_json()` not `model_validate(json.loads(...))`:**
`model_validate_json()` is the preferred path for JSON strings — it handles JSON parsing internally and produces cleaner error messages that point to the JSON path. If the string is not valid JSON at all, it raises `ValidationError` (not `json.JSONDecodeError`), simplifying the error handling path to a single exception type. [VERIFIED: Context7, pydantic docs `json.md`]

**Alternative:** Can also call `json.loads(raw_text)` first (catching `json.JSONDecodeError`), then `CampaignOutput.model_validate(parsed_dict)` (catching `ValidationError`). This separates parse vs. schema errors for logging. Either approach is correct; the single-exception path with `model_validate_json()` is simpler.

### Pattern 6: Manual Validation Retry Loop with Error Injection (GEN-04)
**What:** 3-attempt outer loop that re-calls Claude with error context when validation fails
**When to use:** Wraps the entire Claude call + validation pipeline; separate from tenacity

```python
# Source: [ASSUMED] — standard LLM correction pattern; REQUIREMENTS.md GEN-04 spec
MAX_VALIDATION_ATTEMPTS = 3
messages = [{"role": "user", "content": rendered_prompt}]
last_error = None

for attempt in range(MAX_VALIDATION_ATTEMPTS):
    # The inner call is decorated with @anthropic_retry for API-level failures
    response = _call_claude(client, system_prompt, messages)

    if response.stop_reason == "max_tokens":
        last_error = f"Response truncated (stop_reason=max_tokens, tokens={response.usage.output_tokens})"
        raw_text = response.content[0].text
        messages.append({"role": "assistant", "content": raw_text})
        messages.append({
            "role": "user",
            "content": (
                f"Your previous response was truncated before completion. "
                f"Error: {last_error}. "
                f"Please provide the complete JSON response within the token limit."
            )
        })
        continue

    raw_text = response.content[0].text
    try:
        output = CampaignOutput.model_validate_json(raw_text)
        # Success — break out of retry loop
        break
    except (ValidationError, ValueError) as e:
        last_error = str(e)
        messages.append({"role": "assistant", "content": raw_text})
        messages.append({
            "role": "user",
            "content": (
                f"Your response did not match the required JSON schema. "
                f"Validation error: {last_error}. "
                f"Please return a valid JSON object matching the schema exactly."
            )
        })
else:
    # All 3 attempts exhausted
    write_dlq(contact_id, contact_email, "generate_campaign", last_error)
    raise SystemExit(1)
```

**Key design points:**
- The `for...else` construct: the `else` block runs only when the loop completes without `break` (i.e., all attempts failed)
- Previous assistant response is appended as `{"role": "assistant", "content": raw_text}` so Claude sees its own output
- The correction message clearly states what went wrong and what is expected
- `last_error` is kept for DLQ — always the error from the most recent attempt
- tenacity (`anthropic_retry`) handles 429/5xx transparently inside `_call_claude()` — the outer loop only sees validation failures

### Pattern 7: Em-Dash and En-Dash Post-Processing (GEN-05)
**What:** Strip em dashes (U+2014) and en dashes (U+2013) from all subject and body strings
**When to use:** After successful validation, before writing output

```python
# Source: [VERIFIED: Python docs — str.replace()]
EM_DASH = "—"   # —
EN_DASH = "–"   # –

def strip_dashes(text: str) -> str:
    """Replace em dashes and en dashes with a space (preserves readability)."""
    return text.replace(EM_DASH, " ").replace(EN_DASH, " ")

def post_process_output(output: CampaignOutput) -> dict:
    """Apply post-processing to all email subject and body strings."""
    result = {"reasoning": output.reasoning}
    for i in range(1, 9):
        key = f"email_{i}"
        email = getattr(output, key)
        result[key] = {
            "subject": strip_dashes(email.subject),
            "body": strip_dashes(email.body),
        }
    return result
```

**Replacement strategy:** Replace with a single space rather than empty string. Em/en dashes are used as separators in English; removing them without a separator produces run-together text. A space is the safest substitution. [ASSUMED — reasonable from prompt rules "no em dashes"; space is conventional alternative]

**Why not regex:** `str.replace()` is O(n) and sufficient for two specific Unicode codepoints. No regex overhead needed. [VERIFIED: standard Python str API]

### Pattern 8: Phase 3 → Phase 4 Interface Contract
**What:** The exact key generate_campaign.py reads from campaign_tokens.json
**Critical:** Phase 4 does NOT re-render the Jinja2 template

The rendered prompt string is stored in `campaign_tokens.json` under the key `"rendered_prompt"`:
```python
# In generate_campaign.py — reading Phase 3 output
tokens = file_io.read_json("campaign_tokens.json")
rendered_prompt = tokens["rendered_prompt"]  # pre-rendered, ready to pass to Claude
```

**If Phase 3 does not write `"rendered_prompt"` to campaign_tokens.json:** Phase 4 will fail with `KeyError`. The planner must ensure the Phase 3 plan explicitly includes writing the rendered prompt string into `campaign_tokens.json`. This is the primary interface contract between the two phases.

**Alternative if Phase 3 writes the rendered prompt as a separate file:** Phase 4 can `read_json("rendered_prompt.txt")` — but keeping it inside `campaign_tokens.json` is simpler and reduces the number of files in `$RUNNER_TEMP`. [ASSUMED — must be confirmed or specified in Phase 3 plan]

### Pattern 9: System Prompt Content (GEN-01)
**What:** The system prompt instructing Claude to return raw JSON, use Australian English, avoid dashes
**When to use:** Passed as the `system` parameter in every messages.create call

The system prompt does NOT contain the ASOTOS campaign content — that is the user message. The system prompt contains only meta-instructions:

```python
SYSTEM_PROMPT = """You are generating personalised re-engagement emails for a B2B staffing company.

Return ONLY a raw JSON object. Your response must:
- Begin with { and end with }
- Contain no markdown, no code blocks, no backticks, no explanation text
- Match the schema exactly: {"reasoning": string, "email_1": {"subject": string, "body": string}, ..., "email_8": {"subject": string, "body": string}}

Writing rules (apply to all emails):
- Write in Australian English (e.g. "recognise", "colour", "programme")
- Never use em dashes (—) or en dashes (–) as separators — use commas or short sentences instead
- No salutation (the sending system inserts it automatically)
- No closing/sign-off (the sending system inserts the signature automatically)"""
```

[ASSUMED — system prompt content inferred from GEN-01 requirements, ASOTOS prompt rules, and prompt_template.md. The exact wording is at implementer's discretion as long as it satisfies GEN-01 constraints.]

### Pattern 10: Full Script Structure
**What:** The overall structure of generate_campaign.py
**When to use:** Reference for the planner to know what the single plan needs to produce

```python
#!/usr/bin/env python3
"""generate_campaign.py — Phase 4: Call Claude, validate, post-process, write output."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

from anthropic import Anthropic
from pydantic import BaseModel, ValidationError
from api_client import anthropic_retry
from dlq_writer import write_dlq
from file_io import read_json, write_json

CONTACT_ID = os.environ.get("CONTACT_ID", "")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")

# --- Pydantic models ---
class EmailOutput(BaseModel): ...
class CampaignOutput(BaseModel): ...

# --- Constants ---
SYSTEM_PROMPT = "..."
MAX_VALIDATION_ATTEMPTS = 3

# --- Client init (REL-02) ---
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0)

@anthropic_retry
def _call_claude(messages_list: list) -> object:
    return client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=messages_list,
    )

def post_process(output: CampaignOutput) -> dict: ...

def main():
    tokens = read_json("campaign_tokens.json")
    rendered_prompt = tokens["rendered_prompt"]
    # ... validation retry loop ...
    # ... post-process ...
    write_json("campaign_output.json", result)

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        write_dlq(CONTACT_ID, CONTACT_EMAIL, "generate_campaign", str(e))
        raise SystemExit(1)
```

### Anti-Patterns to Avoid

- **Parsing JSON before checking stop_reason:** If stop_reason is "max_tokens", the JSON may be syntactically complete (unlucky truncation point) but semantically wrong — all 8 emails may not be present. Always check stop_reason first.
- **Using tenacity for validation failures:** Tenacity decorators must NOT retry on ValidationError or JSONDecodeError — these are semantic failures that require error injection, not blind retry. The `_is_retryable_anthropic` predicate in api_client.py does not match these exception types, so this is safe by design. But do not add them to the predicate.
- **Appending raw assistant content with markdown:** If Claude wraps its response in a markdown code block (despite system prompt instructions), the raw_text appended to messages will include the backticks. The next attempt should strip markdown fences before appending. Add a `strip_json_fences(text)` helper that strips ```json ... ``` or ``` ... ``` wrappers before parsing.
- **Initializing Anthropic() inside the retry loop:** Client initialization is expensive. Initialize once at module level.
- **Swallowing SystemExit in the outer except:** The outer `try/except Exception` in `main()` must re-raise `SystemExit` specifically — `SystemExit` does not inherit from `Exception` in Python, but be explicit to be safe.
- **Replacing em dashes with empty string:** Produces "emailsubject" run-together text. Replace with a space.
- **model_validate() on a string (not a dict):** `model_validate()` expects a dict or model instance; `model_validate_json()` expects a JSON string. Using the wrong method raises a cryptic `ValidationError`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JSON schema validation for Claude output | Manual key checking with `if "email_1" in response` | Pydantic v2 `model_validate_json()` | Nested validation with clear error paths; captures loc/msg/type for injection into next attempt |
| API-level retry with jitter | Custom sleep loop | `@anthropic_retry` from lib/api_client.py | Already written; handles RateLimitError + APIStatusError ≥500; reraise=True; uses CLAUDE.md spec |
| Stop reason detection | Trusting that HTTP 200 = valid output | Explicit `response.stop_reason == "max_tokens"` check | PITFALL-M3: truncated JSON can parse successfully and still be wrong |
| Em-dash replacement | Regex | `str.replace("—", " ").replace("–", " ")` | Two known Unicode codepoints — regex adds complexity with zero benefit |

---

## Common Pitfalls

### Pitfall 1: stop_reason "max_tokens" with Parseable JSON
**What goes wrong:** Claude hits max_tokens mid-generation. The truncation point happens to fall at a syntactically complete JSON boundary (e.g., after email_6 which is the last one generated). `json.loads()` succeeds, Pydantic validation fails because email_7 and email_8 are missing. The error message says "email_7 field required" which is confusing — it looks like a schema mismatch, not a truncation.
**Why it happens:** JSON is incrementally parseable; a truncated JSON document that ends after a complete top-level key is valid.
**How to avoid:** Always check `stop_reason` BEFORE calling `json.loads()`. A "max_tokens" stop_reason means the response is incomplete regardless of whether the JSON parses.
**Warning signs:** ValidationError "field required" for email_7 or email_8 in logs. Output token count in usage near the 16000 cap.

### Pitfall 2: Claude Returns Markdown-Fenced JSON
**What goes wrong:** Despite system prompt instructions, Claude occasionally wraps its JSON in ```json ... ``` or ``` ... ``` fences. `json.loads()` raises JSONDecodeError on the backtick prefix. The error is injected and the next attempt may also have the markdown issue.
**Why it happens:** Claude's training has a strong prior toward markdown-formatted output; overriding it completely via system prompt is reliable but not 100%.
**How to avoid:** Strip markdown code fences before parsing. A simple helper:
```python
import re
def strip_json_fences(text: str) -> str:
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ``` wrappers
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return text.strip()
```
Apply this BEFORE `model_validate_json()`. [ASSUMED — common LLM API pitfall; no official citation needed]
**Warning signs:** JSONDecodeError with "Expecting value: line 1 column 1" in the first character.

### Pitfall 3: Tenacity Consuming Validation-Failure Attempts
**What goes wrong:** The `@anthropic_retry` decorator is applied to a function that also does JSON parsing and Pydantic validation inside it. ValidationError is raised inside the decorated function, but because `_is_retryable_anthropic` returns False for ValidationError, tenacity correctly does NOT retry — it reraises immediately. However, if the developer accidentally puts the validation inside the `@anthropic_retry` function and then also has an outer loop, the retry counts get confused.
**Why it happens:** Conflating the two retry layers.
**How to avoid:** Keep the `@anthropic_retry`-decorated function (`_call_claude`) doing ONLY the `client.messages.create()` call. Do all response processing (stop_reason check, JSON parsing, Pydantic validation) in the outer loop, NOT inside the decorated function.
**Warning signs:** More than 6 API calls logged for a single validation failure.

### Pitfall 4: DLQ Not Written on SystemExit
**What goes wrong:** The outer `try/except` catches `Exception` and calls `write_dlq()`. But `SystemExit` does NOT inherit from `Exception` — it inherits from `BaseException`. If the inner loop raises `SystemExit(1)` directly, the outer `try/except Exception` does NOT catch it, so `write_dlq()` is not called.
**Why it happens:** Python exception hierarchy: `BaseException > SystemExit`, NOT `BaseException > Exception > SystemExit`.
**How to avoid:** The validation retry loop should raise a regular exception (e.g., `RuntimeError`) after writing the DLQ, NOT raise `SystemExit` directly. The outer `try/except Exception` then calls `write_dlq()` again (harmless — it overwrites) and raises `SystemExit(1)`. OR: write the DLQ inside the `for...else` block before raising `SystemExit(1)`, and re-raise `SystemExit` in the outer `except` handler without calling `write_dlq()` again.

Correct pattern:
```python
# In the for...else block:
else:
    write_dlq(CONTACT_ID, CONTACT_EMAIL, "generate_campaign", last_error)
    sys.exit(1)  # raises SystemExit(1), not caught by outer `except Exception`

# In the outer wrapper:
try:
    main()
except SystemExit:
    raise  # re-raise without DLQ (already written above)
except Exception as e:
    write_dlq(CONTACT_ID, CONTACT_EMAIL, "generate_campaign", str(e))
    sys.exit(1)
```

### Pitfall 5: Phase 3/4 Interface Ambiguity — Where is the Rendered Prompt?
**What goes wrong:** Phase 4 tries to read `tokens["rendered_prompt"]` but Phase 3 wrote the rendered prompt as a separate `rendered_prompt.txt` file, or embedded it under a different key, or did not write it at all (Phase 3 left rendering to Phase 4).
**Why it happens:** The Phase 3 plan and Phase 4 plan are written independently; the key name must be explicitly coordinated.
**How to avoid:** The Phase 3 plan MUST specify that `campaign_tokens.json` contains a `"rendered_prompt"` string key at the top level with the fully rendered Jinja2 output. If Phase 3 is not yet planned when Phase 4 is planned, leave a clear interface note in the Phase 4 plan.
**Warning signs:** `KeyError: "rendered_prompt"` at runtime.

---

## Code Examples

### Complete CampaignOutput Pydantic model
```python
# Source: Context7 /pydantic/pydantic — models.md (nested model pattern)
from pydantic import BaseModel, ValidationError

class EmailOutput(BaseModel):
    subject: str
    body: str

class CampaignOutput(BaseModel):
    reasoning: str
    email_1: EmailOutput
    email_2: EmailOutput
    email_3: EmailOutput
    email_4: EmailOutput
    email_5: EmailOutput
    email_6: EmailOutput
    email_7: EmailOutput
    email_8: EmailOutput
```

### Validation with error extraction for injection
```python
# Source: Context7 /pydantic/pydantic — models.md
try:
    output = CampaignOutput.model_validate_json(raw_text)
except ValidationError as e:
    # str(e) produces a human-readable multi-line error with field names and error types
    # This is the string to inject into the next user message
    error_summary = str(e)
```

### Extracting text from response
```python
# Source: Context7 /anthropics/anthropic-sdk-python — README.md
# response.content is a list of ContentBlock objects
# For a standard text response, content[0] is a TextBlock with a .text attribute
raw_text = response.content[0].text
```

### Em-dash post-processing
```python
# Source: Python docs — str.replace() — U+2014 EM DASH, U+2013 EN DASH
def strip_dashes(text: str) -> str:
    return text.replace("—", " ").replace("–", " ")
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Parse raw text, check for `"stop_reason"` key manually | Access `response.stop_reason` as a typed attribute | SDK ≥0.30 | Cleaner; IDE autocomplete; no KeyError risk |
| `pydantic.parse_obj_as()` for model parsing | `Model.model_validate()` and `Model.model_validate_json()` | Pydantic v2 (2023) | Old API removed in v2; new API is faster and clearer |
| Tenacity retry on all exceptions (`retry=retry_if_exception_type(Exception)`) | Explicit retryable predicate function | Best practice | Prevents retrying 401/400/422 permanent errors — PITFALL-C4 |
| String system prompt | List of TextBlockParam dicts | SDK ≥0.30 with caching | Enables cache_control; backward compatible |

**Deprecated/outdated:**
- `response.completion` property: removed in SDK v0.x; use `response.content[0].text`
- Pydantic v1 `parse_obj()` / `parse_raw()`: removed in Pydantic v2; use `model_validate()` / `model_validate_json()`

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Phase 3 writes the rendered Jinja2 prompt into `campaign_tokens.json` under the key `"rendered_prompt"` | Pattern 8 (Phase 3/4 interface) | Phase 4 fails with KeyError at runtime; easy to fix once the interface is agreed, but must be coordinated in Phase 3 plan |
| A2 | The system prompt content described in Pattern 9 (meta-instructions only: JSON output, Australian English, no dashes) is sufficient for GEN-01 | Pattern 9 | If the ASOTOS prompt also needs elements in the system turn rather than user turn, system prompt must be expanded; LOW risk since prompt_template.md covers content |
| A3 | Em/en dash replacement with a single space (not empty string) is the correct substitution | Pattern 7 | Visual artifact if wrong; easy to change; prompt rules say "no em dashes", space is safer than empty string |
| A4 | `response.content[0]` is always a TextBlock for a standard non-tool-use response | Pattern 4 | If Claude returns multiple content blocks, only first is used; safe for this use case since no tool use |
| A5 | `stop_reason = "end_turn"` is the only acceptable value (not "stop_sequence" or "pause_turn") | Pattern 3 | If stop_reason is "stop_sequence" (unlikely without a stop_sequences param), the check would incorrectly reject a valid response; add explicit check: `if response.stop_reason not in ("end_turn",)` |

---

## Open Questions (RESOLVED)

1. **Phase 3 → Phase 4 interface: where does the rendered prompt live?** — RESOLVED (Phase 4 renders itself)
   - Resolution: Phase 3 writes token VALUES only into `campaign_tokens.json` (no `rendered_prompt` key). Phase 4 is responsible for loading `prompt_template.md`, rendering it via Jinja2 with the token values from `campaign_tokens.json`, and calling Claude. This keeps Phase 3 focused on data computation and Phase 4 focused on generation. Phase 3's StrictUndefined validation-only render (discarding the result) confirms all tokens are present before Phase 4 renders for real.

2. **Should stop_reason "stop_sequence" be treated as success or failure?** — RESOLVED (any non-end_turn is failure)
   - Resolution: `if response.stop_reason != "end_turn": raise ValueError(f"Unexpected stop_reason: {response.stop_reason}")`. No `stop_sequences` param is passed so this should never fire, but the check is cheap and correct.

---

## Environment Availability

All dependencies for Phase 4 are installed via `requirements.txt` in Phase 1. No new tools or services are required.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| anthropic Python SDK | Claude API call | ✓ (Phase 1 requirements.txt) | 0.109.1 | — |
| pydantic | CampaignOutput model validation | ✓ (Phase 1 requirements.txt) | 2.13.4 | — |
| tenacity | anthropic_retry decorator (via api_client.py) | ✓ (Phase 1 requirements.txt) | 9.1.4 | — |
| scripts/lib/*.py | DLQ, file I/O, retry decorators | ✓ (Phase 1 scaffold) | — | — |
| ANTHROPIC_API_KEY secret | Client initialization | ✓ (GitHub Secrets, Phase 1 wired) | — | — |
| $RUNNER_TEMP/campaign_tokens.json | Input data | ✓ (Phase 3 output) | — | If absent: FileNotFoundError raised, DLQ written |

**Missing dependencies with no fallback:** None — all dependencies delivered in prior phases.

---

## Validation Architecture

> `nyquist_validation` is explicitly set to `false` in `.planning/config.json`. This section is omitted.

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | API key in GitHub Secrets env var; not app-layer auth |
| V3 Session Management | No | Stateless per-job execution |
| V4 Access Control | No | GitHub repository access controls |
| V5 Input Validation | Yes — Pydantic validates Claude output before it reaches any downstream system | Pydantic v2 `CampaignOutput.model_validate_json()` |
| V6 Cryptography | No | TLS handled by SDK |

### Known Threat Patterns for Anthropic SDK

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Claude API key leaked in exception logs | Information disclosure | Never log `str(exc)` for `APIStatusError` — it includes the `x-api-key` request header in its string representation; log only `exc.status_code` and `exc.message` |
| Unvalidated LLM output written to CRM (PITFALL-C1) | Tampering | Pydantic schema validation gate — Phase 5 only reads `campaign_output.json` after it passes validation |
| stop_reason truncation producing invalid emails (PITFALL-M3) | Tampering | Explicit `stop_reason` check before any parsing |
| Retry permanent errors (401, 400) wasting API budget | Denial of service | `_is_retryable_anthropic` predicate only matches `RateLimitError` and `APIStatusError` with `status_code >= 500`; 401/400/422 fail immediately |

---

## Sources

### Primary (HIGH confidence)
- Context7 `/anthropics/anthropic-sdk-python` — `stop_reason` values (message.py), `messages.create` with system as list (test_messages.py), `DEFAULT_MAX_RETRIES=2` (_base_client.py), error types (_exceptions.py), text extraction (README.md)
- Context7 `/pydantic/pydantic` — nested BaseModel pattern (models.md), `model_validate()` and `model_validate_json()` (models.md, json.md), `ValidationError.errors()` (index.md)
- `C:\Users\irahfo\Outreach\Asotos\CLAUDE.md` — max_retries=0 requirement, max_tokens=16000, stop_reason check rule, tenacity config, DLQ pattern
- `C:\Users\irahfo\Outreach\Asotos\.planning\REQUIREMENTS.md` — GEN-01 through GEN-06, REL-02 requirements
- `C:\Users\irahfo\Outreach\Asotos\.planning\research\STACK.md` — anthropic 0.109.1, pydantic 2.13.4 versions confirmed
- `C:\Users\irahfo\Outreach\Asotos\.planning\research\PITFALLS.md` — PITFALL-M3 (stop_reason), PITFALL-C1 (unvalidated output), PITFALL-C4 (permanent error retry)
- `C:\Users\irahfo\Outreach\Asotos\staff_domain_ASOTOS_prompt.md` — system prompt requirements (Australian English, no em dashes, raw JSON), expected response structure

### Secondary (MEDIUM confidence)
- `C:\Users\irahfo\Outreach\Asotos\.planning\phases\01-scaffold-trigger\01-RESEARCH.md` — lib module interfaces, anthropic_retry pattern confirmed
- `C:\Users\irahfo\Outreach\Asotos\.planning\research\SUMMARY.md` — architectural decisions on single Claude call, max_retries=0 rationale

### Tertiary (LOW confidence — ASSUMED items)
- Phase 3 → Phase 4 interface key `"rendered_prompt"` — inferred from pipeline design; not yet validated against Phase 3 plan
- System prompt exact wording — inferred from GEN-01 constraints; actual wording is implementer's discretion
- Em-dash replacement with space (vs empty string) — inferred from prompt rules; either is defensible

---

## Metadata

**Confidence breakdown:**
- Anthropic SDK API (messages.create, stop_reason, content extraction): HIGH — Context7 verified
- Pydantic v2 model_validate_json, ValidationError: HIGH — Context7 verified
- Tenacity anthropic_retry (already built in lib): HIGH — Phase 1 plan verified
- Manual validation retry loop pattern: MEDIUM — standard LLM correction pattern, not a specific library feature
- Phase 3/4 rendered_prompt interface: LOW — inferred from pipeline design, must be confirmed when Phase 3 is planned
- System prompt content: MEDIUM — inferred from requirements and prompt rules; not a locked specification

**Research date:** 2026-06-12
**Valid until:** 2026-09-12 (stable stack; anthropic SDK may release new versions but 0.109.1 is pinned)
