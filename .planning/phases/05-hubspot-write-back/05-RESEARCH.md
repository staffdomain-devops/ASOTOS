# Phase 5: HubSpot Write-Back — Research

**Researched:** 2026-06-12
**Domain:** HubSpot CRM API v3 — contact property writes, notes creation, association management
**Confidence:** HIGH (contact PATCH pattern); MEDIUM (note association type IDs, notes scope availability)

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| WRITE-01 | Write `subject_1`–`subject_8` as single-line text contact properties (overwrite on each run) | PATCH /crm/v3/objects/contacts/{id} with SimplePublicObjectInput supports bulk property write; overwrite is default PATCH semantics |
| WRITE-02 | Write `email_1`–`email_8` as multi-line text contact properties (overwrite, up to 65,000 chars each) | Multi-line text type in HubSpot stores up to 65,536 chars; PATCH handles all 16 properties in one call |
| WRITE-03 | Write `asotos_generated_date` as date contact property | HubSpot date properties require epoch milliseconds at midnight UTC (e.g., `1652659200000`) or YYYY-MM-DD string — both accepted; midnight UTC epoch is safest |
| WRITE-04 | Create HubSpot note with reasoning summary + email preview; non-fatal if creation fails | POST /crm/v3/objects/notes with `hs_note_body` + `hs_timestamp`; associations array with associationTypeId 202 (note→contact); CLAUDE.md mandates try/except around note creation only |
</phase_requirements>

---

## Summary

Phase 5 writes campaign output from `campaign_output.json` back to HubSpot in two distinct operations: (1) a single atomic PATCH to the contact's properties endpoint writing 17 properties (16 emails/subjects + generated date), and (2) a POST to the notes endpoint creating a campaign note associated to the contact. The property PATCH is the critical path — it must succeed or the pipeline fails. The note creation is explicitly non-fatal per CLAUDE.md.

The HubSpot Python SDK (`hubspot-api-client==12.0.0`) provides `client.crm.contacts.basic_api.update()` for the PATCH and `client.crm.objects.notes.basic_api.create()` for the note POST. Both are available in the SDK's `crm` namespace. Association type IDs are HubSpot-defined constants: 202 for note→contact and 190 for note→company. The STACK.md note about associating to both contact AND company is architecture context from the earlier research — WRITE-04 in REQUIREMENTS.md specifies contact-only association (associationTypeId 202); the planner should not add company association unless WRITE-04 is updated.

The primary risk in this phase is the notes scope availability issue (documented in STATE.md as a pre-Phase 5 blocker): `crm.objects.notes.write` may not appear in the Private App scope picker on some HubSpot portal tiers. The error surfaces as HTTP 403 with a message about missing scope. The non-fatal pattern in CLAUDE.md is the correct mitigation — log a warning and continue so the 17 property writes succeed regardless of note creation outcome.

**Primary recommendation:** Single PATCH for all 17 contact properties using `SimplePublicObjectInput`; wrapped note creation in its own try/except with `hubspot_retry` applied only to the PATCH (not the note POST, since a scope-403 is a permanent error that must not be retried).

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Read campaign_output.json | `write_hubspot.py` script | `lib/file_io.read_json` | File written by Phase 4 to RUNNER_TEMP; file_io helper provides typed read |
| Contact property write (17 props) | `write_hubspot.py` → HubSpot API | — | PATCH /crm/v3/objects/contacts/{id} via SDK `basic_api.update` |
| Date formatting for `asotos_generated_date` | `write_hubspot.py` script | — | Must convert today's date to midnight UTC epoch milliseconds before PATCH |
| Note body construction | `write_hubspot.py` script | — | Assembles reasoning summary + email preview from campaign_output.json |
| Note creation + association | `write_hubspot.py` → HubSpot Notes API | — | POST /crm/v3/objects/notes via SDK `basic_api.create`; non-fatal try/except block |
| Retry on HubSpot failures | `lib/api_client.hubspot_retry` | — | Wraps PATCH only; note POST is not retried (403 scope error is permanent) |
| DLQ on unrecovered failure | `lib/dlq_writer.write_dlq` | — | Called before re-raising if PATCH exhausts retries |

---

## Standard Stack

### Core (already in requirements.txt from Phase 1)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| hubspot-api-client | ==12.0.0 | HubSpot CRM read/write SDK | [VERIFIED: STACK.md] — Only version with full `crm.objects.notes` and v4 Associations API |
| tenacity | ==9.1.4 | Retry decorator for PATCH call | [VERIFIED: STACK.md] — `hubspot_retry` already defined in `lib/api_client.py` |

No new dependencies are introduced by Phase 5. All required packages are already pinned in `requirements.txt` from Phase 1.

### No New Installations Required

```bash
# Phase 5 requires NO new pip installs — all dependencies already in requirements.txt
```

---

## Architecture Patterns

### System Architecture Diagram

```
RUNNER_TEMP/campaign_output.json
        |
        v
write_hubspot.py
        |
        +--[hubspot_retry]---> PATCH /crm/v3/objects/contacts/{contact_id}
        |                      properties: {subject_1..8, email_1..8,
        |                                   asotos_generated_date}
        |                      <-- HTTP 200 (success) or 4xx/5xx (fail)
        |
        +--[try/except]------> POST /crm/v3/objects/notes
                               properties: {hs_note_body, hs_timestamp}
                               associations: [{to: contact_id, typeId: 202}]
                               <-- HTTP 201 (success) or 403 (scope missing)
                                   [non-fatal: log warning and continue]
```

### Recommended Project Structure

```
scripts/
├── lib/
│   ├── api_client.py     # hubspot_retry decorator (used by PATCH call)
│   ├── dlq_writer.py     # write_dlq (called on PATCH failure)
│   └── file_io.py        # read_json (reads campaign_output.json)
└── write_hubspot.py      # Phase 5 implementation
```

### Pattern 1: Atomic PATCH for All 17 Contact Properties

Write all 16 email/subject properties plus `asotos_generated_date` in a single SDK call. This is atomic at the HTTP level — either all properties are accepted or none are written (HubSpot returns 400/422 if any property is invalid, before committing any writes).

```python
# Source: Context7 /hubspot/hubspot-api-python — contacts update pattern
import os
import sys
import time
import json
from datetime import date, timezone, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from api_client import hubspot_retry
from dlq_writer import write_dlq
from file_io import read_json

from hubspot import HubSpot
from hubspot.crm.contacts import SimplePublicObjectInput
from hubspot.crm.contacts.exceptions import ApiException

client = HubSpot(access_token=os.environ["HUBSPOT_API_KEY"])
contact_id = os.environ["CONTACT_ID"]
contact_email = os.environ["CONTACT_EMAIL"]

campaign = read_json("campaign_output.json")

# Build the 17-property dict
properties = {}
for i in range(1, 9):
    properties[f"subject_{i}"] = campaign["emails"][i - 1]["subject"]
    properties[f"email_{i}"]   = campaign["emails"][i - 1]["body"]

# asotos_generated_date: midnight UTC epoch milliseconds for today
today_midnight_utc_ms = int(
    datetime.combine(date.today(), datetime.min.time())
    .replace(tzinfo=timezone.utc)
    .timestamp() * 1000
)
properties["asotos_generated_date"] = str(today_midnight_utc_ms)

@hubspot_retry
def patch_contact(cid, props):
    update_input = SimplePublicObjectInput(properties=props)
    return client.crm.contacts.basic_api.update(
        contact_id=cid,
        simple_public_object_input=update_input
    )

try:
    patch_contact(contact_id, properties)
    print(f"[write_hubspot] Contact {contact_id} properties updated successfully")
except ApiException as e:
    write_dlq(contact_id, contact_email, "write_hubspot_patch", str(e))
    raise SystemExit(1)
```

### Pattern 2: Non-Fatal Note Creation with Contact Association

The note creation is wrapped in its own `try/except`. Any exception (including 403 scope-missing) is logged as a warning; the script exits 0 regardless.

```python
# Source: HubSpot Community confirmed + Context7 /hubspot/hubspot-api-python association pattern
from hubspot.crm.objects.notes import SimplePublicObjectInputForCreate
from hubspot.crm.objects.notes.exceptions import ApiException as NotesApiException

def build_note_body(campaign: dict) -> str:
    """Constructs HTML note body: reasoning summary + preview of email 1."""
    reasoning = campaign.get("reasoning", "No reasoning captured.")
    preview = campaign["emails"][0]["body"][:500]  # first 500 chars of email 1
    return (
        f"<p><strong>ASOTOS Campaign Generated</strong></p>"
        f"<p><strong>Reasoning:</strong> {reasoning}</p>"
        f"<hr>"
        f"<p><strong>Email 1 Preview:</strong><br>{preview}...</p>"
    )

note_body = build_note_body(campaign)
hs_timestamp = str(int(time.time() * 1000))  # epoch milliseconds as string

note_input = SimplePublicObjectInputForCreate(
    properties={
        "hs_note_body": note_body,
        "hs_timestamp": hs_timestamp,
    },
    associations=[{
        "to": {"id": contact_id},
        "types": [{
            "associationCategory": "HUBSPOT_DEFINED",
            "associationTypeId": 202  # NOTE_TO_CONTACT — confirmed via community + API labels endpoint
        }]
    }]
)

try:
    client.crm.objects.notes.basic_api.create(
        simple_public_object_input_for_create=note_input
    )
    print(f"[write_hubspot] Note created and associated to contact {contact_id}")
except Exception as e:
    # NON-FATAL: log warning and continue per CLAUDE.md
    print(f"[write_hubspot] WARNING: Note creation failed (non-fatal): {e}", file=sys.stderr)
```

**Why `Exception` not `NotesApiException` for note catch:** The scope-missing error may surface as a generic `ApiException` from the base `hubspot` package or as an `openapi_client` exception depending on the SDK version. Catching the base `Exception` ensures the non-fatal pattern is robust regardless of exception class.

### Pattern 3: Date Format for `asotos_generated_date`

HubSpot date properties require either:
- **Epoch milliseconds string at midnight UTC** — e.g., `"1652659200000"` for 2022-05-16
- **YYYY-MM-DD string** — e.g., `"2026-06-12"` (confirmed accepted by v3 API)

**Use epoch milliseconds to avoid any ambiguity.** The string representation (not integer) is what the SDK serialises correctly.

```python
# Source: HubSpot Community — confirmed midnight UTC requirement
from datetime import date, datetime, timezone

def today_as_hubspot_date() -> str:
    """Returns today's date as midnight UTC epoch milliseconds string."""
    today = date.today()
    midnight_utc = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    return str(int(midnight_utc.timestamp() * 1000))
```

**Critical:** Passing any timestamp that is NOT midnight UTC for a `date`-type property returns a 400 error. The formula above is correct.

### Anti-Patterns to Avoid

- **Separate PATCH per property:** Do not loop 17 individual PATCH calls. A single PATCH with all 17 properties is atomic, faster, and uses fewer API calls against HubSpot's rate limit (150 req/10s).
- **Retrying note creation:** Do not apply `hubspot_retry` to the note creation call. A 403 scope error is permanent — retrying it wastes API calls and retry budget.
- **Applying `hubspot_retry` as a decorator on the note function:** If the note POST is inside the `try/except` block, do NOT wrap the inner function with `hubspot_retry`. The retry decorator will eat the exception before the outer `except` sees it.
- **Using `datetime.utcnow()` for the timestamp:** Deprecated in Python 3.12+. Use `datetime.now(timezone.utc)` instead.
- **Passing integer (not string) for `hs_timestamp`:** HubSpot note timestamps must be epoch milliseconds **as a string**. An integer causes a 400 type mismatch.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP PATCH for contact properties | Custom `requests.patch()` call | `client.crm.contacts.basic_api.update()` from `hubspot-api-client` | SDK handles auth header, request serialisation, and response deserialisation correctly |
| Note creation with associations | Custom `requests.post()` to `/crm/v3/objects/notes` | `client.crm.objects.notes.basic_api.create()` | SDK's `SimplePublicObjectInputForCreate` model handles association array serialisation |
| Exception classification for retries | Custom status-code inspection | `hubspot_retry` from `lib/api_client.py` already built in Phase 1 | Already correctly classifies (429, 5xx) as retryable; 4xx as permanent |
| DLQ writing on failure | Inline JSON write in except block | `write_dlq()` from `lib/dlq_writer.py` | Canonical CLAUDE.md implementation; consistent format across all scripts |
| File reading from RUNNER_TEMP | `open(os.path.join(...))` | `read_json()` from `lib/file_io.py` | Handles RUNNER_TEMP path resolution and encoding correctly |

**Key insight:** All shared utilities were scaffolded in Phase 1 specifically to serve Phases 2–5. Phase 5 should import from `lib/` without adding new logic to those modules.

---

## Common Pitfalls

### Pitfall 1: `crm.objects.notes.write` Scope Not Visible in Private App UI
**What goes wrong:** When creating the HubSpot Private App, the `crm.objects.notes.write` and `crm.objects.notes.read` scopes do not appear in the scope picker on some HubSpot subscription tiers (confirmed community reports from 2024–2025). If the scope is not granted, the note POST returns HTTP 403 with a message like "The scope needed for this API call isn't available" or "One or more of the following scopes are required: crm.objects.notes.write".
**Why it happens:** HubSpot split note permissions out of the general engagement scope in a recent update, but the scope picker UI does not always reflect this on all portal tiers.
**How to avoid:** Test note creation before Phase 5 execution using the Private App token against a test contact. If 403 occurs: (1) check under "CRM" section in Private App scopes for notes entries, (2) contact HubSpot support if not visible. The non-fatal pattern handles runtime absence gracefully, but the scope issue must be diagnosed before production use.
**Warning signs:** HTTP 403 on `POST /crm/v3/objects/notes` — distinct from a 422 (malformed body) or 401 (invalid token).

### Pitfall 2: Date Property Type Mismatch (400 on `asotos_generated_date`)
**What goes wrong:** Passing today's datetime as an ISO string with time component (e.g., `"2026-06-12T10:30:00"`) or as a non-midnight epoch timestamp causes HubSpot to return 400: "value is not a valid date". HubSpot `date`-type properties are date-only and require midnight UTC alignment.
**Why it happens:** Python's `datetime.now()` includes a time component; passing it directly fails. The error comes from the PATCH call for all 17 properties together, so a date format error blocks the entire property write.
**How to avoid:** Use `today_as_hubspot_date()` pattern above. Validate the epoch value is divisible by 86400000 (midnight check) before sending.
**Warning signs:** `ApiException` with status 400 and body mentioning "asotos_generated_date".

### Pitfall 3: Note Body Contains Unescaped HTML That Corrupts Display
**What goes wrong:** If the email body content (which may contain `<`, `>`, `&`) is injected directly into the `hs_note_body` HTML string without escaping, the note renders incorrectly in the HubSpot timeline.
**Why it happens:** `hs_note_body` accepts HTML, so unescaped characters are interpreted as HTML tags.
**How to avoid:** Use `html.escape()` on any dynamic text content inserted into `hs_note_body`, except for intentional HTML formatting tags you control directly.
**Warning signs:** Note body shows broken HTML in the HubSpot contact timeline.

### Pitfall 4: `campaign_output.json` Schema Mismatch
**What goes wrong:** `write_hubspot.py` assumes `campaign["emails"][i]["subject"]` and `campaign["emails"][i]["body"]` but Phase 4 may use `campaign["email_1"]["subject"]` or another schema shape. If the key doesn't exist, `KeyError` crashes the script before any writes occur.
**Why it happens:** Phase 4 defines the `campaign_output.json` schema; Phase 5 consumes it. If Phase 4's schema deviates from what Phase 5 expects, silent crash occurs.
**How to avoid:** Phase 5 must read and validate the `campaign_output.json` schema at the top of the script (check all 8 emails exist, each has `subject` and `body`). The exact schema should be agreed upon by Phases 4 and 5 plans and documented in both.
**Warning signs:** `KeyError` or `IndexError` on first line that accesses campaign content.

### Pitfall 5: `ApiException.status` Not Available for All Exception Types
**What goes wrong:** The `hubspot_retry` decorator in `lib/api_client.py` inspects `exc.status` from `hubspot.crm.contacts.exceptions.ApiException`. But the notes API raises exceptions from a different module (`hubspot.crm.objects.notes.exceptions`). If notes exceptions were mistakenly wrapped in `hubspot_retry`, the retry predicate may fail to classify them correctly.
**Why it happens:** HubSpot SDK generates separate exception classes per API namespace; they are not all the same class.
**How to avoid:** Do NOT apply `hubspot_retry` to the note creation call. The non-fatal `try/except Exception` block handles all note errors directly.
**Warning signs:** `AttributeError: 'XXX' object has no attribute 'status'` in retry predicate.

---

## Code Examples

### Complete `write_hubspot.py` Skeleton

```python
# Source: CLAUDE.md patterns + Context7 /hubspot/hubspot-api-python
import os
import sys
import time
import html
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from api_client import hubspot_retry
from dlq_writer import write_dlq
from file_io import read_json

from hubspot import HubSpot
from hubspot.crm.contacts import SimplePublicObjectInput
from hubspot.crm.contacts.exceptions import ApiException
from hubspot.crm.objects.notes import SimplePublicObjectInputForCreate


def today_as_hubspot_date() -> str:
    today = date.today()
    midnight_utc = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    return str(int(midnight_utc.timestamp() * 1000))


def build_properties(campaign: dict) -> dict:
    props = {}
    for i in range(1, 9):
        email_obj = campaign["emails"][i - 1]
        props[f"subject_{i}"] = email_obj["subject"]
        props[f"email_{i}"]   = email_obj["body"]
    props["asotos_generated_date"] = today_as_hubspot_date()
    return props


def build_note_body(campaign: dict) -> str:
    reasoning = html.escape(campaign.get("reasoning", "No reasoning captured."))
    preview = html.escape(campaign["emails"][0]["body"][:500])
    return (
        f"<p><strong>ASOTOS Campaign Generated</strong></p>"
        f"<p><strong>Reasoning:</strong><br>{reasoning}</p>"
        f"<hr>"
        f"<p><strong>Email 1 Preview:</strong><br>{preview}...</p>"
    )


@hubspot_retry
def patch_contact_properties(client, contact_id: str, props: dict):
    update_input = SimplePublicObjectInput(properties=props)
    return client.crm.contacts.basic_api.update(
        contact_id=contact_id,
        simple_public_object_input=update_input,
    )


def create_note(client, contact_id: str, note_body: str):
    note_input = SimplePublicObjectInputForCreate(
        properties={
            "hs_note_body": note_body,
            "hs_timestamp": str(int(time.time() * 1000)),
        },
        associations=[{
            "to": {"id": contact_id},
            "types": [{
                "associationCategory": "HUBSPOT_DEFINED",
                "associationTypeId": 202,
            }]
        }]
    )
    client.crm.objects.notes.basic_api.create(
        simple_public_object_input_for_create=note_input
    )


def main():
    contact_id    = os.environ["CONTACT_ID"]
    contact_email = os.environ["CONTACT_EMAIL"]
    client        = HubSpot(access_token=os.environ["HUBSPOT_API_KEY"])

    campaign = read_json("campaign_output.json")
    props    = build_properties(campaign)

    # --- CRITICAL PATH: PATCH contact properties ---
    try:
        patch_contact_properties(client, contact_id, props)
        print(f"[write_hubspot] Properties written to contact {contact_id}")
    except ApiException as e:
        write_dlq(contact_id, contact_email, "write_hubspot_patch", str(e))
        raise SystemExit(1)

    # --- NON-FATAL: create note ---
    try:
        note_body = build_note_body(campaign)
        create_note(client, contact_id, note_body)
        print(f"[write_hubspot] Note created for contact {contact_id}")
    except Exception as e:
        print(f"[write_hubspot] WARNING: Note creation failed (non-fatal): {e}", file=sys.stderr)

    print("[write_hubspot] Done.")


if __name__ == "__main__":
    main()
```

### Verifying associationTypeId at Runtime (Pre-Flight Check)

The STATE.md documents a blocker: "Confirm associationTypeId 202 for note-to-contact via GET /crm/v4/associations/notes/contacts/labels". This can be confirmed programmatically:

```python
# One-time verification — run before Phase 5 execution against the real portal
# Response should contain: {"results": [{"category": "HUBSPOT_DEFINED", "typeId": 202, "label": null}]}
response = client.api_request({
    "path": "/crm/v4/associations/notes/contacts/labels",
    "method": "GET"
})
labels = response.json()
type_ids = [r["typeId"] for r in labels.get("results", [])]
assert 202 in type_ids, f"associationTypeId 202 not found in portal. Found: {type_ids}"
```

---

## `campaign_output.json` Schema Contract

Phase 5 depends on Phase 4's output file having a specific structure. The plan must explicitly document this schema and verify it during execution:

```json
{
  "reasoning": "string — reasoning block from Claude",
  "emails": [
    {
      "subject": "string — email subject line",
      "body": "string — full email body"
    }
  ]
}
```

`emails` must be an array of exactly 8 objects. Phase 5 should validate this at the top of `main()` before building properties:

```python
emails = campaign.get("emails", [])
if len(emails) != 8:
    write_dlq(contact_id, contact_email, "write_hubspot_validation",
              f"campaign_output.json has {len(emails)} emails, expected 8")
    raise SystemExit(1)
for i, e in enumerate(emails, 1):
    if "subject" not in e or "body" not in e:
        write_dlq(contact_id, contact_email, "write_hubspot_validation",
                  f"email_{i} missing 'subject' or 'body' key")
        raise SystemExit(1)
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| HubSpot legacy Engagements API v1 (`/engagements/v1/notes`) | CRM Notes v3 (`/crm/v3/objects/notes`) | HubSpot deprecated v1 engagements (sunset extended to Apr 2026) | Use notes via SDK `client.crm.objects.notes.basic_api`; do NOT use v1 |
| Single-property PATCH calls in a loop | Single bulk PATCH with all properties in one call | Always supported in v3 | Fewer API calls, atomic write, simpler code |
| Associating notes post-creation via separate API call | Including `associations` in the `SimplePublicObjectInputForCreate` body | v3 notes API design | Creates note + association in one request; no separate association step needed |

**Deprecated/outdated:**
- `/engagements/v1/notes`: Deprecated; Python SDK no longer exposes it under `crm`. Use `/crm/v3/objects/notes`.
- `HubSpot(api_key=...)`: Legacy API key auth deprecated. Use `HubSpot(access_token=...)` with a Private App token.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `campaign_output.json` uses schema `{"reasoning": "...", "emails": [{"subject": "...", "body": "..."}]}` | Code Examples | Phase 5 `build_properties()` would raise KeyError; pipeline fails before any HubSpot write |
| A2 | associationTypeId 202 is valid for note→contact in the Staff Domain portal specifically | Architecture Patterns | Note created but not linked to contact; appears as orphan note in HubSpot |
| A3 | `crm.objects.notes.write` scope is grantable in the Staff Domain HubSpot portal | Common Pitfalls | Notes always fail with 403; pipeline proceeds but never creates campaign notes |

**A1 risk mitigation:** Phase 4 plan must document and lock the `campaign_output.json` schema. Phase 5 plan should include a validation step that fails fast if the schema doesn't match.

**A2 risk mitigation:** Run the associationTypeId verification query (see Code Examples) against the actual portal before first production execution. STATE.md already documents this as a required pre-Phase 5 check.

**A3 risk mitigation:** Non-fatal pattern in CLAUDE.md already covers this. Test note creation against the portal's Private App token before executing Phase 5 in production.

---

## Open Questions (RESOLVED)

1. **What is the exact `campaign_output.json` schema from Phase 4?** — RESOLVED (flat dict)
   - Resolution: Phase 4 uses a flat dict schema: `{"reasoning": str, "email_1": {"subject": str, "body": str}, ..., "email_8": {"subject": str, "body": str}}`. This matches REQUIREMENTS.md GEN-02 ("email_1 through email_8") and aligns with HubSpot write-back property names (subject_1..subject_8, email_1..email_8). Phase 5 accesses `output["email_1"]["subject"]`, `output["email_1"]["body"]`, etc.

2. **Should the note include previews of all 8 emails or just email 1?** — RESOLVED (email 1 preview only)
   - Resolution: Note body contains reasoning summary + email_1 subject and first 500 chars of email_1 body. Full campaign is readable via contact properties. Note body formatted as HTML with `<br>` line breaks.

3. **Does the note need a `hubspot_owner_id` property?** — RESOLVED (omit)
   - Resolution: Omitted per WRITE-04 spec. HubSpot notes appear on the contact timeline regardless of owner assignment. Add in follow-up if portal-specific visibility issues arise.

---

## Environment Availability

No new external dependencies are introduced in Phase 5. All external services (HubSpot API) were already validated in Phase 2. The `hubspot-api-client==12.0.0` package is already in `requirements.txt`.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| hubspot-api-client | PATCH + Notes API | In requirements.txt | 12.0.0 | — |
| HubSpot Private App token | All writes | In GitHub Secrets | — | — |
| `crm.objects.notes.write` scope | WRITE-04 note creation | Unconfirmed (see STATE.md blocker) | — | Non-fatal fallback already in design |

**Missing dependencies with no fallback:** None that would block execution.

**Missing dependencies with fallback:** `crm.objects.notes.write` scope — if missing, note creation fails with 403 but pipeline continues per CLAUDE.md non-fatal pattern.

---

## Validation Architecture

Phase 5 has no automated test infrastructure defined. The success criteria are integration tests against the live HubSpot portal.

### Test Framework

| Property | Value |
|----------|-------|
| Framework | Manual integration test (no pytest for Phase 5 per ROADMAP.md) |
| Config file | none |
| Quick run command | `python scripts/write_hubspot.py` (with real env vars, test contact) |
| Full suite command | End-to-end pipeline run from Phase 1–5 for a known test contact |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| WRITE-01 | `subject_1`–`subject_8` written to HubSpot contact | integration | Manual: inspect contact in HubSpot UI | N/A |
| WRITE-02 | `email_1`–`email_8` written (up to 65,000 chars) | integration | Manual: inspect contact in HubSpot UI | N/A |
| WRITE-03 | `asotos_generated_date` set to today's date | integration | Manual: inspect contact date property | N/A |
| WRITE-04 | Note appears on timeline; note failure is non-fatal | integration | Manual: check timeline + test with bad scope token | N/A |

### Wave 0 Gaps

The ROADMAP.md specifies a single plan (05-01) for Phase 5 with no test scaffolding requirement. All verification is manual integration testing against a test contact in the HubSpot portal.

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | HubSpot Private App token via `os.environ["HUBSPOT_API_KEY"]` — never hardcoded |
| V3 Session Management | no | Stateless API calls; no session tokens |
| V4 Access Control | yes | hubspot_retry only retries 429/5xx; 403 (scope error) is permanent failure — not retried |
| V5 Input Validation | yes | campaign_output.json schema validated before any write; `html.escape()` applied to note body content |
| V6 Cryptography | no | No cryptographic operations; HTTPS handled by HubSpot SDK |

### Known Threat Patterns for HubSpot Write-Back

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Unvalidated LLM output written to CRM | Tampering | Campaign schema validation before PATCH; Phase 4 Pydantic validation is first gate |
| API token in log output | Information Disclosure | Never log `os.environ`; catch `ApiException` and log only `e.status` + sanitised message, never `str(e)` in full (may include request headers) |
| Partial write state from 400 on type mismatch | Tampering | Single atomic PATCH — 400 before write means no partial state; treat 4xx as hard failure |
| 403 scope error retried as if transient | Denial of Service | Note POST not wrapped in `hubspot_retry`; non-fatal catch prevents retry loops |

---

## Sources

### Primary (HIGH confidence)
- Context7 `/hubspot/hubspot-api-python` — `basic_api.update` for contacts, `SimplePublicObjectInput`, `SimplePublicObjectInputForCreate` for notes, association pattern
- `.planning/research/STACK.md` — verified library versions, HubSpot auth pattern, notes creation with associations (code example with `associationTypeId: 202`)
- `.planning/research/SUMMARY.md` — architecture decisions, note association to contact (202) and company (190), `hs_timestamp` epoch milliseconds as string

### Secondary (MEDIUM confidence)
- HubSpot Community (multiple threads) — `associationTypeId 202` for note→contact confirmed in multiple community examples and verified against `GET /crm/v4/associations/notes/contacts/labels` response `{"typeId": 202}`
- HubSpot Community — `crm.objects.notes.write` scope not visible in Private App UI on some portal tiers; HTTP 403 is the surfaced error
- HubSpot Community — multi-line text property character limit confirmed as 65,536 characters
- HubSpot Community + WebSearch — date properties require midnight UTC epoch milliseconds; YYYY-MM-DD is also accepted but epoch string is more explicit
- HubSpot Community — `associationTypeId 190` for note→company (used in SUMMARY.md architecture but NOT required by WRITE-04)
- HubSpot Community — `hs_note_body` accepts HTML; use `<br>` for line breaks, not `\n`

### Tertiary (LOW confidence — needs first-run validation)
- `associationTypeId 202` specific to Staff Domain portal: [ASSUMED] — all evidence is from community sources; must verify via `GET /crm/v4/associations/notes/contacts/labels` against the actual portal before first production run

---

## Metadata

**Confidence breakdown:**
- Contact PATCH pattern: HIGH — verified via Context7 SDK docs and existing STACK.md research
- Date property format (epoch ms): HIGH — multiple community sources + HubSpot changelog consistent
- Note creation SDK pattern: HIGH — Context7 + community consistently show `SimplePublicObjectInputForCreate` with associations
- `associationTypeId 202` (note→contact): MEDIUM — community-verified + API labels endpoint confirmation shown in search results; ASSUMED for this specific portal
- Notes scope availability: MEDIUM — known issue documented in STATE.md; non-fatal design handles it
- `campaign_output.json` schema: LOW — depends on Phase 4 plan (not yet written); assumption documented

**Research date:** 2026-06-12
**Valid until:** 2026-09-12 (90 days — HubSpot v3 API is stable; notes scope UI issue is a known standing bug)
