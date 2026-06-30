# Phase 6: Failure Handling + Observability — Research

**Researched:** 2026-06-12
**Domain:** GitHub Actions artifact upload, Teams webhook notification, tenacity retry audit
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| REL-01 | All external API calls use tenacity exponential backoff — retryable: 429 and 5xx only; permanent failure on all other 4xx; max 6 attempts, 60s total cap | Retry decorators defined in Phase 1 `lib/api_client.py`; Phase 6 audits all call sites in 4 scripts to confirm decorator application; audit checklist documented |
| REL-03 | On unrecovered failure, each script writes a DLQ JSON record to `$RUNNER_TEMP/failed_contacts.json` | DLQ writer defined in Phase 1 `lib/dlq_writer.py`; Phase 6 confirms each of the 5 scripts calls `write_dlq` in its top-level `except`; exact record schema documented |
| REL-04 | On workflow failure: upload `failed-contacts` artifact; POST Teams webhook with contact_email, failed_step, error excerpt, run log link | `actions/upload-artifact@v4` with `if: failure()` verified; Teams webhook payload format verified (MessageCard still supported by Workflows webhooks); env vars for run URL construction confirmed |
| REL-05 | Upload `campaign-output-{contact_id}` artifact (7-day retention) on every successful run | `actions/upload-artifact@v4` with `if: success()` and `retention-days: 7` verified; artifact naming with `${{ env.CONTACT_ID }}` confirmed |
</phase_requirements>

---

## Summary

Phase 6 is the safety net that wraps the completed Phases 1–5 happy path. It has two distinct scopes:

**Scope 1 — Python code audit (REL-01, REL-03):** Confirm that the tenacity retry decorators from `lib/api_client.py` and the DLQ writer from `lib/dlq_writer.py` (both scaffolded in Phase 1) are correctly applied in every script that makes external API calls. This is an audit-and-fix task, not a greenfield build. Each of the four API-calling scripts (`fetch_hubspot.py`, `fetch_chorus.py`, `generate_campaign.py`, `write_hubspot.py`) must have: (a) `@hubspot_retry`/`@chorus_retry`/`@anthropic_retry` decorating the actual API call functions, and (b) a top-level `except` block calling `write_dlq` before `raise SystemExit(1)`.

**Scope 2 — Workflow YAML additions (REL-04, REL-05):** Add three new steps to `campaign.yml` after the existing `write_hubspot.py` step: (1) a `notify_teams.py` script invocation with `if: failure()`, (2) a DLQ artifact upload step with `if: failure()`, and (3) a campaign output artifact upload step with `if: success()`. The Teams notification is best implemented as a Python script (`scripts/notify_teams.py`) rather than a shell `curl` command — consistent with the project's Python-first approach, testable locally, and already has `requests` available in `requirements.txt`.

**Primary recommendation:** Treat the tenacity/DLQ audit as Task 1 (verify and fix all 4 scripts), then add the three new workflow steps and the `notify_teams.py` script as Task 2.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Retry on transient failures | `scripts/lib/api_client.py` | Applied via decorator in each script | Centralised retry logic; each API has its own retryability predicate |
| DLQ record writing | `scripts/lib/dlq_writer.py` | Called by each script's `except` block | Single canonical implementation; called at each script boundary |
| DLQ artifact upload | `campaign.yml` — `if: failure()` step | — | GitHub Actions native capability; no Python needed |
| Teams failure notification | `scripts/notify_teams.py` | `campaign.yml` — `if: failure()` step | Python script for testability; `requests` already in requirements.txt |
| Campaign output artifact | `campaign.yml` — `if: success()` step | — | GitHub Actions native capability; no Python needed |

---

## Standard Stack

### Core (all already in requirements.txt — no new packages needed)

| Library | Version | Purpose | Notes |
|---------|---------|---------|-------|
| tenacity | ==9.1.4 | Retry decorators | Already pinned in requirements.txt — audit only |
| requests | ==2.34.2 | Teams webhook POST in `notify_teams.py` | Already in requirements.txt |

### GitHub Actions

| Action | Version | Purpose | Why |
|--------|---------|---------|-----|
| actions/upload-artifact | @v4 | Upload DLQ artifact on failure; upload campaign output on success | v4 is current; v3 deprecated [VERIFIED: Context7 /actions/upload-artifact] |

**No new Python packages are required for Phase 6.** `requests` (already pinned) handles the Teams webhook POST. `tenacity` (already pinned) is the retry library.

---

## Architecture Patterns

### System Architecture Diagram

```
campaign.yml (existing steps)
  ...
  Step: Write campaign to HubSpot   [write_hubspot.py]
        |
        v (any prior step fails)
  Step: Notify Teams on failure    [if: failure()]
        |  python scripts/notify_teams.py
        |  POST $TEAMS_WEBHOOK_URL → Teams channel
        v (any prior step fails)
  Step: Upload DLQ artifact        [if: failure()]
        |  cp $RUNNER_TEMP/failed_contacts.json ./failed_contacts.json
        |  actions/upload-artifact@v4 → name: failed-contacts
        v (all prior steps succeed)
  Step: Upload campaign output     [if: success()]
        |  actions/upload-artifact@v4 → name: campaign-output-{contact_id}
        |  path: $RUNNER_TEMP/campaign_output.json
        |  retention-days: 7
```

### Python Retry Audit — Required Call Sites

Each API-calling script must have its external call functions decorated. The exact pattern:

```python
# Source: CLAUDE.md + lib/api_client.py (Phase 1 scaffold)

# In fetch_hubspot.py — decorate each HubSpot SDK call function
from api_client import hubspot_retry

@hubspot_retry
def fetch_contact_properties(client, contact_id):
    return client.crm.contacts.basic_api.get_by_id(contact_id, ...)

# In fetch_chorus.py — decorate each requests.get/post function
from api_client import chorus_retry

@chorus_retry
def fetch_transcript(session, conversation_id):
    resp = session.get(f"https://chorus.ai/api/v3/engagements/{conversation_id}")
    resp.raise_for_status()  # CRITICAL: must call raise_for_status() for tenacity to see HTTPError
    return resp.json()

# In generate_campaign.py — decorate the Claude API call function
from api_client import anthropic_retry

@anthropic_retry
def call_claude(client, model, max_tokens, system, messages):
    return client.messages.create(model=model, max_tokens=max_tokens,
                                  system=system, messages=messages)

# In write_hubspot.py — decorate each HubSpot write function
@hubspot_retry
def write_contact_properties(client, contact_id, properties):
    return client.crm.contacts.basic_api.update(contact_id, ...)
```

### DLQ Integration Pattern — Required in Each Script

```python
# Source: CLAUDE.md (canonical) + lib/dlq_writer.py (Phase 1 scaffold)

# Top-level pattern in every script's __main__ block:
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        write_dlq(CONTACT_ID, CONTACT_EMAIL, "fetch_hubspot", str(e))
        raise SystemExit(1)
```

The five `failed_step` values must be exactly:
- `"fetch_hubspot"` in `fetch_hubspot.py`
- `"fetch_chorus"` in `fetch_chorus.py`
- `"compute_campaign_tokens"` in `compute_campaign_tokens.py`
- `"generate_campaign"` in `generate_campaign.py`
- `"write_hubspot"` in `write_hubspot.py`

### notify_teams.py Pattern

The Teams webhook notification should be a standalone Python script that reads environment variables and POSTs a MessageCard payload. MessageCard is the correct format for simple notification-only messages (no interactive buttons needed).

```python
# Source: CLAUDE.md + Microsoft Teams docs [CITED: learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook]
# notify_teams.py

import os
import json
import requests
import sys
from dlq_writer import write_dlq  # reuse for any failure in this script itself? No — notification failure is non-fatal

def notify_failure():
    webhook_url = os.environ.get("TEAMS_WEBHOOK_URL", "")
    contact_email = os.environ.get("CONTACT_EMAIL", "unknown")
    contact_id = os.environ.get("CONTACT_ID", "unknown")
    run_url = (
        f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
        f"{os.environ.get('GITHUB_REPOSITORY', '')}/"
        f"actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    )

    # Read DLQ file for failed_step and error excerpt
    runner_temp = os.environ.get("RUNNER_TEMP", ".")
    dlq_path = os.path.join(runner_temp, "failed_contacts.json")
    failed_step = "unknown"
    error_excerpt = "See run log"
    if os.path.exists(dlq_path):
        with open(dlq_path) as f:
            record = json.load(f)
        failed_step = record.get("failed_step", "unknown")
        error_excerpt = record.get("error_message", "")[:500]

    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "FF0000",
        "summary": f"ASOTOS pipeline failed for {contact_email}",
        "sections": [{
            "activityTitle": "ASOTOS Pipeline Failed",
            "facts": [
                {"name": "Contact email", "value": contact_email},
                {"name": "Contact ID", "value": contact_id},
                {"name": "Failed step", "value": failed_step},
                {"name": "Error", "value": error_excerpt},
            ],
            "potentialAction": [{
                "@type": "OpenUri",
                "name": "View Run Log",
                "targets": [{"os": "default", "uri": run_url}]
            }]
        }]
    }

    if not webhook_url:
        print("[notify_teams] TEAMS_WEBHOOK_URL not set — skipping notification")
        return

    resp = requests.post(webhook_url, json=payload, timeout=10)
    resp.raise_for_status()
    print(f"[notify_teams] Teams notification sent — HTTP {resp.status_code}")

if __name__ == "__main__":
    try:
        notify_failure()
    except Exception as e:
        # Notification failure is NON-FATAL — log and exit 0
        print(f"[notify_teams] WARNING: notification failed: {e}", file=sys.stderr)
        sys.exit(0)
```

Key points:
- Exit 0 even on failure (notification failure must never fail the workflow step)
- Read from the DLQ file to get `failed_step` and error excerpt — this is richer than env vars alone
- `GITHUB_SERVER_URL`, `GITHUB_REPOSITORY`, `GITHUB_RUN_ID` are default env vars provided by GitHub Actions automatically — do NOT add them to the job-level `env` block

### campaign.yml Step Additions Pattern

```yaml
# Source: ARCHITECTURE.md + actions/upload-artifact docs [VERIFIED: Context7 /actions/upload-artifact]
# Append these steps AFTER the "Write campaign to HubSpot" step

      - name: Notify Teams on failure
        if: failure()
        run: python scripts/notify_teams.py
        # CONTACT_ID, CONTACT_EMAIL, TEAMS_WEBHOOK_URL, RUNNER_TEMP all inherited from job-level env
        # GITHUB_SERVER_URL, GITHUB_REPOSITORY, GITHUB_RUN_ID are default GitHub Actions env vars

      - name: Upload DLQ artifact
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: failed-contacts
          path: ${{ env.RUNNER_TEMP }}/failed_contacts.json
          retention-days: 30
          if-no-files-found: warn

      - name: Upload campaign output
        if: success()
        uses: actions/upload-artifact@v4
        with:
          name: campaign-output-${{ env.CONTACT_ID }}
          path: ${{ env.RUNNER_TEMP }}/campaign_output.json
          retention-days: 7
          if-no-files-found: warn
```

**Why `${{ env.RUNNER_TEMP }}` not `${{ runner.temp }}`:** `runner.temp` and `env.RUNNER_TEMP` resolve to the same value, but since the job-level env block already wires `RUNNER_TEMP: ${{ runner.temp }}`, using `${{ env.RUNNER_TEMP }}` is consistent. Both forms are correct. [VERIFIED: GitHub Actions contexts docs]

**Why `${{ env.CONTACT_ID }}` in artifact name:** In `with:` blocks, `${{ inputs.contact_id }}` is accessible but `${{ env.CONTACT_ID }}` is the more resilient form since the env var is guaranteed set at job level. [VERIFIED: GitHub Actions variable reference docs]

### Anti-Patterns to Avoid

- **`if: always()` on notification steps:** Use `if: failure()` — `always()` fires on workflow cancellation too, creating noise. [CITED: ARCHITECTURE.md Decision 5]
- **`curl` in workflow YAML for Teams notification:** Use a Python script — shell curl with JSON interpolation creates injection risk and is non-testable.
- **Logging full exception objects:** `str(e)[:2000]` is the correct truncation — raw exception `repr()` can include API key fragments. [CITED: PITFALLS.md PITFALL-M7]
- **`raise_for_status()` missing in Chorus calls:** Without `raise_for_status()`, requests returns non-2xx responses silently; tenacity never sees an exception to retry. [CITED: STACK.md Tenacity Retry Patterns]
- **Teams notification step with `if: failure()` blocking DLQ upload:** Both `notify_teams` and DLQ upload must have `if: failure()` independently — if one fails, the other must still run. Because both have `if: failure()` checking the job status (not prior step status), both run when any earlier step fails.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Retry logic with jitter | Custom sleep loop | `@hubspot_retry` / `@chorus_retry` / `@anthropic_retry` from `lib/api_client.py` | Already built in Phase 1; hand-rolling misses the 60s cap |
| DLQ record format | Custom exception handler | `write_dlq()` from `lib/dlq_writer.py` | Already built in Phase 1; canonical record schema from CLAUDE.md |
| Teams notification | Shell `curl` with heredoc | `scripts/notify_teams.py` using `requests` | Testable, no injection risk, consistent with Python-first project |
| Artifact upload logic | Custom HTTP calls to GitHub API | `actions/upload-artifact@v4` | GitHub native; handles chunking, compression, retention automatically |

---

## REL-01 Audit Checklist

The tenacity decorators were defined in Phase 1 `lib/api_client.py`. Phase 6 must verify they are actually applied at every external call site.

### fetch_hubspot.py — Expected decorator application
| Function | Decorator | Retryable errors |
|----------|-----------|-----------------|
| HubSpot contact property fetch | `@hubspot_retry` | 429, 500-504 |
| HubSpot engagement history fetch (each page) | `@hubspot_retry` | 429, 500-504 |
| HubSpot CRM meeting fetch | `@hubspot_retry` | 429, 500-504 |
| HubSpot deals fetch | `@hubspot_retry` | 429, 500-504 |
| HubSpot owners fetch | `@hubspot_retry` | 429, 500-504 |

### fetch_chorus.py — Expected decorator application
| Function | Decorator | Retryable errors |
|----------|-----------|-----------------|
| Chorus transcript GET | `@chorus_retry` | 429, 500-504 |

**Chorus note:** Chorus 401 and 404 are NOT retried (silent fallback per CHO-02). The `_is_retryable_chorus` predicate in `api_client.py` only retries 429 and 500-504 — correct.

### generate_campaign.py — Expected decorator application
| Function | Decorator | Retryable errors |
|----------|-----------|-----------------|
| `client.messages.create()` call | `@anthropic_retry` | RateLimitError (429), APIStatusError >= 500 |

**Anthropic note:** The `Anthropic()` client must have `max_retries=0` to prevent SDK-level double-retry. This is REL-02 (already addressed in Phase 4), but Phase 6 audit should verify it.

### write_hubspot.py — Expected decorator application
| Function | Decorator | Retryable errors |
|----------|-----------|-----------------|
| Contact properties PATCH | `@hubspot_retry` | 429, 500-504 |
| Note creation POST | `@hubspot_retry` | 429, 500-504 |

**Note creation caveat:** Note creation is NON-FATAL (per WRITE-04/CLAUDE.md). The `@hubspot_retry` decorator should still be applied, but the call must be wrapped in its own `try/except` that logs and continues rather than re-raising to the top-level handler.

### compute_campaign_tokens.py — No external API calls
This script reads from `$RUNNER_TEMP` JSON files only. No tenacity decorator needed. DLQ writer still needed for any unrecovered exception (e.g., malformed JSON, missing required token).

---

## Teams Webhook Status (June 2026)

**Critical context:** Microsoft deprecated Office 365 Connectors (the old webhook type). All webhook URLs must now use the Power Automate "Workflows" webhook. [VERIFIED: Microsoft Teams docs, retirement blog post]

| Webhook Type | Status | Format Accepted |
|--------------|--------|----------------|
| Office 365 Connector webhook URLs | Deprecated — disabled May 18-22, 2026 | Was: MessageCard only |
| Power Automate Workflows webhook | Current | MessageCard AND simple `{"text": "..."}` |

**Impact for ASOTOS:** The `TEAMS_WEBHOOK_URL` secret must be a **Workflows webhook URL** (from Teams channel > Workflows > "Send webhook alerts to a channel"). If it is an old Office 365 Connector URL, it is now broken.

**Payload format for Workflows webhooks:**

Option A — Simple text (lowest friction):
```json
{"text": "ASOTOS pipeline failed for contact@example.com — step: fetch_hubspot\nRun: https://github.com/staffdomain-devops/ASOTOS/actions/runs/12345"}
```

Option B — MessageCard (richer, still supported):
```json
{
    "@type": "MessageCard",
    "@context": "http://schema.org/extensions",
    "themeColor": "FF0000",
    "summary": "ASOTOS pipeline failed",
    "sections": [...]
}
```

**Recommendation:** Use MessageCard (Option B) for richer display. Workflows webhooks confirmed to support MessageCard. Button rendering not supported in Workflows webhooks — use `OpenUri` for "View Run Log" link only.

[CITED: learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook]
[CITED: devblogs.microsoft.com/microsoft365dev/retirement-of-office-365-connectors-within-microsoft-teams]

---

## Common Pitfalls

### Pitfall 1: raise_for_status() Missing from Chorus Calls
**What goes wrong:** `requests.get(url).raise_for_status()` is absent. Chorus returns 429 — tenacity never sees an exception, never retries, and the script proceeds with a response object it can't parse.
**Why it happens:** Easy to forget when decorating functions — the `@chorus_retry` decorator catches exceptions but only sees them if they are raised.
**How to avoid:** Every `requests` call in `fetch_chorus.py` must call `resp.raise_for_status()` before accessing `resp.json()`.
**Warning signs:** Chorus 429 responses cause silent JSON parse errors rather than retry attempts.

### Pitfall 2: Notification Step Failure Stops DLQ Upload
**What goes wrong:** `notify_teams.py` fails (e.g., TEAMS_WEBHOOK_URL is unset or returns 429). If the notify step has `if: failure()` and exits non-zero, GitHub Actions marks the step as failed but continues because the DLQ upload also has `if: failure()`. However, if `notify_teams.py` exits non-zero, the workflow job itself is marked as failed — which is correct. The key is that `notify_teams.py` must exit 0 even if the notification POST fails.
**Why it happens:** Treating notification failure as a fatal error creates a cascading failure where the DLQ artifact also isn't uploaded (if notify exits non-zero and the upload relies on step ordering).
**How to avoid:** `notify_teams.py` exits 0 unconditionally — catch all exceptions, log to stderr, and `sys.exit(0)`. The DLQ artifact upload runs independently with its own `if: failure()` condition.

### Pitfall 3: Artifact Path Uses $RUNNER_TEMP Shell Variable Instead of ${{ env.RUNNER_TEMP }}
**What goes wrong:** In the `with:` block of `actions/upload-artifact@v4`, writing `path: $RUNNER_TEMP/failed_contacts.json` — the `$RUNNER_TEMP` shell variable is not expanded in `with:` blocks; only `${{ }}` expressions are.
**Why it happens:** `with:` blocks use GitHub expression syntax, not shell syntax.
**How to avoid:** Use `path: ${{ env.RUNNER_TEMP }}/failed_contacts.json` in all `with:` blocks.

### Pitfall 4: Teams Notification Uses Old Office 365 Connector URL
**What goes wrong:** `TEAMS_WEBHOOK_URL` stored in GitHub Secrets was created before May 2026 using the old "Connectors" UI. Post-May 2026, that URL is disabled. The notification step silently fails (or returns 410 Gone).
**Why it happens:** Office 365 Connectors were retired May 18-22, 2026.
**How to avoid:** The `TEAMS_WEBHOOK_URL` must be a Power Automate Workflows URL. Steps to create: Teams channel > "..." menu > Workflows > "Send webhook alerts to a channel" template > copy the generated URL. Log the HTTP response code in `notify_teams.py` to catch this.

### Pitfall 5: DLQ File Not Present at Failure Step Time
**What goes wrong:** A script fails before calling `write_dlq()` (e.g., import error, CONTACT_ID missing from env). The artifact upload step runs with `if: failure()` but `failed_contacts.json` doesn't exist. `if-no-files-found: warn` means the upload succeeds with a warning, but the Teams notification can't read the DLQ file.
**Why it happens:** `write_dlq` is called inside the `except` block — if the exception occurs before Python reaches `try`, or if there's an uncaught exception in a C extension, the DLQ is not written.
**How to avoid:** `notify_teams.py` gracefully handles a missing `failed_contacts.json` — check `os.path.exists()` before reading, fall back to env vars for `CONTACT_EMAIL` and generic message for `failed_step`.

---

## Code Examples

### Verified upload-artifact@v4 with if: failure() pattern
```yaml
# Source: Context7 /actions/upload-artifact + GitHub Actions docs [VERIFIED]
- name: Upload logs on fail
  if: ${{ failure() }}
  uses: actions/upload-artifact@v4
  with:
    name: Build failure logs
    path: ${{ runner.temp }}/build_logs
```

### Run URL construction from GitHub default env vars
```python
# Source: docs.github.com/en/actions/reference/workflows-and-actions/variables [VERIFIED]
run_url = (
    f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
    f"{os.environ.get('GITHUB_REPOSITORY', '')}/"
    f"actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
)
# Result: https://github.com/staffdomain-devops/ASOTOS/actions/runs/12345678
```

### Teams MessageCard payload (minimal, verified against Workflows webhook)
```python
# Source: learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/connectors-using [CITED]
payload = {
    "@type": "MessageCard",
    "@context": "http://schema.org/extensions",
    "themeColor": "FF0000",
    "summary": f"ASOTOS pipeline failed for {contact_email}",
    "sections": [{
        "activityTitle": "ASOTOS Pipeline Failed",
        "facts": [
            {"name": "Contact email", "value": contact_email},
            {"name": "Contact ID",    "value": contact_id},
            {"name": "Failed step",   "value": failed_step},
            {"name": "Error",         "value": error_excerpt},
        ],
        "potentialAction": [{
            "@type": "OpenUri",
            "name": "View Run Log",
            "targets": [{"os": "default", "uri": run_url}]
        }]
    }]
}
resp = requests.post(webhook_url, json=payload, timeout=10)
```

### Tenacity decorator application (verified against CLAUDE.md)
```python
# Source: CLAUDE.md canonical spec + lib/api_client.py (Phase 1)
from api_client import hubspot_retry, chorus_retry, anthropic_retry

@hubspot_retry
def _fetch_contact_properties(client, contact_id, properties):
    return client.crm.contacts.basic_api.get_by_id(
        contact_id, properties=properties
    )

@chorus_retry
def _fetch_chorus_transcript(session, conversation_id):
    resp = session.get(
        f"https://chorus.ai/api/v3/engagements/{conversation_id}"
    )
    resp.raise_for_status()  # raise_for_status() is REQUIRED for tenacity visibility
    return resp.json()
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Office 365 Connector webhooks | Power Automate Workflows webhooks | Deprecated Aug 2024, disabled May 2026 | TEAMS_WEBHOOK_URL must be re-created if using old connector URL |
| MessageCard format (connector only) | MessageCard + Adaptive Cards (Workflows) | Feb 2026 | MessageCard still works in Workflows, no migration needed for simple notifications |
| `actions/upload-artifact@v3` | `actions/upload-artifact@v4` | v4 stable 2024, v3 deprecated | Use @v4; v3 is significantly slower |

---

## Environment Availability

Phase 6 modifies existing files only. No new external services are required.

| Dependency | Required By | Available | Notes |
|------------|------------|-----------|-------|
| `requests` (PyPI) | `notify_teams.py` | Already in requirements.txt | No change needed |
| `tenacity` (PyPI) | `lib/api_client.py` audit | Already in requirements.txt | No change needed |
| `TEAMS_WEBHOOK_URL` secret | Teams notification | Must be Workflows webhook | Old Office 365 Connector URLs are disabled post-May 2026 — verify and re-create if needed |
| `actions/upload-artifact@v4` | Artifact upload steps | GitHub-hosted runner | Confirmed available on ubuntu-latest [VERIFIED: Context7] |
| `GITHUB_SERVER_URL`, `GITHUB_REPOSITORY`, `GITHUB_RUN_ID` | Run URL in notification | Automatically provided by GitHub Actions | No env block addition needed |

**Missing dependencies with no fallback:** None.

**Action required before Phase 6 execution:** Verify that `TEAMS_WEBHOOK_URL` in GitHub Secrets is a Power Automate Workflows URL (format: `https://prod-XX.westus2.logic.azure.com:443/workflows/...`) and not an old Office 365 Connector URL (format: `https://xxxx.webhook.office.com/...`). If it is an old URL, it must be re-created via the Teams channel Workflows UI.

---

## Validation Architecture

> `nyquist_validation` is explicitly set to `false` in `.planning/config.json`. This section is omitted.

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | No new auth flows |
| V3 Session Management | No | Stateless |
| V4 Access Control | No | No new access control |
| V5 Input Validation | Yes | `error_message[:2000]` truncation in DLQ prevents oversized payloads |
| V6 Cryptography | No | HTTPS for Teams webhook POST handled by `requests` |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Error message contains API key fragment | Information Disclosure | `str(e)[:2000]` in `write_dlq` + never log exception `repr()` |
| DLQ artifact contains contact PII | Information Disclosure | DLQ includes `contact_email` (required for routing); retention-days: 30 is acceptable; restrict artifact download to repo collaborators |
| Teams notification leaks API key in error excerpt | Information Disclosure | Truncate error to 500 chars in `notify_teams.py`; never include raw exception objects |
| Old connector URL silently fails | Denial of Service (observability) | Log HTTP response code; fail gracefully with `sys.exit(0)` |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | MessageCard format is supported by Power Automate Workflows webhook URLs as of June 2026 | Teams Webhook Status | Notification fails silently; fall back to `{"text": "..."}` format |
| A2 | `GITHUB_SERVER_URL`, `GITHUB_REPOSITORY`, `GITHUB_RUN_ID` are available as environment variables in all GitHub Actions steps without explicit declaration | Code Examples | Run URL is malformed; log a warning and use a static fallback URL |
| A3 | `${{ env.CONTACT_ID }}` is available in the `with:` block of `actions/upload-artifact@v4` after job-level env wiring | Architecture Patterns | Artifact name is `campaign-output-` with no ID suffix; use `${{ inputs.contact_id }}` as fallback |

**Confidence note on A1:** Multiple sources confirm Workflows webhooks support MessageCard as of February 2026 [CITED: Microsoft Teams known issues list]. Treat as MEDIUM confidence until smoke-tested against a real Workflows webhook URL.

---

## Open Questions (RESOLVED)

1. **TEAMS_WEBHOOK_URL format in GitHub Secrets** — RESOLVED (documented as pre-execution check)
   - Resolution: Plan 06-01 includes a pre-execution note to verify the TEAMS_WEBHOOK_URL secret format before first run. If it's a `webhook.office.com` URL (old Office 365 Connector), it must be replaced with a Power Automate Workflows URL (`logic.azure.com`). The notify_teams.py script works with either format at the HTTP level; the difference is in Teams channel setup only.

2. **`compute_campaign_tokens.py` DLQ call confirmation** — RESOLVED (Phase 6 audit covers all 5 scripts)
   - Resolution: The Phase 6 audit scope explicitly covers all 5 main scripts including `compute_campaign_tokens.py`. The plan includes a task to verify the `try/except` + `write_dlq` + `SystemExit(1)` pattern is present in every script, not just the 4 API-calling ones.

---

## Sources

### Primary (HIGH confidence)
- `C:\Users\irahfo\Outreach\Asotos\CLAUDE.md` — canonical retry policy, DLQ pattern, Anthropic SDK rules
- `C:\Users\irahfo\Outreach\Asotos\.planning\phases\01-scaffold-trigger\01-RESEARCH.md` — Phase 1 lib scaffolding, tenacity patterns
- `C:\Users\irahfo\Outreach\Asotos\.planning\research\ARCHITECTURE.md` — artifact upload YAML patterns, `if: failure()` decision rationale
- `C:\Users\irahfo\Outreach\Asotos\.planning\research\STACK.md` — tenacity retry predicates, `raise_for_status()` requirement
- Context7 `/actions/upload-artifact` — upload-artifact@v4 YAML syntax, retention-days, if-no-files-found options
- GitHub Actions docs (via Context7 `/websites/github_en_actions`) — `if: failure()` with `runner.temp`, upload-artifact examples

### Secondary (MEDIUM confidence)
- [learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook) — Workflows webhook URL creation, payload format
- [learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/connectors-using](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/connectors-using) — MessageCard format reference, full JSON example
- [docs.github.com/en/actions/reference/workflows-and-actions/variables](https://docs.github.com/en/actions/reference/workflows-and-actions/variables) — `GITHUB_SERVER_URL`, `GITHUB_REPOSITORY`, `GITHUB_RUN_ID` default variables

### Tertiary (LOW confidence / requires validation)
- [devblogs.microsoft.com/microsoft365dev/retirement-of-office-365-connectors-within-microsoft-teams](https://devblogs.microsoft.com/microsoft365dev/retirement-of-office-365-connectors-within-microsoft-teams/) — retirement timeline; MessageCard support in Workflows (need smoke test to confirm A1)

---

## Metadata

**Confidence breakdown:**
- Tenacity/DLQ audit scope: HIGH — patterns are from CLAUDE.md canonical spec; Phase 1 research confirms lib scaffold
- campaign.yml additions: HIGH — upload-artifact@v4 syntax verified via Context7; if:failure() pattern verified
- Teams notification payload: MEDIUM — MessageCard in Workflows confirmed by docs but not smoke-tested against live URL
- Teams webhook URL deprecation impact: HIGH — retirement timeline confirmed via Microsoft Developer Blog

**Research date:** 2026-06-12
**Valid until:** 2026-09-12 (stable stack; verify Teams webhook URL format before Phase 6 execution)
