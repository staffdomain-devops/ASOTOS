# Technology Stack: ASOTOS GitHub Actions Pipeline

**Project:** ASOTOS — AI email campaign pipeline for Staff Domain
**Researched:** 2026-06-12
**Scope:** Python 3.12 on GitHub Actions ubuntu-latest

---

## Overview

The pipeline is a linear data-fetch → AI-generate → write-back workflow triggered by
`workflow_dispatch`. There is no web server, no database, and no persistent state beyond
HubSpot contact properties and GitHub Actions artifacts. The stack is therefore minimal:
standard HTTP clients, two official API SDKs, and retrying glue. Every library choice
below prioritises current versions and verified compatibility.

---

## Libraries

### Core Dependencies

| Library | Spec in PROJECT.md | Current Latest | Recommendation | Confidence |
|---|---|---|---|---|
| `hubspot-api-client` | `>=12.0.0` | `12.0.0` (2025-05-07) | Pin to `==12.0.0` | HIGH |
| `requests` | `>=2.31.0` | `2.34.2` | Pin to `==2.34.2` | HIGH |
| `beautifulsoup4` | `>=4.12.0` | `4.15.0` (2026-06-07) | Pin to `==4.15.0` | HIGH |
| `anthropic` | `>=0.30.0` | `0.109.1` (2026-06-09) | Pin to `==0.109.1` | HIGH |
| `tiktoken` | `>=0.7.0` | `0.13.0` (2025-10-06) | Pin to `==0.13.0` | HIGH |
| `tenacity` | `>=9.0.0` | `9.1.4` (2026-02-07) | Pin to `==9.1.4` | HIGH |
| `lxml` | not listed | current | Add as `lxml` — bs4 optional backend but faster HTML parsing | MEDIUM |

All versions confirmed via PyPI JSON API on 2026-06-12.

**Why pin exact versions rather than `>=`:**
GitHub Actions runners are ephemeral. `pip install` with a floor spec will silently upgrade
on next run if a new release drops. A pin produces reproducible builds and avoids unexpected
breakage from minor API changes mid-campaign. Regenerate pins via `pip-compile` when
intentionally upgrading.

### Library Rationale

**`hubspot-api-client==12.0.0`**
The official HubSpot Python SDK for v3 CRM APIs. v12 is the only release with full support
for the CRM Notes v3 endpoint (`client.crm.objects.notes`) and the v4 Associations API needed
to link notes to contacts. Do not use the legacy engagements API (`/engagements/v1/`) — HubSpot
is deprecating it and the Python SDK no longer exposes it under `crm`. The SDK handles OAuth
and Private App bearer token auth identically via `HubSpot(access_token=token)`.

**`anthropic==0.109.1`**
The latest official Anthropic Python SDK. Verified `claude-sonnet-4-6` is a stable, non-dated
model ID (confirmed in model overview: no date suffix required). The SDK's built-in retry
handles 429 and 5xx by default (2 attempts, 0.5 s initial delay doubling up to 8 s). Set
`max_retries=0` on the `Anthropic()` client so tenacity owns all retry policy uniformly.

**`requests==2.34.2`**
Used exclusively for Chorus AI REST API calls. The HubSpot SDK uses its own HTTP transport,
so `requests` is only needed for Chorus. 2.34.x maintains the same API surface as 2.31.x;
the upgrade is a free security and compatibility improvement.

**`beautifulsoup4==4.15.0`**
Used to strip HTML from HubSpot rich-text fields (email history, meeting notes) before
injecting into the prompt. The `lxml` parser is faster than the default `html.parser` and
is recommended; add `lxml` to requirements.txt alongside bs4.

**`tiktoken==0.13.0`**
Used in `compute_campaign_tokens.py` to count tokens before calling the Anthropic API. This
prevents hitting the `max_tokens` ceiling mid-generation. The `cl100k_base` encoding maps
closely enough to claude-sonnet-4-6's tokenizer for budgeting purposes (not identical, but
sufficient for a conservative pre-check with ~10% margin).

**`tenacity==9.1.4`**
Retry orchestration for all three external API calls (HubSpot SDK, requests/Chorus, Anthropic
SDK). Policy: `stop=stop_after_attempt(6)`, `wait=wait_exponential(multiplier=1, min=2, max=60)`,
`reraise=True`. The `reraise=True` flag is critical — without it tenacity swallows the original
exception and raises `RetryError`, which loses the HTTP status code needed for DLQ logging.

### Do NOT Use

| Package | Why Not |
|---|---|
| `httpx` | Not needed — requests is already in the dep tree via hubspot-api-client. Two HTTP clients for one project is wasteful. |
| `aiohttp` / `asyncio` | The pipeline runs sequentially (fetch → compute → generate → write). Async adds complexity with zero benefit in a single-contact-per-run model. |
| `openai` | Wrong vendor. |
| `langchain` / `llama-index` | Abstraction over an API we call directly once. The overhead (tokens, latency, dependency surface) is not justified. |
| HubSpot legacy engagements (`/v1/`) | Deprecated. Use `/crm/v3/objects/notes` via the SDK. |

---

## API Authentication Patterns

### HubSpot — Private App Token

**Auth pattern:** Bearer token in `Authorization` header. The SDK handles this transparently:

```python
from hubspot import HubSpot
import os

client = HubSpot(access_token=os.environ["HUBSPOT_API_KEY"])
```

`HUBSPOT_API_KEY` must be a **Private App** access token generated in HubSpot Settings >
Integrations > Private Apps. Do NOT use the legacy API key (deprecated) or OAuth flow.

**Required scopes for this pipeline.** When creating the Private App, enable exactly these:

| Scope | Purpose |
|---|---|
| `crm.objects.contacts.read` | Fetch contact properties, email/meeting history |
| `crm.objects.contacts.write` | Write `email_1`–`email_8`, `subject_1`–`subject_8`, `asotos_generated_date` |
| `crm.objects.notes.read` | Required alongside write by HubSpot's scope validator |
| `crm.objects.notes.write` | Create the campaign note engagement on the contact |
| `crm.objects.owners.read` | Resolve owner name from owner ID (fetched as a property on the contact) |

The `crm.objects.notes` scopes may appear unavailable in the scope picker on some HubSpot
subscription tiers. If missing, check with Super Admin — this is a known HubSpot portal
configuration issue separate from the API capability (MEDIUM confidence — based on community
reports, not official docs confirmation).

**Writing contact properties:** Use `client.crm.contacts.basic_api.update()` with
`SimplePublicObjectInput`. Writing 16 properties (8 subjects + 8 bodies) in a single call
is fine; the SDK serialises them as a flat properties dict.

**Creating a note and associating with contact:**

```python
from hubspot.crm.objects.notes import SimplePublicObjectInputForCreate

note_input = SimplePublicObjectInputForCreate(
    properties={
        "hs_note_body": note_html,
        "hs_timestamp": str(int(time.time() * 1000)),  # milliseconds
    },
    associations=[{
        "to": {"id": contact_id},
        "types": [{
            "associationCategory": "HUBSPOT_DEFINED",
            "associationTypeId": 202  # NOTE_TO_CONTACT
        }]
    }]
)
client.crm.objects.notes.basic_api.create(
    simple_public_object_input_for_create=note_input
)
```

`associationTypeId 202` is the HubSpot-defined type for note → contact. The `hs_timestamp`
must be in epoch milliseconds as a string. The `hs_note_body` accepts HTML.

### Chorus AI — REST API v1

**Auth pattern:** Raw bearer token in the `Authorization` header. **No `Bearer` prefix** —
Chorus expects just the raw token value:

```python
headers = {
    "Authorization": os.environ["CHORUS_API_TOKEN"],
    "Content-Type": "application/json",
}
```

Validated by: `GET https://chorus.ai/api/v1/me` — if this returns 200, the token is valid.
Use this as a health check at the start of `fetch_chorus.py`.

**Transcript endpoint:** `GET https://chorus.ai/api/v3/engagements/{conversation_id}`
returns meeting metadata and full transcript. The `v3` path is current (v1 still accessible
but v3 is what the official docs reference for engagements). Extract conversation IDs from
HubSpot meeting notes during `fetch_hubspot.py` — they appear as Chorus URLs in the body.

**Rate limits:** No official rate limit figures found in public docs. Treat as unknown.
Apply tenacity with a conservative `max=30` cap for Chorus calls specifically (not the 60 s
cap used for HubSpot/Anthropic). Log the actual `Retry-After` header value if a 429 is
received, as Chorus may return one. If `fetch_chorus.py` exhausts retries, continue silently
(silent fallback per PROJECT.md spec) rather than failing the whole workflow.

**Error handling note:** Chorus returns 404 when a conversation ID is valid but the call
recording was deleted or the token user lacks access. Treat 404 as a silent fallback (no
transcript), not a retryable error.

### Anthropic Claude API

**Model ID:** `claude-sonnet-4-6` (confirmed stable dateless ID from official model overview;
no date suffix required, not an alias pointer).

**Context window:** 1,000,000 tokens input; **64,000 tokens max output.**

**`max_tokens` recommendation for this pipeline:** Set `max_tokens=16000`. Eight emails at
roughly 300–500 words each ≈ 2,400–4,000 words ≈ 3,200–5,400 tokens. 16,000 gives 3× headroom
for the JSON wrapper, reasoning output, and subject lines. Do not use the full 64k — it makes
cost budgeting unpredictable and signals a prompt design problem if you approach it.

**Streaming vs non-streaming:** Use non-streaming (`client.messages.create()`). Streaming
adds complexity (stream management, partial JSON parsing) with no benefit — the GitHub Actions
step has no UI to update and the complete response is needed before writing to HubSpot.

**Prompt caching:** Apply `cache_control: {type: "ephemeral"}` to the system prompt block.
Minimum cacheable size for `claude-sonnet-4-6` is **1,024 tokens** (confirmed). The ASOTOS
system prompt (`staff_domain_ASOTOS_prompt.md`) plus Data Honesty Rules will comfortably
exceed 1,024 tokens. Cache TTL defaults to 5 minutes at no extra charge. The workflow runs
once per contact and likely doesn't re-use the cache within a run, but caching is free on
5-minute TTL and protects against any retry needing to resubmit the full system prompt.

```python
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0)

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=16000,
    system=[
        {
            "type": "text",
            "text": system_prompt_text,
            "cache_control": {"type": "ephemeral"},
        }
    ],
    messages=[{"role": "user", "content": user_message}],
)
```

**Error handling with tenacity (correct pattern):**

```python
from anthropic import RateLimitError, APIStatusError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception, reraise

def _is_retryable_anthropic(exc):
    if isinstance(exc, RateLimitError):  # 429
        return True
    if isinstance(exc, APIStatusError) and exc.status_code >= 500:
        return True
    return False

@retry(
    retry=retry_if_exception(_is_retryable_anthropic),
    stop=stop_after_attempt(6),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    reraise=True,
)
def call_claude(client, system_prompt, user_message):
    return client.messages.create(...)
```

Setting `max_retries=0` on the `Anthropic()` client disables the SDK's internal retry and
gives tenacity full control. Without this, you get double-retry: SDK (2 attempts) × tenacity
(6 attempts) = up to 12 actual API calls.

---

## GitHub Actions Configuration

### Secrets

Store all four secrets in **GitHub repository Secrets** (Settings > Secrets and variables >
Actions > Repository secrets):

| Secret Name | Value |
|---|---|
| `HUBSPOT_API_KEY` | HubSpot Private App access token |
| `CHORUS_API_TOKEN` | Chorus raw API token (no `Bearer` prefix in the secret value) |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `TEAMS_WEBHOOK_URL` | Microsoft Teams incoming webhook URL |

Reference in workflow YAML as `${{ secrets.HUBSPOT_API_KEY }}`. Never echo secrets in
`run:` steps. GitHub masks them in logs automatically, but write-to-file patterns can
accidentally expose them in artifacts — see RUNNER_TEMP below.

### `workflow_dispatch` Inputs

```yaml
on:
  workflow_dispatch:
    inputs:
      contact_id:
        description: "HubSpot contact ID"
        required: true
        type: string
      contact_email:
        description: "HubSpot contact email (for logging)"
        required: true
        type: string
```

Reference in steps as `${{ inputs.contact_id }}` (not `github.event.inputs.contact_id` —
the `inputs` context preserves types and is the current recommended form).

Make.com triggers via the GitHub API endpoint:
`POST /repos/{owner}/{repo}/actions/workflows/{workflow_file}/dispatches`
with `inputs` as JSON in the request body.

### `RUNNER_TEMP` Usage

`$RUNNER_TEMP` (`${{ runner.temp }}` in expressions) is the correct location for inter-step
data files. It is:
- Outside the workspace (`$GITHUB_WORKSPACE`) so data files are never committed
- Cleaned up automatically after the job
- Not included in artifacts unless you explicitly upload from that path

Pattern for passing data between steps:

```yaml
- name: Fetch HubSpot data
  env:
    HUBSPOT_API_KEY: ${{ secrets.HUBSPOT_API_KEY }}
    CONTACT_ID: ${{ inputs.contact_id }}
    RUNNER_TEMP: ${{ runner.temp }}
  run: python scripts/fetch_hubspot.py

- name: Generate campaign
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    RUNNER_TEMP: ${{ runner.temp }}
  run: python scripts/generate_campaign.py
```

Scripts read/write `$RUNNER_TEMP/hubspot_data.json`, `$RUNNER_TEMP/chorus_data.json`, etc.
using `os.environ["RUNNER_TEMP"]`.

### Artifact Upload Pattern

```yaml
- name: Upload campaign output
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: campaign-output-${{ inputs.contact_id }}
    path: ${{ runner.temp }}/campaign_output.json
    retention-days: 7
    if-no-files-found: warn

- name: Upload DLQ on failure
  if: failure()
  uses: actions/upload-artifact@v4
  with:
    name: failed-contact-${{ inputs.contact_id }}
    path: ${{ runner.temp }}/failed_contacts.json
    retention-days: 7
    if-no-files-found: ignore
```

Use `actions/upload-artifact@v4` (current; v7.0.1 tag as of 2026-04-10). Do not use v3 —
it is deprecated and significantly slower. Artifact names must be unique per run; appending
`${{ inputs.contact_id }}` satisfies this if the same workflow runs multiple times per day.

`if: always()` on the output upload ensures the artifact is present even if a downstream
notification step fails. `if: failure()` on the DLQ upload ensures it only appears when
there is actually a failed contact record.

---

## Dependency Management

**File structure:**

```
requirements.txt        # exact pinned versions for production
requirements-dev.txt    # unpinned, for local development tooling
```

**`requirements.txt` (exact pins):**

```
hubspot-api-client==12.0.0
requests==2.34.2
beautifulsoup4==4.15.0
lxml==5.3.0          # verify latest on PyPI; bs4 optional backend
anthropic==0.109.1
tiktoken==0.13.0
tenacity==9.1.4
```

**Workflow installation step:**

```yaml
- name: Install dependencies
  run: pip install -r requirements.txt
```

Cache pip with `actions/cache@v4` keyed on the hash of `requirements.txt` if workflow
cold-start time becomes a concern (typically saves 30–60 seconds per run):

```yaml
- uses: actions/cache@v4
  with:
    path: ~/.cache/pip
    key: ${{ runner.os }}-pip-${{ hashFiles('requirements.txt') }}
    restore-keys: |
      ${{ runner.os }}-pip-
```

**Updating pins:** Use `pip-compile` from `pip-tools` (in `requirements-dev.txt`) to
regenerate `requirements.txt` when upgrading. Run `pip-compile --upgrade requirements.in`
from a clean environment and review the diff before committing.

---

## Tenacity Retry Patterns

The pipeline has three distinct retry surfaces. Each needs a tailored predicate.

### HubSpot SDK (`ApiException`)

```python
from hubspot.crm.contacts.exceptions import ApiException
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception, reraise

def _is_retryable_hubspot(exc):
    if isinstance(exc, ApiException):
        return exc.status in (429, 500, 502, 503, 504)
    return False

hubspot_retry = retry(
    retry=retry_if_exception(_is_retryable_hubspot),
    stop=stop_after_attempt(6),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    reraise=True,
)
```

### Chorus AI (`requests.HTTPError`)

```python
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception, reraise

def _is_retryable_chorus(exc):
    if isinstance(exc, requests.HTTPError):
        return exc.response is not None and exc.response.status_code in (429, 500, 502, 503, 504)
    return False

chorus_retry = retry(
    retry=retry_if_exception(_is_retryable_chorus),
    stop=stop_after_attempt(6),
    wait=wait_exponential(multiplier=1, min=2, max=30),  # shorter cap for Chorus
    reraise=True,
)
```

Note: call `response.raise_for_status()` after each Chorus request so HTTPError is raised
and tenacity can inspect it. Without `raise_for_status()` tenacity sees no exception.

### Anthropic SDK (`RateLimitError`, `APIStatusError`)

See the Anthropic section above. Key difference: set `max_retries=0` on the `Anthropic()`
client to prevent double-retry.

### DLQ (Dead Letter Queue) on Exhaustion

All three decorators use `reraise=True`. The caller wraps each decorated function in a
`try/except` block that:
1. Catches the reraised exception after all retries are exhausted
2. Writes a JSON record to `$RUNNER_TEMP/failed_contacts.json`
3. Raises a `SystemExit(1)` to fail the GitHub Actions step

This ensures the failure artifact and Teams notification are still uploaded/sent.

---

## Confidence Notes

| Area | Confidence | Basis |
|---|---|---|
| Library versions (hubspot, anthropic, requests, bs4, tiktoken, tenacity) | HIGH | PyPI JSON API queried directly on 2026-06-12 |
| HubSpot Private App auth pattern | HIGH | Context7 / official GitHub SDK README |
| HubSpot notes scopes (`crm.objects.notes.*`) | MEDIUM | Confirmed via community posts and scope naming pattern; `crm.objects.notes` may be hidden in UI on some tiers — validate in actual HubSpot portal |
| HubSpot note association type ID 202 | MEDIUM | Consistent across community examples; verify with `GET /crm/v4/associations/notes/contacts/labels` before hardcoding |
| Chorus auth header format (raw token, no Bearer prefix) | MEDIUM | Confirmed by Nexla connector docs (third-party source); no official Chorus docs were accessible. Validate against `https://chorus.ai/api/v1/me` on first run. |
| Chorus transcript endpoint (`/v3/engagements/`) | LOW | Inferred from community references and API validator endpoint; official docs at api-docs.chorus.ai returned empty content during research. Treat as needing validation on first integration run. |
| Chorus rate limits | LOW | Not found in any public source. Implement conservative tenacity and log all 429 responses. |
| claude-sonnet-4-6 model ID (dateless, stable) | HIGH | Confirmed in official Anthropic model overview page |
| claude-sonnet-4-6 max output tokens (64k) | HIGH | Confirmed in official Anthropic model comparison table |
| Prompt caching min threshold for sonnet-4-6 (1,024 tokens) | HIGH | Confirmed in official Anthropic prompt caching docs |
| GitHub Actions upload-artifact v4 syntax | HIGH | Confirmed via official actions/upload-artifact repo |
| `workflow_dispatch` `inputs` context (vs `github.event.inputs`) | HIGH | Confirmed in official GitHub Actions docs |
| Tenacity `reraise=True` behaviour | HIGH | Confirmed via Context7 / official tenacity docs |
| SDK double-retry problem (`max_retries=0` on Anthropic client) | HIGH | Confirmed via Anthropic SDK source code (DEFAULT_MAX_RETRIES=2) and community pattern |
