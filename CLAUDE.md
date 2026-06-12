# ASOTOS Email Campaign Pipeline

## Project Overview

GitHub Actions pipeline that auto-generates 8 personalised re-engagement emails for Staff Domain's cold prospects via Claude API, pulling from HubSpot + Chorus AI, writing results back to HubSpot.

**Trigger:** Make.com detects contact added to HubSpot list → fires `workflow_dispatch` with `contact_id` + `contact_email`
**Stack:** Python 3.12, GitHub Actions ubuntu-latest, Anthropic SDK, HubSpot API client, Chorus AI REST
**Repo:** https://github.com/staffdomain-devops/ASOTOS

## File Structure

```
Asotos/
├── scripts/
│   ├── lib/              ← shared retry + DLQ utilities
│   ├── fetch_hubspot.py
│   ├── fetch_chorus.py
│   ├── compute_campaign_tokens.py
│   ├── generate_campaign.py
│   └── write_hubspot.py
├── prompt_template.md    ← ASOTOS campaign prompt ({{token.name}} syntax)
├── requirements.txt      ← exact version pins (==)
├── CLAUDE.md
└── .planning/

.github/
└── workflows/
    └── campaign.yml
```

## GSD Workflow

This project uses GSD (Get Shit Done) for planning and execution.

**Current State:** `.planning/STATE.md`
**Roadmap:** `.planning/ROADMAP.md`
**Requirements:** `.planning/REQUIREMENTS.md`

### Phase Commands

```
/gsd-discuss-phase <N>   — gather context before planning
/gsd-plan-phase <N>      — create execution plan
/gsd-execute-phase <N>   — execute the plan
/gsd-verify-work <N>     — verify phase requirements met
/gsd-progress            — check current status
```

## Critical Implementation Rules

### Anthropic SDK
- Always initialize with `max_retries=0` — prevents double-retry with tenacity: `Anthropic(api_key=..., max_retries=0)`
- Check `stop_reason` on every response — `max_tokens` returns HTTP 200 but is an incomplete/invalid output
- `max_tokens=16000` for the 8-email generation call

### Retry Policy (tenacity)
- Retry ONLY on: 429, 5xx, connection errors
- Fail immediately (no retry) on: 400, 401, 403, 422, other 4xx
- Config: `stop_after_attempt(6)`, `stop_after_delay(60)`, `wait_random_exponential(min=1, max=60)`
- HubSpot SDK: check `e.status` on `ApiException`
- requests: check `e.response.status_code` on `HTTPError`

### Template Substitution
- Use Jinja2 with `StrictUndefined` — missing tokens must raise explicitly, never substitute empty string
- Pre-render: validate all required tokens present before calling Claude

### Data Freshness (from ASOTOS prompt rules)
- Every HubSpot field pulled must include `updatedAt` timestamp
- Freshness tiers: <60 days = "recently", 60d-6m = past observation, 6-12m = "earlier this year", 12m+ = drop or reframe
- `name_of_target_role` handling: see `staff_domain_ASOTOS_prompt.md` Rule 4

### HubSpot Notes
- Use CRM notes API: `POST /crm/v3/objects/notes` with `hs_note_body`
- Associate to contact: `associationTypeId: 202`
- Note creation is NON-FATAL — log warning and continue if it fails

### Chorus AI
- Auth header: raw token (no `Bearer` prefix): `Authorization: Token XXXXXXXX`
- Only fetch if Chorus IDs found in meeting notes (regex: `chorus.ai/meeting/(\w+)`)
- Silent fallback on 404/401/timeout — write empty array to `chorus_transcripts.json`

### DLQ Pattern
Every script writes this on unrecovered failure before re-raising:
```python
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

## HubSpot Properties Required (create before first run)

| Property Name | Type |
|---|---|
| `subject_1` through `subject_8` | Single-line text |
| `email_1` through `email_8` | Multi-line text |
| `asotos_generated_date` | Date |

## GitHub Secrets Required

- `HUBSPOT_API_KEY` — Private App token (contacts read/write, engagements read/write, owners read, notes write)
- `CHORUS_API_TOKEN` — Chorus token (format: `Token XXXXXXXX`)
- `ANTHROPIC_API_KEY` — Anthropic API key
- `TEAMS_WEBHOOK_URL` — Microsoft Teams incoming webhook URL

## Known Risks (from research)

1. **Make.com 204 handling** — Make.com HTTP module treats GitHub's 204 response as failure by default; configure the module to accept 204 as success to prevent duplicate triggers
2. **HubSpot notes scope** — `crm.objects.notes.write` may not appear in Private App UI on some HubSpot portal tiers; verify before Phase 5
3. **Chorus endpoint uncertainty** — Official API docs inaccessible; validate auth header format and endpoint path in Phase 2 smoke test
4. **Claude JSON truncation** — `stop_reason: max_tokens` returns HTTP 200; always check before parsing
