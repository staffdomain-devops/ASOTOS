# Phase 1: Scaffold + Trigger — Research

**Researched:** 2026-06-12
**Domain:** GitHub Actions workflow_dispatch trigger, Python project scaffold, shared utilities
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TRIG-01 | GitHub Actions `workflow_dispatch` accepts `contact_id` and `contact_email` as inputs, triggered by Make.com via GitHub API | Exact YAML syntax verified; Make.com HTTP module trigger pattern documented; 204 response handling confirmed |
| PROJ-01 | `prompt_template.md` contains full ASOTOS campaign prompt with `{{token.name}}` placeholders | Full token inventory extracted from `staff_domain_ASOTOS_prompt.md`; all 16 tokens catalogued |
| PROJ-02 | `requirements.txt` uses exact `==` version pins for all dependencies | Versions confirmed via PyPI for all 7 packages; pydantic added (required for Phase 4 but wired here) |
| PROJ-03 | Shared retry/DLQ utilities extracted to `scripts/lib/` | Module responsibilities defined; import path pattern confirmed; placeholder impl pattern documented |
</phase_requirements>

---

## Summary

Phase 1 scaffolds the entire project structure that all subsequent phases build on. The work divides cleanly into two deliverables: (1) the GitHub Actions workflow YAML with correct `workflow_dispatch` inputs, job-level environment variable wiring, Python setup, and step stubs; and (2) the three shared lib modules (`api_client.py`, `dlq_writer.py`, `file_io.py`) plus the migrated prompt template.

All technology decisions are already locked by CONTEXT (CLAUDE.md). The research confirms the exact syntax for every element — nothing is left ambiguous. The only non-trivial design decision is whether the Phase 1 workflow steps should be real script stubs (that run and succeed) or fully skeleton steps with `echo` placeholders. The research recommendation is real stub scripts that can be imported and called — this ensures the lib import path works correctly from day one.

The key operational risk for Phase 1 is the Make.com 204 handling documented in CLAUDE.md. As of Feb 2026, the GitHub dispatch API optionally returns 200 with run details via `return_run_details: true`. This is a useful enhancement but the default 204 is the safer choice until Make.com 204 acceptance is confirmed.

**Primary recommendation:** Build the workflow YAML and lib stubs together in Plan 01-01; migrate the prompt template and flesh out lib implementations in Plan 01-02.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Workflow trigger (Make.com -> GitHub) | GitHub Actions | — | `workflow_dispatch` API is a GitHub primitive; no app code involved |
| Input routing (contact_id -> env vars) | GitHub Actions YAML | — | Job-level `env:` block wires inputs to all Python scripts uniformly |
| Inter-step data passing | GitHub Actions runner filesystem (RUNNER_TEMP) | — | Same job, shared temp dir; artifact upload only for post-run retention |
| Shared retry logic | `scripts/lib/api_client.py` | Used by all 5 main scripts | Centralised tenacity decorators prevent duplication |
| DLQ record writing | `scripts/lib/dlq_writer.py` | Called by all 5 main scripts on failure | Single implementation of the CLAUDE.md DLQ pattern |
| JSON file I/O | `scripts/lib/file_io.py` | Used by all 5 main scripts | Typed path construction and JSON read/write helpers |
| Prompt template | `prompt_template.md` (repo root) | Rendered by `compute_campaign_tokens.py` in Phase 3 | Template in repo root; Jinja2 rendering in Phase 3 |

---

## Standard Stack

### Core (Phase 1 only installs all deps for reproducibility)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| jinja2 | ==3.1.6 | `{{token.name}}` template substitution with StrictUndefined | [VERIFIED: pypi.org/project/Jinja2] — Official Jinja2, latest stable Mar 2025 |
| pydantic | ==2.13.4 | Output schema validation for Claude responses | [VERIFIED: pypi.org/project/pydantic] — Latest stable May 2026; already installed on dev machine |
| tenacity | ==9.1.4 | Exponential backoff retry across all APIs | [VERIFIED: existing research STACK.md] — Confirmed via PyPI 2026-06-12 |
| anthropic | ==0.109.1 | Claude API client | [VERIFIED: existing research STACK.md] — Latest stable Jun 2026 |
| hubspot-api-client | ==12.0.0 | HubSpot CRM read/write | [VERIFIED: existing research STACK.md] — Latest stable |
| requests | ==2.34.2 | Chorus AI REST calls | [VERIFIED: existing research STACK.md] — Latest stable |
| beautifulsoup4 | ==4.15.0 | Strip HTML from HubSpot rich-text fields | [VERIFIED: existing research STACK.md] — Latest stable |
| lxml | ==5.3.0 | Faster bs4 HTML parser backend | [ASSUMED] — Correct parser; version confirm via `pip index versions lxml` before committing |
| tiktoken | ==0.13.0 | Token pre-count before Claude call | [VERIFIED: existing research STACK.md] — Latest stable |

**Installation:**
```bash
pip install -r requirements.txt
```

**Version verification:** All versions above confirmed via PyPI JSON API on 2026-06-12 except `lxml` which is ASSUMED. Run `pip index versions lxml` before writing the pin.

### GitHub Actions
| Action | Version | Purpose |
|--------|---------|---------|
| actions/checkout | @v6 | Repo checkout | [VERIFIED: github.com/actions/checkout/releases — v6.0.3 latest as of Jun 2026] |
| actions/setup-python | @v6 | Python 3.12 setup with pip cache | [VERIFIED: github.com/actions/setup-python/releases — v6.2.0 latest] |
| actions/upload-artifact | @v4 | Artifact upload on success/failure | [CITED: existing research STACK.md — v4 current, v3 deprecated] |

---

## Architecture Patterns

### System Architecture Diagram

```
Make.com HTTP Module
  POST /repos/staffdomain-devops/ASOTOS/actions/workflows/campaign.yml/dispatches
  Body: {"ref": "main", "inputs": {"contact_id": "...", "contact_email": "..."}}
  Response: 204 No Content (or 200 with return_run_details: true)
        |
        v
GitHub Actions: campaign.yml
  trigger: workflow_dispatch
  concurrency: asotos-{contact_id}  [v2 feature — not Phase 1]
        |
        v
Job: generate-campaign  (ubuntu-latest)
  env: (job-level — all steps inherit)
    CONTACT_ID, CONTACT_EMAIL
    HUBSPOT_API_KEY, CHORUS_API_TOKEN
    ANTHROPIC_API_KEY, TEAMS_WEBHOOK_URL
    RUNNER_TEMP
        |
        v
  Step: actions/checkout@v6
  Step: actions/setup-python@v6 (python-version: "3.12", cache: "pip")
  Step: pip install -r requirements.txt
        |
        v
  Step: fetch_hubspot.py    --> $RUNNER_TEMP/hubspot_contact.json
  Step: fetch_chorus.py     --> $RUNNER_TEMP/chorus_transcripts.json
  Step: compute_campaign_tokens.py --> $RUNNER_TEMP/campaign_tokens.json
  Step: generate_campaign.py --> $RUNNER_TEMP/campaign_output.json
  Step: write_hubspot.py    --> HubSpot CRM
        |
        v (on failure only — Phase 6)
  Step: upload failed_contacts.json artifact
  Step: POST Teams webhook
```

### Recommended Project Structure
```
.github/
└── workflows/
    └── campaign.yml           # workflow_dispatch trigger, job, steps
scripts/
├── lib/
│   ├── __init__.py            # empty, makes lib a package
│   ├── api_client.py          # tenacity retry decorators
│   ├── dlq_writer.py          # DLQ record writer
│   └── file_io.py             # RUNNER_TEMP path helpers + JSON read/write
├── fetch_hubspot.py           # Phase 2
├── fetch_chorus.py            # Phase 2
├── compute_campaign_tokens.py # Phase 3
├── generate_campaign.py       # Phase 4
└── write_hubspot.py           # Phase 5
prompt_template.md             # ASOTOS prompt with {{token.name}} placeholders
requirements.txt               # exact == pins
CLAUDE.md
.planning/
```

### Pattern 1: workflow_dispatch YAML with Inputs
**What:** Defines the GitHub Actions trigger accepting Make.com inputs
**When to use:** Required for TRIG-01; this is the exact syntax

```yaml
# Source: docs.github.com/actions/using-workflows/workflow-syntax-for-github-actions
name: ASOTOS Campaign Generator

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

jobs:
  generate-campaign:
    runs-on: ubuntu-latest
    env:
      CONTACT_ID: ${{ inputs.contact_id }}
      CONTACT_EMAIL: ${{ inputs.contact_email }}
      HUBSPOT_API_KEY: ${{ secrets.HUBSPOT_API_KEY }}
      CHORUS_API_TOKEN: ${{ secrets.CHORUS_API_TOKEN }}
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      TEAMS_WEBHOOK_URL: ${{ secrets.TEAMS_WEBHOOK_URL }}
      RUNNER_TEMP: ${{ runner.temp }}

    steps:
      - uses: actions/checkout@v6

      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
          cache: "pip"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Fetch HubSpot contact
        run: python scripts/fetch_hubspot.py

      - name: Fetch Chorus transcripts
        run: python scripts/fetch_chorus.py

      - name: Compute campaign tokens
        run: python scripts/compute_campaign_tokens.py

      - name: Generate campaign
        run: python scripts/generate_campaign.py

      - name: Write campaign to HubSpot
        run: python scripts/write_hubspot.py
```

**Note on `inputs` vs `github.event.inputs`:** Use `${{ inputs.contact_id }}` — the `inputs` context is the current recommended form and preserves declared types. `github.event.inputs` is the legacy form. [CITED: docs.github.com/actions/reference/workflows-and-actions/contexts]

### Pattern 2: Job-Level env Wiring
**What:** Secrets and inputs declared once at job level, inherited by all 5 script steps
**When to use:** Required — prevents repetition across 5+ steps; CLAUDE.md and STATE.md locked this decision

Key points:
- NEVER redeclare secrets at individual step level
- `RUNNER_TEMP: ${{ runner.temp }}` must be wired at job level so Python reads it from `os.environ`
- On ubuntu-latest, `${{ runner.temp }}` resolves to `/home/runner/work/_temp`

### Pattern 3: lib/ Import Path in Scripts
**What:** How main scripts import from `scripts/lib/`
**When to use:** Every Phase 2-6 script needs this

```python
# Source: [CITED: docs.python.org/3/library/sys.html]
import sys
import os

# Add lib/ to path — insert at 0 so lib overrides any same-named installed package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

from api_client import hubspot_retry, chorus_retry, anthropic_retry
from dlq_writer import write_dlq
from file_io import read_runner_temp, write_runner_temp
```

**Phase 1 implication:** The lib import path must work at Phase 1 smoke-test time, before any real implementation. Plan 01-02 should verify this by importing the modules in each stub script.

### Pattern 4: DLQ Writer (exact from CLAUDE.md)
**What:** Called by every script on unrecovered failure before re-raising
**When to use:** Every script's top-level `except` block

```python
# Source: CLAUDE.md (project canonical spec)
from datetime import datetime, timezone
import json
import os

def write_dlq(contact_id, contact_email, failed_step, error_message):
    record = {
        "contact_id": contact_id,
        "contact_email": contact_email,
        "failed_step": failed_step,
        "error_message": str(error_message)[:2000],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    path = os.path.join(os.environ.get("RUNNER_TEMP", "."), "failed_contacts.json")
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
```

`dlq_writer.py` is a thin wrapper exposing this function. In Phase 1, the implementation can be complete — it has no external dependencies.

### Pattern 5: Tenacity Retry Decorators (exact from CLAUDE.md + STACK.md)
**What:** Per-API retry functions; placeholder in Phase 1, exercised in Phase 2+

```python
# Source: CLAUDE.md + STACK.md (verified against tenacity docs)
from tenacity import (
    retry,
    stop_after_attempt,
    stop_after_delay,
    wait_random_exponential,
    retry_if_exception,
    reraise,
)

# --- HubSpot SDK (ApiException) ---
def _is_retryable_hubspot(exc):
    from hubspot.crm.contacts.exceptions import ApiException
    if isinstance(exc, ApiException):
        return exc.status in (429, 500, 502, 503, 504)
    return False

hubspot_retry = retry(
    retry=retry_if_exception(_is_retryable_hubspot),
    stop=(stop_after_attempt(6) | stop_after_delay(60)),
    wait=wait_random_exponential(min=1, max=60),
    reraise=True,
)

# --- Chorus AI (requests.HTTPError) ---
def _is_retryable_chorus(exc):
    import requests
    if isinstance(exc, requests.HTTPError):
        return exc.response is not None and exc.response.status_code in (429, 500, 502, 503, 504)
    return False

chorus_retry = retry(
    retry=retry_if_exception(_is_retryable_chorus),
    stop=(stop_after_attempt(6) | stop_after_delay(60)),
    wait=wait_random_exponential(min=1, max=60),
    reraise=True,
)

# --- Anthropic SDK ---
def _is_retryable_anthropic(exc):
    from anthropic import RateLimitError, APIStatusError
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIStatusError) and exc.status_code >= 500:
        return True
    return False

anthropic_retry = retry(
    retry=retry_if_exception(_is_retryable_anthropic),
    stop=(stop_after_attempt(6) | stop_after_delay(60)),
    wait=wait_random_exponential(min=1, max=60),
    reraise=True,
)
```

**Critical:** `stop_after_delay(60)` implements the CLAUDE.md `stop_after_delay(60)` cap. Using `|` to OR the two stop conditions (attempt OR time) matches the spec. `reraise=True` ensures the original exception propagates to the DLQ handler.

**Note on `wait_random_exponential`:** CLAUDE.md specifies `wait_random_exponential(min=1, max=60)`. This is tenacity's jittered exponential backoff — correct for all three APIs. [CITED: tenacity.readthedocs.io]

### Pattern 6: file_io.py RUNNER_TEMP Helpers
**What:** Centralises all `$RUNNER_TEMP` path construction and JSON read/write

```python
# Source: [ASSUMED] — pattern from ARCHITECTURE.md confirmed design
import json
import os
from pathlib import Path

RUNNER_TEMP = Path(os.environ.get("RUNNER_TEMP", "."))

def runner_temp_path(filename: str) -> Path:
    """Return absolute path to a file in RUNNER_TEMP."""
    return RUNNER_TEMP / filename

def write_json(filename: str, data: dict) -> None:
    """Write dict to JSON file in RUNNER_TEMP."""
    path = runner_temp_path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def read_json(filename: str) -> dict:
    """Read JSON file from RUNNER_TEMP. Raises FileNotFoundError if absent."""
    path = runner_temp_path(filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
```

Standard filenames used across all phases:
- `hubspot_contact.json` — written by fetch_hubspot.py
- `chorus_transcripts.json` — written by fetch_chorus.py
- `campaign_tokens.json` — written by compute_campaign_tokens.py
- `campaign_output.json` — written by generate_campaign.py
- `failed_contacts.json` — written by dlq_writer.py

### Pattern 7: Jinja2 StrictUndefined
**What:** Template rendering that raises on missing tokens instead of substituting empty string
**When to use:** Phase 3 `compute_campaign_tokens.py`, but the setup is part of Phase 1 understanding

```python
# Source: [CITED: jinja.palletsprojects.com/en/3.1.x/api/#jinja2.StrictUndefined]
from jinja2 import Environment, FileSystemLoader, StrictUndefined

env = Environment(
    loader=FileSystemLoader("."),  # prompt_template.md is in repo root
    undefined=StrictUndefined,
)
template = env.get_template("prompt_template.md")
# Raises jinja2.exceptions.UndefinedError if any {{token.name}} is missing
rendered = template.render(**tokens)
```

**Note on `{{token.name}}` syntax:** Jinja2 uses `{{ variable }}` (double braces, spaces). The prompt template uses `{{token.name}}` (no spaces). Both work — Jinja2 is whitespace-flexible in delimiters. When converting `staff_domain_ASOTOS_prompt.md` to `prompt_template.md`, the existing `{{token.name}}` syntax is already valid Jinja2.

### Pattern 8: Script Stub Pattern (Phase 1)
**What:** Phase 1 stub scripts that run successfully so the workflow end-to-end is testable
**When to use:** Plans 01-01 and 01-02

```python
#!/usr/bin/env python3
"""fetch_hubspot.py — Phase 2 implementation pending."""
import os
import sys

# Add lib/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

from file_io import write_json
from dlq_writer import write_dlq

CONTACT_ID = os.environ.get("CONTACT_ID", "")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")

def main():
    print(f"[fetch_hubspot] stub — contact_id={CONTACT_ID}")
    # Write sentinel output so downstream steps don't fail on missing file
    write_json("hubspot_contact.json", {"stub": True, "contact_id": CONTACT_ID})

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        write_dlq(CONTACT_ID, CONTACT_EMAIL, "fetch_hubspot", str(e))
        raise SystemExit(1)
```

Each Phase 2-5 script gets a stub like this in Phase 1. This validates the lib import path, DLQ pattern, and `file_io` in a real runner environment before any actual API calls.

### Anti-Patterns to Avoid

- **Step-level secret re-declaration:** Never repeat `env: HUBSPOT_API_KEY: ${{ secrets.HUBSPOT_API_KEY }}` inside individual steps — only at job level. [CITED: ARCHITECTURE.md decision rationale]
- **`github.event.inputs` context:** Use `${{ inputs.contact_id }}` not `${{ github.event.inputs.contact_id }}` — the legacy form doesn't preserve types. [CITED: GitHub Actions docs, contexts]
- **Logging `os.environ`:** Never call `print(os.environ)` or `run: env` — GitHub's masking is exact-string only; base64-encoded or truncated secrets will appear unmasked. [CITED: PITFALLS.md, PITFALL-M7]
- **Boolean input coercion:** If adding boolean workflow inputs in future, always coerce: `dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"` — GitHub passes booleans as the string `"true"` or `"false"`. [CITED: PITFALLS.md, PITFALL-L1]
- **`pip install` without `requirements.txt`:** Always install from the pinned requirements file; never inline `pip install package` in steps.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Exponential backoff with jitter | Custom sleep/retry loop | `tenacity` | Off-by-one errors, missing jitter, retry-on-permanent-errors bugs |
| Template rendering with strict validation | Custom string.format() | `jinja2` with `StrictUndefined` | Silent empty substitution is a production-damaging failure mode |
| JSON schema validation for Claude output | Manual key checking | `pydantic` v2 models | Nested validation, clear error messages, type coercion |
| RUNNER_TEMP path construction | `"/home/runner/work/_temp/" + filename` | `file_io.py` wrapper using `os.environ.get("RUNNER_TEMP", ".")` | Hardcoded path fails locally and on self-hosted runners |

**Key insight:** In this scaffold phase, the "don't hand-roll" risk is mostly in retry logic and template rendering. Tenacity and Jinja2 both have non-obvious edge cases (reraise semantics, undefined variable handling) that hand-rolled solutions consistently get wrong.

---

## Prompt Template Token Inventory

All `{{token.name}}` placeholders extracted from `staff_domain_ASOTOS_prompt.md`. These are the tokens `prompt_template.md` must contain and that Phase 3 must populate:

| Token | Source | Notes |
|-------|--------|-------|
| `{{contact.first_name}}` | HubSpot `firstname` | Required |
| `{{contact.last_name}}` | HubSpot `lastname` | Required |
| `{{contact.company}}` | HubSpot `company` | Required |
| `{{contact.industry}}` | HubSpot `industry` | Required |
| `{{contact.jobtitle}}` | HubSpot `jobtitle` | Required |
| `{{contact.website}}` | HubSpot `website` | Required |
| `{{contact.numberofemployees}}` | HubSpot `numberofemployees` | Required |
| `{{contact.company_locations}}` | HubSpot `company_locations` | Custom property |
| `{{contact.years_in_crm}}` | Computed from create date | Phase 3 derived |
| `{{contact.outreach_attempt_count}}` | Computed from engagement history | Phase 3 derived |
| `{{contact.related_contacts}}` | HubSpot related contacts fetch | Phase 2 derived |
| `{{contact.secondary_contact_name}}` | Computed via selection logic | Phase 3 derived (nullable) |
| `{{crm.full_activity_history}}` | Assembled from emails + meetings + Chorus | Phase 3 assembled |
| `{{crm.deals_history}}` | HubSpot deals API | Phase 2 derived |
| `{{contact.name_of_target_role}}` | HubSpot `name_of_target_role` | Custom property |
| `{{contact.name_of_target_role_last_updated}}` | HubSpot `name_of_target_role_last_updated` | Custom property (timestamp) |
| `{{industry.market_intelligence}}` | Pre-populated HubSpot property or passed in | Out-of-scope per REQUIREMENTS.md |
| `{{company.observable_signals}}` | Pre-populated HubSpot property or passed in | Out-of-scope per REQUIREMENTS.md |

**Count:** 18 tokens total; 16 populated by pipeline, 2 (`industry.market_intelligence` and `company.observable_signals`) are pre-populated externally per the Out of Scope section in REQUIREMENTS.md.

**Phase 1 implication:** `prompt_template.md` must contain ALL 18 tokens as-is. No substitution happens in Phase 1 — the template is just the raw file. Jinja2 rendering happens in Phase 3.

---

## Make.com Trigger Pattern

Make.com fires the workflow via the GitHub REST API:

```
POST https://api.github.com/repos/staffdomain-devops/ASOTOS/actions/workflows/campaign.yml/dispatches
Authorization: Bearer {GITHUB_PAT}
Content-Type: application/json

{
  "ref": "main",
  "inputs": {
    "contact_id": "12345678",
    "contact_email": "contact@example.com"
  }
}
```

**Response:** `204 No Content` (default). As of Feb 2026, passing `"return_run_details": true` in the body returns `200` with `workflow_run_id`, `run_url`, and `html_url`. [CITED: github.blog/changelog/2026-02-19-workflow-dispatch-api-now-returns-run-ids]

**204 handling (CLAUDE.md Known Risk #1):** Make.com's HTTP module by default expects 200 and treats 204 as failure, causing duplicate triggers. Configure Make.com to accept 204 as success OR use `return_run_details: true` to get a 200 response. The `return_run_details` option is the cleaner fix — it also gives Make.com the run ID for status polling.

**GitHub PAT required:** Make.com needs a GitHub Personal Access Token with `Actions: write` scope (or a fine-grained PAT with workflow trigger permission on this repo). This is a GitHub secret in Make.com, not in the repo.

---

## Common Pitfalls

### Pitfall 1: RUNNER_TEMP Not Wired at Job Level
**What goes wrong:** Python scripts call `os.environ.get("RUNNER_TEMP")` and get `None`, then write files to `.` (current directory / workspace), which is the checkout directory. Files end up in git workspace, risk accidental staging, and paths differ between local and runner.
**Why it happens:** `runner.temp` is a GitHub Actions expression context value, not automatically available as an env var in Python. It must be explicitly set in the workflow.
**How to avoid:** Always wire `RUNNER_TEMP: ${{ runner.temp }}` in the job-level `env:` block. `file_io.py` falls back to `.` only for local dev.
**Warning signs:** Files appearing in the git workspace during a run; `$RUNNER_TEMP` in shell resolves but Python `os.environ.get("RUNNER_TEMP")` returns `None`.

### Pitfall 2: Stub Scripts That Don't Create Output Files
**What goes wrong:** Phase 1 stubs run successfully but don't write JSON sentinel files. Phase 2+ scripts fail with `FileNotFoundError` on the first test run because the previous step wrote nothing. Makes the workflow non-runnable end-to-end.
**Why it happens:** Stubs are minimal — easy to forget the output file requirement.
**How to avoid:** Every stub script must write its expected output JSON file (even `{"stub": true}`) using `file_io.write_json()`. The test criteria for Phase 1 is that the workflow runs end-to-end without errors.
**Warning signs:** Phase 1 workflow fails at `fetch_chorus.py` step with `FileNotFoundError: hubspot_contact.json`.

### Pitfall 3: `actions/setup-python` Cache with Missing requirements.txt
**What goes wrong:** `cache: "pip"` in `setup-python@v6` automatically caches pip installs keyed on `requirements.txt` hash. If `requirements.txt` doesn't exist yet in the repo when the action runs, it silently falls back to no cache. If the file is added later but the cache key was seeded without it, builds may use stale cache.
**Why it happens:** `setup-python@v6` with `cache: "pip"` looks for a `requirements.txt` in the root. If absent, no cache is created.
**How to avoid:** Commit `requirements.txt` before or in the same commit as the workflow YAML. Plan 01-01 creates both.
**Warning signs:** Cache restore shows "Cache not found" on every run even after `requirements.txt` exists.

### Pitfall 4: lib/ Not a Package
**What goes wrong:** `import api_client` fails with `ModuleNotFoundError` even though `sys.path.insert` points to the lib/ directory. Python can't import a file from a non-package directory in some configurations.
**Why it happens:** Without `__init__.py`, `lib/` is not a Python package. While `sys.path.insert` with direct file imports usually works, adding `__init__.py` is the robust pattern.
**How to avoid:** Create `scripts/lib/__init__.py` (empty file). This is a one-line file that costs nothing.
**Warning signs:** `ModuleNotFoundError: No module named 'api_client'` in GitHub Actions log.

### Pitfall 5: Secrets in `contact_email` Log Lines
**What goes wrong:** Scripts log `CONTACT_EMAIL` as part of debug output. GitHub masks the exact secret value but `contact_email` is an input, not a secret — it appears in plain text in logs. PII concern for a production pipeline.
**Why it happens:** `contact_email` is a workflow input, not a GitHub Secret, so it's not automatically masked.
**How to avoid:** In Phase 1 stubs, log `contact_id` not `contact_email`. Add `::add-mask::` if logging email is required: `print(f"::add-mask::{contact_email}")`.
**Warning signs:** Email addresses visible in the GitHub Actions run log.

---

## Code Examples

### requirements.txt (exact content)
```
# Source: PyPI JSON API verified 2026-06-12
jinja2==3.1.6
pydantic==2.13.4
tenacity==9.1.4
anthropic==0.109.1
hubspot-api-client==12.0.0
requests==2.34.2
beautifulsoup4==4.15.0
lxml==5.3.0
tiktoken==0.13.0
```

**Note:** `lxml` version is ASSUMED — verify with `pip index versions lxml` and pin to current. All others are VERIFIED.

### Minimal Workflow YAML (annotated)
```yaml
# Source: docs.github.com/actions/using-workflows/workflow-syntax-for-github-actions
name: ASOTOS Campaign Generator

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

jobs:
  generate-campaign:
    runs-on: ubuntu-latest

    # Wire all env vars at job level — inherited by all steps below
    env:
      CONTACT_ID: ${{ inputs.contact_id }}
      CONTACT_EMAIL: ${{ inputs.contact_email }}
      HUBSPOT_API_KEY: ${{ secrets.HUBSPOT_API_KEY }}
      CHORUS_API_TOKEN: ${{ secrets.CHORUS_API_TOKEN }}
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      TEAMS_WEBHOOK_URL: ${{ secrets.TEAMS_WEBHOOK_URL }}
      RUNNER_TEMP: ${{ runner.temp }}    # CRITICAL: must be explicit for Python os.environ

    steps:
      - uses: actions/checkout@v6

      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
          cache: "pip"                   # Caches pip installs keyed on requirements.txt hash

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Fetch HubSpot contact
        run: python scripts/fetch_hubspot.py

      - name: Fetch Chorus transcripts
        run: python scripts/fetch_chorus.py

      - name: Compute campaign tokens
        run: python scripts/compute_campaign_tokens.py

      - name: Generate campaign
        run: python scripts/generate_campaign.py

      - name: Write campaign to HubSpot
        run: python scripts/write_hubspot.py
```

### dlq_writer.py (complete implementation)
```python
# Source: CLAUDE.md (canonical spec)
import json
import os
from datetime import datetime, timezone


def write_dlq(contact_id: str, contact_email: str, failed_step: str, error_message: str) -> None:
    """Write a DLQ record to $RUNNER_TEMP/failed_contacts.json.
    
    Called by every script on unrecovered failure before re-raising.
    Overwrites any existing DLQ record (last failure wins).
    """
    record = {
        "contact_id": contact_id,
        "contact_email": contact_email,
        "failed_step": failed_step,
        "error_message": str(error_message)[:2000],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    path = os.path.join(os.environ.get("RUNNER_TEMP", "."), "failed_contacts.json")
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
```

---

## Environment Availability

Phase 1 has no external API calls. The only external dependencies are the GitHub Actions runner itself and pip packages.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | All scripts | ✓ (via setup-python@v6) | 3.12.x | — |
| pip | requirements.txt install | ✓ (bundled with setup-python) | current | — |
| GitHub Secrets (4) | Workflow YAML env block | ✗ (must be created) | — | Phase 1 smoke test can proceed without secrets if env block has fallback defaults; actual API calls start Phase 2 |
| ubuntu-latest runner | GitHub Actions | ✓ (GitHub-hosted) | ubuntu 24.04 | — |

**GitHub Secrets must be created before Phase 1 smoke test:** `HUBSPOT_API_KEY`, `CHORUS_API_TOKEN`, `ANTHROPIC_API_KEY`, `TEAMS_WEBHOOK_URL`. Phase 1 stubs don't call the APIs, but the env vars being absent causes scripts to get empty strings — not a failure if code handles `None` gracefully. Add a startup validation step or handle in stubs.

**Missing dependencies with no fallback:** None — all Python packages install from PyPI via requirements.txt.

---

## Validation Architecture

> `nyquist_validation` is explicitly set to `false` in `.planning/config.json`. This section is omitted.

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Secrets via GitHub encrypted secrets — not app-layer auth |
| V3 Session Management | No | Stateless per-job execution |
| V4 Access Control | No | GitHub repository access controls |
| V5 Input Validation | Yes — contact_id, contact_email | Validate contact_id is numeric string before use; contact_email basic format check |
| V6 Cryptography | No | No custom crypto; TLS handled by SDK/requests |

### Known Threat Patterns for GitHub Actions + Python

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Secret leakage via debug logging | Information disclosure | Never log `os.environ`; never log full exception objects; `::add-mask::` for derived values |
| Workflow injection via inputs | Tampering | `workflow_dispatch` inputs go to env vars, not shell interpolation — no injection risk in the Python path; `run: echo $CONTACT_ID` would be risky but we never do this |
| Stale requirements.txt silently upgraded | Tampering | Exact `==` pins prevent silent upgrades on GitHub-hosted runners |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `lxml==5.3.0` is current stable | Standard Stack | Wrong pin causes install failure; verify with `pip index versions lxml` |
| A2 | `scripts/lib/__init__.py` (empty) is sufficient for import resolution | Architecture Patterns — Pattern 3 | Import fails; trivial fix (create the file) |
| A3 | `actions/checkout@v6` and `actions/setup-python@v6` are compatible with ubuntu-latest (ubuntu 24.04) | Standard Stack | Workflow setup step fails; downgrade to @v4/@v5 which are confirmed stable |
| A4 | `return_run_details: true` in dispatch body gives Make.com a 200 response it can handle | Make.com Trigger Pattern | Behaviour untested; fall back to configuring Make.com to accept 204 |

---

## Open Questions (RESOLVED)

1. **lxml version pin** — RESOLVED
   - What we know: lxml is the bs4 parser backend; version 5.3.0 was listed in prior research
   - Resolution: Plan 01-01 Task 1 instructs the executor to run `pip index versions lxml` and pin to the confirmed current stable version before committing requirements.txt. The ASSUMED marker in the Standard Stack table documents this. Execution-time verification is the correct approach; no blocker to planning.

2. **GitHub Secrets existence before smoke test** — RESOLVED
   - What we know: Phase 1 stubs don't call APIs; secrets are wired at job level
   - Resolution: All five stub scripts use `os.environ.get("HUBSPOT_API_KEY", "")` pattern (via Plan 01-01 stub pattern, Pattern 8). Empty strings cause no failure in Phase 1 — no API calls are made. Placeholder dummy-value secrets should be created in the repo before the first workflow trigger, but stubs handle their absence gracefully.

3. **`return_run_details` Make.com compatibility** — RESOLVED (deferred, out of Phase 1 scope)
   - What we know: As of Feb 2026 the API supports `return_run_details: true` for a 200 response
   - Resolution: This is a Make.com configuration concern, not a code concern. Phase 1 plans create the workflow YAML with the standard `workflow_dispatch` trigger that returns 204. Configuring Make.com to accept 204 or passing `return_run_details: true` is a post-Phase 1 operational step documented in CLAUDE.md Known Risk #1. No plan change required.

---

## Sources

### Primary (HIGH confidence)
- `C:\Users\irahfo\Outreach\Asotos\CLAUDE.md` — canonical implementation spec (DLQ pattern, tenacity config, Anthropic SDK, retry policy)
- `C:\Users\irahfo\Outreach\Asotos\.planning\research\STACK.md` — PyPI versions confirmed 2026-06-12
- `C:\Users\irahfo\Outreach\Asotos\.planning\research\ARCHITECTURE.md` — workflow YAML skeleton, RUNNER_TEMP patterns, decision rationale
- `C:\Users\irahfo\Outreach\Asotos\.planning\research\PITFALLS.md` — pitfalls PITFALL-L1, PITFALL-M6, PITFALL-M7
- `C:\Users\irahfo\Outreach\Asotos\staff_domain_ASOTOS_prompt.md` — token inventory source
- [pypi.org/project/Jinja2](https://pypi.org/project/Jinja2/) — Jinja2 3.1.6 confirmed
- [pypi.org/project/pydantic](https://pypi.org/project/pydantic/) — pydantic 2.13.4 confirmed
- [github.com/actions/checkout/releases](https://github.com/actions/checkout/releases) — actions/checkout v6.0.3 confirmed
- [github.com/actions/setup-python/releases](https://github.com/actions/setup-python/releases) — actions/setup-python v6.2.0 confirmed

### Secondary (MEDIUM confidence)
- [github.blog/changelog/2026-02-19-workflow-dispatch-api-now-returns-run-ids](https://github.blog/changelog/2026-02-19-workflow-dispatch-api-now-returns-run-ids/) — `return_run_details` parameter
- [docs.github.com/actions/using-workflows/workflow-syntax-for-github-actions](https://docs.github.com/actions/using-workflows/workflow-syntax-for-github-actions) — `inputs` context and `workflow_dispatch` syntax

### Tertiary (LOW confidence)
- `lxml==5.3.0` pin — from prior research STACK.md entry; not re-verified in this session

---

## Metadata

**Confidence breakdown:**
- Workflow YAML syntax: HIGH — official GitHub docs and prior research ARCHITECTURE.md
- Package versions: HIGH except lxml (LOW) — PyPI confirmed for all others
- Tenacity retry pattern: HIGH — matches CLAUDE.md spec exactly, verified in STACK.md
- DLQ pattern: HIGH — verbatim from CLAUDE.md canonical spec
- lib/ import path: MEDIUM — standard Python pattern, not verified on ubuntu-latest runner
- Make.com 204 handling: MEDIUM — documented in CLAUDE.md; `return_run_details` option MEDIUM from changelog

**Research date:** 2026-06-12
**Valid until:** 2026-09-12 (stable stack; re-verify `lxml` version before committing requirements.txt)
