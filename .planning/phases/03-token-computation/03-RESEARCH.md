# Phase 3: Token Computation — Research

**Researched:** 2026-06-12
**Domain:** Python data transformation, Jinja2 template validation, HubSpot data processing, freshness tier logic
**Confidence:** HIGH — all core patterns verified; Phase 2 output contract is ASSUMED (Phase 2 plans are TBD)

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TOK-01 | Secondary contact selection: peer seniority, prefer CONNECTED, prefer recency, exclude EAs/admins, exclude DNC/opted-out | Selection algorithm defined; seniority classification patterns documented; sort key verified |
| TOK-02 | Compute `contact.years_in_crm` from contact create date to today | HubSpot returns `createdate` as ISO 8601 string; `datetime.fromisoformat` + `.days / 365.25` pattern verified |
| TOK-03 | Compute `contact.outreach_attempt_count` from engagement history | Count outbound emails + meetings from Phase 2 arrays; filter by direction field |
| TOK-04 | Assemble `crm.full_activity_history` from emails + meetings + Chorus transcripts as labelled blocks | Labelled block format defined; Chorus sentinel handling documented |
| TOK-05 | StrictUndefined substitution — missing required tokens cause explicit pipeline failure | Verified: `jinja2.UndefinedError` raised immediately for any missing token; catch pattern confirmed |
</phase_requirements>

---

## Summary

Phase 3 is pure Python computation — no external API calls. It reads `hubspot_contact.json` and `chorus_transcripts.json` from `RUNNER_TEMP` (written by Phase 2), transforms the raw data into a validated token dictionary, and writes `campaign_tokens.json`. There are no new library dependencies beyond what Phase 1 installs.

The central design challenge is the **data contract from Phase 2**. Phase 2 plans are not yet written; Phase 3 must define the input structure it expects so Phase 2 can be planned against it. The expected contract (verified against HUB-01 through HUB-08 requirements and Phase 1 research) is that each HubSpot property is stored as `{"value": X, "updatedAt": Y}` — enabling per-property freshness tier computation.

The Jinja2 token namespace structure is a critical implementation detail: `{{contact.first_name}}` in the template requires the render context to have `contact` as a dict key mapping to a sub-dict. The `campaign_tokens.json` must be structured as `{"contact": {...}, "crm": {...}, "industry": {...}, "company": {...}}` — matching exactly the four Jinja2 namespaces used in `prompt_template.md`.

StrictUndefined validation in Phase 3 is a dry-run render: load `prompt_template.md`, attempt `template.render(**tokens)`, and catch `jinja2.UndefinedError`. Phase 3 does not save the rendered prompt — that is Phase 4's responsibility. The validation render is purely a gate to fail early on missing tokens before Phase 4 attempts the real Claude call.

**Primary recommendation:** Implement `compute_campaign_tokens.py` as a single module with clear function boundaries per concern: `compute_direct_tokens()`, `compute_derived_tokens()`, `assemble_activity_history()`, `select_secondary_contact()`, `validate_tokens()`. This isolation makes each function independently unit-testable with fixture JSON without any external API calls.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Read Phase 2 outputs | `compute_campaign_tokens.py` | `scripts/lib/file_io.py` | file_io.read_json abstracts RUNNER_TEMP path resolution |
| Freshness tier classification | `compute_campaign_tokens.py` | — | Pure date arithmetic, no external dependencies |
| Secondary contact selection | `compute_campaign_tokens.py` | — | Business logic isolated in this script only |
| Activity history assembly | `compute_campaign_tokens.py` | — | Formatting logic, no external calls |
| Token validation (StrictUndefined) | `compute_campaign_tokens.py` | `prompt_template.md` | Validation render reads repo root template |
| Write campaign_tokens.json | `compute_campaign_tokens.py` | `scripts/lib/file_io.py` | file_io.write_json to RUNNER_TEMP |
| Failure recording | `scripts/lib/dlq_writer.py` | — | Called by Phase 3 on unrecovered failure per CLAUDE.md pattern |

---

## Standard Stack

### Core (all installed by Phase 1 — no new packages needed)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| jinja2 | ==3.1.6 | StrictUndefined validation render | [VERIFIED: Phase 1 RESEARCH.md, pypi.org/project/Jinja2] — already in requirements.txt |
| datetime | stdlib | years_in_crm, freshness tier calculation | Standard library, no install required |
| json | stdlib | Reading/writing JSON files via file_io helpers | Standard library |

**No new packages required for Phase 3.** All computation is pure Python using stdlib + Jinja2 (already pinned in requirements.txt).

### Phase 2 Inputs Required (from file_io.read_json)

| File | Written By | Required For |
|------|-----------|-------------|
| `hubspot_contact.json` | `fetch_hubspot.py` (Phase 2) | All token computation |
| `chorus_transcripts.json` | `fetch_chorus.py` (Phase 2) | `crm.full_activity_history` assembly |

---

## Architecture Patterns

### System Architecture Diagram

```
RUNNER_TEMP/hubspot_contact.json  ─┐
RUNNER_TEMP/chorus_transcripts.json ─┤
                                    │
                                    ▼
                    compute_campaign_tokens.py
                           │
                   ┌───────┴──────────────────────┐
                   │                              │
         compute_direct_tokens()      compute_derived_tokens()
         (pass-through from HubSpot)  (secondary_contact, years_in_crm,
         - firstname, lastname         outreach_count, activity_history,
         - company, industry           deals_history)
         - jobtitle, website                      │
         - numberofemployees                       │
         - company_locations                       │
         - name_of_target_role                     │
         - market_intelligence                     │
         - observable_signals           assemble_activity_history()
                   │                   (emails + meetings + Chorus)
                   │                              │
                   └──────────────┬───────────────┘
                                  │
                            tokens dict (4 namespaces:
                            contact, crm, industry, company)
                                  │
                      validate_tokens() — Jinja2 StrictUndefined
                      load prompt_template.md, attempt render
                      UndefinedError -> DLQ + SystemExit(1)
                                  │
                                  ▼
                    RUNNER_TEMP/campaign_tokens.json
```

### Recommended Project Structure

No new files or directories. Phase 3 adds one script:

```
scripts/
├── lib/                    # Phase 1 — shared utilities
│   ├── __init__.py
│   ├── api_client.py
│   ├── dlq_writer.py
│   └── file_io.py
├── compute_campaign_tokens.py   # Phase 3 — this phase
├── fetch_hubspot.py        # Phase 2
├── fetch_chorus.py         # Phase 2
├── generate_campaign.py    # Phase 4 stub
└── write_hubspot.py        # Phase 5 stub
prompt_template.md          # Phase 1 — rendered here for validation
```

### Pattern 1: Input Contract — Expected hubspot_contact.json Structure

**What:** The data structure Phase 3 expects from Phase 2's `hubspot_contact.json`.
**Why documented here:** Phase 2 plans are TBD — Phase 3 must define the contract it consumes so Phase 2 can be planned against it.

```python
# [ASSUMED] — inferred from HUB-01 through HUB-08 requirements and HubSpot v3 API patterns
# Phase 2 must produce this structure; if it differs, Phase 3 accessor code must adapt

hubspot_contact = {
    "id": "12345678",
    "createdAt": "2022-03-15T00:00:00.000Z",   # ISO 8601, for years_in_crm computation
    "properties": {
        "firstname":    {"value": "John",          "updatedAt": "2026-01-01T00:00:00.000Z"},
        "lastname":     {"value": "Smith",         "updatedAt": "2026-01-01T00:00:00.000Z"},
        "company":      {"value": "Acme Corp",     "updatedAt": "2025-06-01T00:00:00.000Z"},
        "industry":     {"value": "Accounting",    "updatedAt": "2025-06-01T00:00:00.000Z"},
        "jobtitle":     {"value": "CEO",           "updatedAt": "2025-06-01T00:00:00.000Z"},
        "website":      {"value": "https://acme.com.au", "updatedAt": "2025-06-01T00:00:00.000Z"},
        "numberofemployees": {"value": "50-100",   "updatedAt": "2025-01-01T00:00:00.000Z"},
        "company_locations": {"value": "Sydney, Melbourne", "updatedAt": "2025-01-01T00:00:00.000Z"},
        "name_of_target_role": {"value": "Senior Accountant", "updatedAt": "2025-09-15T00:00:00.000Z"},
        "name_of_target_role_last_updated": {"value": "2025-09-15", "updatedAt": "2025-09-15T00:00:00.000Z"},
        # External tokens (pre-populated in HubSpot; may be absent):
        "industry_market_intelligence": {"value": "...", "updatedAt": "..."},
        "company_observable_signals":   {"value": "...", "updatedAt": "..."},
    },
    "emails": [
        {
            "direction": "EMAIL",           # "EMAIL" = outbound, "INCOMING_EMAIL" = inbound
            "timestamp": "2026-01-15T09:00:00.000Z",
            "subject": "Following up on our conversation",
            "body_text": "Hi John..."       # HTML already stripped by Phase 2 (bs4)
        }
    ],
    "meetings": [
        {
            "timestamp": "2025-12-01T10:00:00.000Z",
            "title": "Discovery Call",
            "body": "Discussed hiring needs...",
            "internal_notes": "Good prospect, follow up Q1",
            "attendees": ["John Smith", "Sarah Doe"]
        }
    ],
    "deals": [
        {
            "dealname": "Offshore Staffing Setup",
            "dealstage": "closedwon",
            "amount": "15000",
            "closedate": "2023-06-01T00:00:00.000Z",
            "pipeline": "default"
        }
    ],
    "related_contacts": [
        {
            "id": "12346",
            "firstname": "Jane",
            "lastname": "Smith",
            "jobtitle": "CFO",
            "hs_lead_status": "CONNECTED",       # used in TOK-01 selection
            "hs_email_optout": False,             # True = excluded from selection
            "notes_last_contacted": "2025-12-01T00:00:00.000Z"  # for recency sort
        }
    ],
    "owner": {"firstName": "Sarah", "lastName": "Doe"}
}
```

### Pattern 2: Input Contract — Expected chorus_transcripts.json Structure

```python
# [ASSUMED] — inferred from CHO-01, CHO-02, ARCHITECTURE.md sentinel spec

# Success case:
chorus_success = {
    "transcripts": [
        {
            "conversation_id": "abc123",
            "date": "2025-12-01T10:00:00.000Z",
            "title": "Discovery Call with John Smith",
            "transcript": "Full transcript text or summary..."
        }
    ]
}

# Failure/absence case (sentinel per CLAUDE.md + CHO-02):
chorus_sentinel = {
    "transcript_available": False,
    "transcript_status": "unavailable_404"  # or "unavailable_401"
}
```

### Pattern 3: Jinja2 Token Namespace Structure

**What:** The `campaign_tokens.json` dict structure that maps exactly to the four Jinja2 namespaces in `prompt_template.md`. Verified: Jinja2 dot notation `{{contact.first_name}}` requires `contact` to be a dict key in the render context.

```python
# Source: [VERIFIED: Jinja2 docs, ctx7 /websites/jinja_palletsprojects_en_stable]
# Verified locally: template.render(contact={'first_name': 'John'}) produces 'John'
# Verified locally: template.render(contact={}) raises UndefinedError 'dict object has no attribute first_name'

campaign_tokens = {
    "contact": {
        "first_name": "John",
        "last_name": "Smith",
        "company": "Acme Corp",
        "industry": "Accounting",
        "jobtitle": "CEO",
        "website": "https://acme.com.au",
        "numberofemployees": "50-100",
        "company_locations": "Sydney, Melbourne",
        "years_in_crm": "4.2",                    # string, computed
        "outreach_attempt_count": "7",             # string, computed
        "related_contacts": "Jane Smith (CFO, CONNECTED), Bob Jones (Director of Ops)",
        "secondary_contact_name": "Jane Smith",   # or "null" (string, not Python None)
        "name_of_target_role": "Senior Accountant",
        "name_of_target_role_last_updated": "2025-09-15 (9 months ago)",
    },
    "crm": {
        "full_activity_history": "=== EMAIL HISTORY ===\n...",  # assembled text block
        "deals_history": "- Offshore Staffing Setup (AUD 15000) [closedwon]",
    },
    "industry": {
        "market_intelligence": "The accounting sector is facing...",  # from HubSpot property; default ""
    },
    "company": {
        "observable_signals": "Acme Corp recently expanded to Melbourne...",  # from HubSpot property; default ""
    },
}
```

**Render call:**

```python
# Source: [VERIFIED: Jinja2 docs]
from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError

env = Environment(
    loader=FileSystemLoader("."),  # cwd = GITHUB_WORKSPACE = repo root in CI
    undefined=StrictUndefined,
)
template = env.get_template("prompt_template.md")
try:
    rendered = template.render(**campaign_tokens)
    # rendered is a validated string — discard; Phase 4 renders for real
except UndefinedError as e:
    # e.g. "'dict object' has no attribute 'secondary_contact_name'"
    raise  # caller handles DLQ + SystemExit(1)
```

**Critical:** Use `template.render(**campaign_tokens)` — the double-star unpacks the outer dict so `contact`, `crm`, `industry`, `company` become top-level Jinja2 variables. Do NOT use `template.render(tokens=campaign_tokens)` — that would require `{{tokens.contact.first_name}}` syntax.

### Pattern 4: Freshness Tier Computation

**What:** Apply CLAUDE.md freshness tiers to any HubSpot property with an `updatedAt` timestamp.

```python
# Source: CLAUDE.md Data Freshness tiers; verified with datetime arithmetic locally
from datetime import datetime, timezone

FRESHNESS_TIERS = {
    "recently":          (0, 60),     # < 60 days
    "past_observation":  (60, 183),   # 60 days to ~6 months
    "earlier_this_year": (183, 365),  # 6 to 12 months
    "drop_or_reframe":   (365, None), # 12+ months
}

def compute_freshness(updated_at_iso: str) -> tuple[str, int]:
    """Return (tier_name, age_days) for a HubSpot ISO 8601 timestamp.
    
    Returns ('drop_or_reframe', -1) if updated_at_iso is None/empty.
    """
    if not updated_at_iso:
        return "drop_or_reframe", -1
    updated_at = datetime.fromisoformat(updated_at_iso.replace("Z", "+00:00"))
    age_days = (datetime.now(timezone.utc) - updated_at).days
    for tier, (low, high) in FRESHNESS_TIERS.items():
        if high is None or age_days < high:
            if age_days >= low:
                return tier, age_days
    return "drop_or_reframe", age_days

def format_timestamp_with_age(updated_at_iso: str) -> str:
    """Format timestamp as human-readable string with age for Claude context.
    Example: '2025-09-15 (9 months ago)'
    """
    if not updated_at_iso:
        return "unknown"
    tier, age_days = compute_freshness(updated_at_iso)
    date_part = updated_at_iso[:10]  # 'YYYY-MM-DD'
    if age_days < 0:
        return "unknown"
    elif age_days < 60:
        return f"{date_part} ({age_days} days ago)"
    elif age_days < 365:
        months = round(age_days / 30)
        return f"{date_part} ({months} months ago)"
    else:
        years = round(age_days / 365.25, 1)
        return f"{date_part} ({years} years ago)"
```

### Pattern 5: years_in_crm Computation

```python
# Source: [VERIFIED: datetime stdlib; HubSpot createdate format confirmed as ISO 8601]
from datetime import datetime, timezone

def compute_years_in_crm(created_at_iso: str) -> str:
    """Compute years since HubSpot contact was created.
    
    Returns a string like '4.2' for use in the prompt template.
    HubSpot createdAt is ISO 8601: 'YYYY-MM-DDTHH:MM:SS.sssZ'
    """
    if not created_at_iso:
        return "unknown"
    created_at = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
    days = (datetime.now(timezone.utc) - created_at).days
    years = round(days / 365.25, 1)
    return str(years)
```

### Pattern 6: outreach_attempt_count Computation

```python
# Source: [ASSUMED] — inferred from TOK-03, HUB-02, HUB-03 requirements
# 'distinct outreach attempts' = outbound emails + booked/completed meetings
# Direction field: 'EMAIL' = outbound; 'INCOMING_EMAIL' = inbound (exclude)
# Meetings are always outreach attempts (scheduler-created or manually logged)

def compute_outreach_count(emails: list, meetings: list) -> str:
    """Count distinct outreach attempts from engagement history.
    
    Counts: outbound emails (direction='EMAIL') + all meetings.
    Excludes: inbound emails (direction='INCOMING_EMAIL'), notes, tasks.
    Returns string for template injection.
    """
    outbound_emails = [e for e in emails if e.get("direction") == "EMAIL"]
    count = len(outbound_emails) + len(meetings)
    return str(count)
```

### Pattern 7: Secondary Contact Selection (TOK-01)

```python
# Source: prompt_template.md SECONDARY CONTACT SELECTION LOGIC section (verbatim spec)
# Verified locally: sort key logic produces correct ordering

# EA/admin exclusion keywords (case-insensitive match)
_EXCLUDED_TITLE_KEYWORDS = [
    "executive assistant", "personal assistant", " pa ", "pa,", "pa)",
    "admin", "administrator", "coordinator", "receptionist",
    "office manager", " ea ", "ea,", "ea)", "secretary",
]

# Seniority tiers for peer-level matching
_CSUITE = ["ceo", "coo", "cfo", "cto", "cmo", "founder", "co-founder",
           "managing director", "md,", "md)", " md ", "president", "owner",
           "partner", "principal"]
_DIRECTOR = ["director", "head of", "vp", "vice president", "general manager",
             " gm ", "gm,", "gm)"]

def _classify_seniority(title: str) -> str:
    if not title:
        return "unknown"
    t = title.lower()
    if any(kw in t for kw in _CSUITE):
        return "C-suite"
    if any(kw in t for kw in _DIRECTOR):
        return "Director"
    return "Other"

def _is_excluded_title(title: str) -> bool:
    if not title:
        return True  # No title = exclude (likely admin)
    t = title.lower()
    return any(kw in t for kw in _EXCLUDED_TITLE_KEYWORDS)

def _parse_last_contacted(contact: dict):
    """Return sortable epoch int for notes_last_contacted, or 0 if absent."""
    ts = contact.get("notes_last_contacted", "")
    if not ts:
        return 0
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except (ValueError, AttributeError):
        return 0

def select_secondary_contact(primary_jobtitle: str, related_contacts: list) -> str:
    """Apply TOK-01 selection logic. Returns first name only, or 'null'.
    
    Peer-level seniority: C-suite with C-suite/Director; Other with Other/Director.
    Sorts by: CONNECTED status first, then most recently contacted.
    Excludes: EAs/admins, DNC/opted-out contacts.
    Returns 'null' (string) when no suitable contact found.
    """
    primary_seniority = _classify_seniority(primary_jobtitle)
    
    candidates = []
    for c in related_contacts:
        # Exclude opted-out
        if c.get("hs_email_optout"):
            continue
        # Exclude EA/admin titles
        if _is_excluded_title(c.get("jobtitle", "")):
            continue
        # Peer-level seniority filter
        seniority = _classify_seniority(c.get("jobtitle", ""))
        if primary_seniority == "C-suite" and seniority not in ("C-suite", "Director"):
            continue
        if primary_seniority == "Other" and seniority not in ("Other", "Director"):
            continue
        candidates.append(c)
    
    if not candidates:
        return "null"
    
    # Sort: CONNECTED first, then most recently contacted
    candidates.sort(key=lambda x: (
        0 if x.get("hs_lead_status") == "CONNECTED" else 1,
        -_parse_last_contacted(x),
    ))
    
    best = candidates[0]
    first_name = best.get("firstname", "").strip()
    return first_name if first_name else "null"
```

### Pattern 8: Activity History Assembly (TOK-04)

```python
# Source: [ASSUMED] format; logic derived from TOK-04 requirement and prompt context
# The full_activity_history is a plain-text narrative block for Claude to read

def assemble_activity_history(
    emails: list,
    meetings: list,
    chorus_data: dict,
) -> str:
    """Assemble crm.full_activity_history as labelled plain-text blocks.
    
    Sections: EMAIL HISTORY, MEETING HISTORY, CHORUS TRANSCRIPTS (or note if unavailable).
    Sorted by timestamp within each section (newest last for readability).
    """
    lines = []
    
    # === EMAIL HISTORY ===
    lines.append("=== EMAIL HISTORY ===")
    if emails:
        sorted_emails = sorted(emails, key=lambda e: e.get("timestamp", ""))
        for email in sorted_emails:
            direction = "OUTBOUND" if email.get("direction") == "EMAIL" else "INBOUND"
            ts = email.get("timestamp", "")[:10]  # YYYY-MM-DD
            subject = email.get("subject", "(no subject)")
            body = (email.get("body_text", "") or "")[:500]  # truncate body
            lines.append(f"[{ts}] {direction} EMAIL")
            lines.append(f"Subject: {subject}")
            if body:
                lines.append(f"Body: {body}")
            lines.append("")
    else:
        lines.append("No email history.")
        lines.append("")
    
    # === MEETING HISTORY ===
    lines.append("=== MEETING HISTORY ===")
    if meetings:
        sorted_meetings = sorted(meetings, key=lambda m: m.get("timestamp", ""))
        for meeting in sorted_meetings:
            ts = meeting.get("timestamp", "")[:10]
            title = meeting.get("title", "Meeting")
            body = (meeting.get("body", "") or "")[:500]
            internal = (meeting.get("internal_notes", "") or "")[:200]
            attendees = meeting.get("attendees", [])
            lines.append(f"[{ts}] MEETING — {title}")
            if attendees:
                lines.append(f"Attendees: {', '.join(attendees)}")
            if body:
                lines.append(f"Notes: {body}")
            if internal:
                lines.append(f"Internal notes: {internal}")
            lines.append("")
    else:
        lines.append("No meeting history.")
        lines.append("")
    
    # === CHORUS TRANSCRIPTS ===
    lines.append("=== CHORUS TRANSCRIPTS ===")
    transcripts = chorus_data.get("transcripts")
    if transcripts:
        for t in transcripts:
            ts = (t.get("date", "") or "")[:10]
            title = t.get("title", "Transcript")
            text = (t.get("transcript", "") or "")[:2000]  # truncate for token budget
            lines.append(f"[{ts}] TRANSCRIPT — {title}")
            if text:
                lines.append(text)
            lines.append("")
    elif chorus_data.get("transcript_available") is False:
        status = chorus_data.get("transcript_status", "unavailable")
        lines.append(f"No Chorus transcripts available ({status}).")
        lines.append("Do not reference call content — only base STATE classification on email and meeting history.")
    else:
        lines.append("No Chorus transcripts.")
    
    return "\n".join(lines)
```

### Pattern 9: name_of_target_role Handling (CLAUDE.md Rule 4)

**What:** The `name_of_target_role` field has special freshness handling per CLAUDE.md Rule 4. Phase 3 formats the `name_of_target_role_last_updated` token as a human-readable string with age — Claude then applies Rule 4 from the prompt instructions.

```python
# Phase 3 responsibility: format the timestamp with age
# Claude's responsibility: apply the Rule 4 framing (< 60 days / 60d-12m / 12m+)
# No special pre-processing of the role VALUE itself — pass through as-is

role = props.get("name_of_target_role", {}).get("value", "")
role_updated_at = props.get("name_of_target_role", {}).get("updatedAt", "")
# OR: if Phase 2 stores last_updated in a separate field:
# role_updated_at = props.get("name_of_target_role_last_updated", {}).get("updatedAt", "")

tokens["contact"]["name_of_target_role"] = role or "Not specified"
tokens["contact"]["name_of_target_role_last_updated"] = format_timestamp_with_age(role_updated_at)
# e.g. "2025-09-15 (9 months ago)"
```

**Why no pre-filtering:** The prompt's Rule 4 already instructs Claude how to handle each tier. Passing the formatted timestamp alongside the raw value gives Claude the information it needs. Pre-filtering (dropping the value before passing to Claude) would remove Claude's ability to surface the role indirectly through industry framing (the 12m+ case requires knowing the role category, not the specific value).

### Pattern 10: `secondary_contact_name` Null Handling

```python
# Source: [VERIFIED locally] — Python None renders as "None" in Jinja2; use string "null"
# 'null' (string) is JSON-idiomatic and unambiguous for Claude
# The prompt says: "If no suitable secondary contact exists, set to null"

secondary_name = select_secondary_contact(primary_jobtitle, related_contacts)
# select_secondary_contact() returns "null" (string) when no candidate found
tokens["contact"]["secondary_contact_name"] = secondary_name  # "Jane Smith" or "null"
```

### Pattern 11: External Tokens Fallback

```python
# Source: REQUIREMENTS.md Out of Scope — these tokens are pre-populated externally
# If absent from HubSpot, default to empty string (not UndefinedError)
# The prompt will work without these; Claude handles empty gracefully

market_intel = props.get("industry_market_intelligence", {}).get("value", "")
signals = props.get("company_observable_signals", {}).get("value", "")

# ASSUMPTION: HubSpot property internal names are 'industry_market_intelligence' 
# and 'company_observable_signals'. Phase 2 must confirm these exact names.
# If different, only the accessor key changes — no structural impact on Phase 3.

tokens["industry"]["market_intelligence"] = market_intel or ""
tokens["company"]["observable_signals"] = signals or ""
```

### Anti-Patterns to Avoid

- **Python None for secondary_contact_name:** Renders as `"None"` in Jinja2 output. Use string `"null"` instead. [VERIFIED: tested locally]
- **`template.render(tokens=campaign_tokens)`:** Wraps the dict under a `tokens` key; template expects top-level `contact`, `crm`, `industry`, `company`. Use `template.render(**campaign_tokens)`. [VERIFIED: Jinja2 docs]
- **FileSystemLoader from wrong directory:** If script is invoked from `scripts/` subdirectory, `FileSystemLoader(".")` won't find `prompt_template.md` (which is in repo root). GitHub Actions invokes from repo root. Include a guard: if `prompt_template.md` not found, check `../prompt_template.md` for local dev. [VERIFIED: GitHub Actions cwd = GITHUB_WORKSPACE = repo root]
- **Catching generic Exception before UndefinedError:** The DLQ handler should re-raise `UndefinedError` with a clear message identifying which token is missing. Don't swallow the error name in a broad `except Exception`. Log `str(e)` which includes the token name.
- **Counting inbound emails as outreach attempts:** `direction='INCOMING_EMAIL'` is a reply from the prospect, not an outreach attempt. Only `direction='EMAIL'` (outbound) counts toward TOK-03.
- **Hardcoding seniority keywords:** The title-matching lists are heuristic and must handle edge cases (MD., Managing Director, Co-Founder). Use substring matching, not exact match.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Template validation with failure on missing vars | Custom manifest check iterating token names | Jinja2 StrictUndefined test render | StrictUndefined raises with exact variable name; no custom inventory to maintain |
| Date difference calculation | Custom year/month arithmetic | `datetime` stdlib + `.days / 365.25` | Handles leap years correctly; fromisoformat handles both UTC-Z and +00:00 suffixes |
| Contact title exclusion matching | Complex regex | Simple `any(kw in title.lower() for kw in KEYWORDS)` | Sufficient for EA/admin detection; regex adds no benefit over substring match |

**Key insight:** Phase 3 is pure computation — the complexity is in the business rules (freshness tiers, secondary contact selection, activity assembly), not in any technical infrastructure. Keep each rule in its own function so it can be tested independently with fixture data.

---

## Common Pitfalls

### Pitfall 1: Phase 2 Output Contract Mismatch
**What goes wrong:** Phase 3 expects `properties.firstname.value` but Phase 2 delivers `properties.firstname` directly (flat string). Or Phase 2 stores `updatedAt` at the property level but Phase 3 looks for a separate `properties_timestamps` key.
**Why it happens:** Phase 2 plans are TBD; the contract is an assumption.
**How to avoid:** Document the expected contract explicitly in this research (Pattern 1, Pattern 2). Phase 3 PLAN must include a task that defines the contract as a must-have truth, so Phase 2 planning can verify conformance before Phase 3 is executed.
**Warning signs:** `KeyError` or `TypeError` when accessing `props["firstname"]["value"]` in Phase 3.

### Pitfall 2: FileSystemLoader Working Directory
**What goes wrong:** `FileSystemLoader(".")` loads from wherever the script is invoked. If a developer runs `python scripts/compute_campaign_tokens.py` from the `scripts/` directory, the loader looks for `prompt_template.md` in `scripts/` (not found).
**Why it happens:** `os.getcwd()` differs between GitHub Actions (repo root) and local development.
**How to avoid:** Use `FileSystemLoader(os.path.join(os.path.dirname(__file__), ".."))` — resolves to the parent directory of the script (= repo root) regardless of invocation location. This is more robust than `"."`.
**Warning signs:** `jinja2.TemplateNotFound: prompt_template.md` during local testing.

```python
import os
from jinja2 import Environment, FileSystemLoader, StrictUndefined

# Robust: relative to this script's location, not cwd
REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
env = Environment(
    loader=FileSystemLoader(REPO_ROOT),
    undefined=StrictUndefined,
)
```

### Pitfall 3: Chorus Sentinel Not Handled
**What goes wrong:** `crm.full_activity_history` assembly reads `chorus_data["transcripts"]` directly. When the sentinel is `{"transcript_available": false}`, accessing `["transcripts"]` raises `KeyError`. The activity history step fails entirely.
**Why it happens:** Chorus sentinel and success structures have different top-level keys.
**How to avoid:** Always check for `transcript_available` key first. `transcripts = chorus_data.get("transcripts")` returns `None` safely; then `if transcripts:` handles both absent and empty arrays.
**Warning signs:** `KeyError: 'transcripts'` in the `assemble_activity_history` function.

### Pitfall 4: StrictUndefined on Empty String
**What goes wrong:** A HubSpot property exists but has value `""` (empty string). `StrictUndefined` does NOT raise on empty strings — it only raises on fully absent keys. An empty string passes validation and renders as nothing in the prompt. Claude receives `"Number of previous outreach attempts: "` with no value.
**Why it happens:** Empty string is a valid Python value; Jinja2 StrictUndefined only catches missing *keys*, not empty *values*.
**How to avoid:** Add a post-computation manifest check for required tokens: for each non-optional token in `campaign_tokens`, assert `value is not None and value != ""`. Log a warning (not error) for genuinely optional tokens.
**Warning signs:** Prompt contains "Name: " or "Company: " with nothing after the colon.

### Pitfall 5: years_in_crm Using Wrong Date Field
**What goes wrong:** Script reads `properties.createdAt` from inside the `properties` dict, but the actual create timestamp is at the top-level `createdAt` key of the contact object (not a property).
**Why it happens:** HubSpot v3 API returns two different date fields: `contact.createdAt` (top-level, ISO 8601) and `contact.properties.createdate` (which may be the same but is stored differently). Phase 2 must surface the correct one.
**How to avoid:** Prefer the top-level `contact["createdAt"]` for years_in_crm. Fall back to `contact["properties"]["createdate"]["value"]` if top-level is absent.
**Warning signs:** `years_in_crm` computes as a very large or negative number.

---

## Code Examples

### Complete Script Skeleton

```python
#!/usr/bin/env python3
"""compute_campaign_tokens.py — Phase 3: Token Computation.

Reads hubspot_contact.json and chorus_transcripts.json from RUNNER_TEMP.
Computes all 18 prompt tokens with freshness tiers and secondary contact selection.
Validates tokens via Jinja2 StrictUndefined test render.
Writes campaign_tokens.json to RUNNER_TEMP.
"""
import os
import sys
from datetime import datetime, timezone

# Add lib/ to path (standard Phase 2-5 pattern from CLAUDE.md)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from file_io import read_json, write_json
from dlq_writer import write_dlq

from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError

CONTACT_ID = os.environ.get("CONTACT_ID", "")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")

# Repo root for prompt_template.md (robust against cwd variation)
REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def main():
    # 1. Read Phase 2 outputs
    contact = read_json("hubspot_contact.json")
    chorus = read_json("chorus_transcripts.json")
    
    props = contact.get("properties", {})
    
    # 2. Build token dict
    tokens = {
        "contact": {
            "first_name": props.get("firstname", {}).get("value", ""),
            "last_name":  props.get("lastname", {}).get("value", ""),
            # ... all 14 contact tokens ...
            "secondary_contact_name": select_secondary_contact(
                primary_jobtitle=props.get("jobtitle", {}).get("value", ""),
                related_contacts=contact.get("related_contacts", []),
            ),
            "years_in_crm": compute_years_in_crm(contact.get("createdAt", "")),
            "outreach_attempt_count": compute_outreach_count(
                contact.get("emails", []),
                contact.get("meetings", []),
            ),
            "related_contacts": format_related_contacts(contact.get("related_contacts", [])),
            "name_of_target_role": props.get("name_of_target_role", {}).get("value", "Not specified"),
            "name_of_target_role_last_updated": format_timestamp_with_age(
                props.get("name_of_target_role", {}).get("updatedAt", "")
            ),
        },
        "crm": {
            "full_activity_history": assemble_activity_history(
                emails=contact.get("emails", []),
                meetings=contact.get("meetings", []),
                chorus_data=chorus,
            ),
            "deals_history": format_deals(contact.get("deals", [])),
        },
        "industry": {
            "market_intelligence": props.get("industry_market_intelligence", {}).get("value", ""),
        },
        "company": {
            "observable_signals": props.get("company_observable_signals", {}).get("value", ""),
        },
    }
    
    # 3. Validate via StrictUndefined test render
    env = Environment(
        loader=FileSystemLoader(REPO_ROOT),
        undefined=StrictUndefined,
    )
    template = env.get_template("prompt_template.md")
    try:
        template.render(**tokens)  # discard result; validation only
    except UndefinedError as e:
        print(f"[compute_campaign_tokens] VALIDATION FAILED — missing token: {e}", file=sys.stderr)
        raise
    
    # 4. Write output
    write_json("campaign_tokens.json", tokens)
    print(f"[compute_campaign_tokens] campaign_tokens.json written — {len(tokens)} namespaces")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        write_dlq(CONTACT_ID, CONTACT_EMAIL, "compute_campaign_tokens", str(e))
        raise SystemExit(1)
```

### Token Validation Manifest Check (Pitfall 4 mitigation)

```python
# Source: PITFALLS.md PITFALL-C2 — manifest validation for empty strings
# Run after building tokens dict, before StrictUndefined render

# Required tokens — pipeline MUST have non-empty values
REQUIRED_TOKENS = [
    ("contact", "first_name"),
    ("contact", "last_name"),
    ("contact", "company"),
    ("contact", "jobtitle"),
    ("contact", "years_in_crm"),
    ("contact", "outreach_attempt_count"),
    ("crm", "full_activity_history"),
]

def validate_required_tokens(tokens: dict) -> None:
    """Assert required tokens are non-empty. Raises ValueError on failure."""
    missing = []
    for namespace, key in REQUIRED_TOKENS:
        val = tokens.get(namespace, {}).get(key)
        if not val or (isinstance(val, str) and val.strip() == ""):
            missing.append(f"{namespace}.{key}")
    if missing:
        raise ValueError(f"Required tokens missing or empty: {', '.join(missing)}")
```

---

## Data Flow: Phase 2 Output Contract

This section is authoritative — Phase 2 planning MUST produce `hubspot_contact.json` conforming to Pattern 1 above. The key requirements:

| Field | Location in hubspot_contact.json | Required For | Confidence |
|-------|----------------------------------|-------------|------------|
| `createdAt` | Top-level key | TOK-02 (years_in_crm) | [ASSUMED] |
| `properties.*.value` | Each property has `{value, updatedAt}` | All direct tokens | [ASSUMED] |
| `properties.*.updatedAt` | Per-property timestamp | Freshness tiers | [ASSUMED — HUB-08 requirement] |
| `emails[].direction` | `"EMAIL"` = outbound, `"INCOMING_EMAIL"` = inbound | TOK-03 | [ASSUMED] |
| `meetings[]` | Array of meeting objects | TOK-03, TOK-04 | [ASSUMED] |
| `related_contacts[]` | Array with `hs_lead_status`, `hs_email_optout`, `notes_last_contacted` | TOK-01 | [ASSUMED] |
| `deals[]` | Array of deal objects | `crm.deals_history` | [ASSUMED] |

**If Phase 2 uses a different structure** (e.g., flat properties dict, no per-property `updatedAt`), Phase 3 accessors need adjustment but the algorithm is the same.

---

## Open Questions (RESOLVED)

1. **HubSpot property names for external tokens** — RESOLVED (assumed names with empty-string fallback)
   - Resolution: Use `industry_market_intelligence` and `company_observable_signals` as HubSpot internal property names. If absent from `hubspot_contact.json`, Phase 3 defaults to empty string — these tokens are pre-populated externally and their absence is not a pipeline failure.

2. **Phase 2 output contract for `createdate` vs `createdAt`** — RESOLVED (top-level `createdAt`, property fallback)
   - Resolution: Phase 3 reads `data["createdAt"]` for years_in_crm computation, with fallback to `data["properties"].get("createdate", {}).get("value")`. This handles either format Phase 2 may produce.

3. **Chorus transcript object schema** — RESOLVED (use `.get()` with defaults)
   - Resolution: Phase 3 accesses all transcript fields via `.get("title", "")`, `.get("date", "")`, `.get("transcript", "")`. Field-name variance from the Phase 2 smoke test is handled gracefully without code changes.

4. **Outreach attempt definition boundary** — RESOLVED (outbound emails + all meetings)
   - Resolution: TOK-03 counts len(outbound emails where direction != INCOMING_EMAIL) + len(meetings). Phase 2 delivers no calls array (not in ROADMAP requirements). This count is documented in the plan for future extension.

---

## Environment Availability

Phase 3 has no external API calls — all computation is local Python. Step 2.6 skipped.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | Script execution | ✓ (installed by Phase 1) | 3.12.x | — |
| jinja2 | StrictUndefined validation | ✓ (requirements.txt) | ==3.1.6 | — |
| datetime (stdlib) | years_in_crm, freshness tiers | ✓ | stdlib | — |
| scripts/lib/ | file_io, dlq_writer | ✓ (Phase 1) | Phase 1 output | — |
| prompt_template.md | Validation render | ✓ (Phase 1) | Phase 1 output | — |
| hubspot_contact.json | All token computation | ✓ if Phase 2 complete | Phase 2 output | Script exits non-zero (FileNotFoundError propagates) |
| chorus_transcripts.json | Activity history | ✓ if Phase 2 complete | Phase 2 output | Script exits non-zero |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `hubspot_contact.json` stores each property as `{"value": X, "updatedAt": Y}` (not flat strings) | Pattern 1, Pattern 4 | Freshness tier computation breaks; accessor code needs rewrite — algorithm unchanged |
| A2 | `hubspot_contact.json` has top-level `createdAt` key for contact create date | Pattern 5 | Falls back to `properties.createdate.value`; minor accessor fix |
| A3 | Email direction field is `"EMAIL"` for outbound and `"INCOMING_EMAIL"` for inbound | Pattern 6 | Outreach count may be wrong; fix requires changing filter value |
| A4 | `related_contacts` array has `hs_lead_status`, `hs_email_optout`, `notes_last_contacted` keys | Pattern 7 (TOK-01) | Secondary contact selection falls back gracefully via `.get()` defaults |
| A5 | Chorus transcript success format: `{"transcripts": [{...}]}`; sentinel: `{"transcript_available": false, ...}` | Pattern 2, Pattern 8 | Activity history assembly needs sentinel check update; low structural risk |
| A6 | HubSpot internal property names for external tokens: `industry_market_intelligence`, `company_observable_signals` | Pattern 11 | Empty tokens passed to Claude (non-fatal); property name must be confirmed with Phase 2 |
| A7 | `prompt_template.md` is in repo root; GitHub Actions cwd = repo root | Pattern 3, Pitfall 2 | FileSystemLoader path must be adjusted |

---

## Sources

### Primary (HIGH confidence)
- `C:\Users\irahfo\Outreach\Asotos\CLAUDE.md` — Data Freshness tiers, DLQ pattern, Retry Policy (canonical spec)
- `C:\Users\irahfo\Outreach\Asotos\staff_domain_ASOTOS_prompt.md` — Secondary contact selection logic (verbatim spec), Rule 4 name_of_target_role handling, 18-token inventory
- `C:\Users\irahfo\Outreach\Asotos\.planning\phases\01-scaffold-trigger\01-RESEARCH.md` — Token inventory table, Pattern 7 (Jinja2 StrictUndefined), library versions
- `C:\Users\irahfo\Outreach\Asotos\.planning\REQUIREMENTS.md` — TOK-01 through TOK-05, HUB-01 through HUB-08, Out of Scope section
- [Context7: /websites/jinja_palletsprojects_en_stable] — StrictUndefined raises UndefinedError on ALL operations including printing; dot notation `{{a.b}}` requires `a` as dict key in render context; `template.render(**dict)` unpacks top-level keys
- [VERIFIED locally: Python 3.14 + jinja2 3.1.6] — `template.render(contact={'first_name': 'John'})` works; missing attribute raises `UndefinedError 'dict object' has no attribute 'x'`; Python None renders as `"None"` not `"null"`

### Secondary (MEDIUM confidence)
- `C:\Users\irahfo\Outreach\Asotos\.planning\research\SUMMARY.md` — Data Honesty Rules enforcement, freshness tier application in code not just prompt
- `C:\Users\irahfo\Outreach\Asotos\.planning\research\PITFALLS.md` — PITFALL-C2 (silent token substitution), PITFALL-C3 (stale data as current)
- `C:\Users\irahfo\Outreach\Asotos\.planning\research\ARCHITECTURE.md` — compute_campaign_tokens.py data flow, RUNNER_TEMP file names, Phase 2 → Phase 3 data bus

### Tertiary (LOW confidence — assumptions)
- HubSpot v3 contact object structure (top-level `createdAt`, `properties.*.{value, updatedAt}` contract): inferred from HUB-01/HUB-08 requirements and HubSpot API documentation patterns; not directly verified since Phase 2 plans are TBD
- Chorus transcript object keys (`date`, `title`, `transcript`): inferred from Phase 2 requirements and Chorus v3/engagements endpoint inference; confirmed only at Phase 2 smoke test

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages; all in requirements.txt from Phase 1
- Jinja2 StrictUndefined pattern: HIGH — verified locally and via Context7 docs
- Freshness tier logic: HIGH — direct implementation of CLAUDE.md spec; verified with datetime arithmetic
- Secondary contact selection: HIGH — verbatim spec from prompt_template.md; sort logic verified locally
- Phase 2 output contract: LOW-MEDIUM — ASSUMED from HUB-01/HUB-08 requirements; confirmed only when Phase 2 plans are written
- Activity history format: MEDIUM — format is inferred from prompt context; exact Chorus transcript keys are ASSUMED

**Research date:** 2026-06-12
**Valid until:** 2026-09-12 (stable stack; re-verify after Phase 2 plans are written to confirm input contract)
