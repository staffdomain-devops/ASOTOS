# ASOTOS Email Campaign Pipeline

## What This Is

A GitHub Actions pipeline that auto-generates personalised 8-email re-engagement sequences for Staff Domain's cold prospects (180+ days silent). Triggered by Make.com when a contact enters a HubSpot list, it fetches contact data from HubSpot and call transcripts from Chorus AI, builds a prompt, calls Claude API, and writes the 8 generated emails back to HubSpot contact properties — ready to send via HubSpot sequences.

## Core Value

When triggered for a contact, the pipeline must produce and store 8 correctly personalised emails in HubSpot — the Make.com trigger, enrichment, and AI generation all exist to serve this outcome.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] GitHub Actions workflow triggers on `workflow_dispatch` with `contact_id` and `contact_email` inputs from Make.com
- [ ] `fetch_hubspot.py` fetches contact properties, email/meeting history, deal stage, owner names, and related contacts
- [ ] `fetch_chorus.py` fetches Chorus AI transcripts by conversation ID extracted from meeting notes (silent fallback on missing IDs)
- [ ] `compute_campaign_tokens.py` calculates: secondary contact selection, years in CRM, outreach attempt count
- [ ] `generate_campaign.py` builds prompt from `prompt_template.md`, calls Claude API, returns 8 emails + reasoning as JSON
- [ ] `write_hubspot.py` writes `email_1`–`email_8` + `subject_1`–`subject_8` to HubSpot contact properties and creates a note
- [ ] All scripts implement exponential backoff retry (tenacity) on 429/5xx; write DLQ record on unrecovered failure
- [ ] On workflow failure: upload `failed_contacts.json` artifact and POST Teams webhook notification
- [ ] Campaign output uploaded as GitHub Actions artifact (7-day retention)
- [ ] `prompt_template.md` is the ASOTOS prompt with `{{token.name}}` placeholders substituted at runtime

### Out of Scope

- Email sending — HubSpot sequences handle delivery, not this pipeline
- Web scraping for company signals at runtime — `company.observable_signals` and `industry.market_intelligence` are tokens pre-populated externally or passed as HubSpot properties
- Real-time webhook inbound — Make.com polls HubSpot and calls the GitHub API
- Campaign analytics UI — HubSpot reporting covers this
- SDR call notes — ASOTOS has no call component
- OAuth / SAML authentication — API key/token secrets used throughout

## Context

**Company**: Staff Domain — Australian-owned offshore staffing (Philippines / South Africa). ISO 27001 certified. NOT a recruitment firm or BPO — dedicated offshore teams.

**Campaign**: ASOTOS is an 8-email re-engagement sequence over 50 days targeting prospects silent for 180+ days. The prompt enforces Data Honesty Rules (STATE A/B/C conversation classification, data freshness tiers, no invented conversation detail).

**Trigger architecture**: Make.com scenario watches a HubSpot list → fires `workflow_dispatch` to GitHub Actions with `contact_id` + `contact_email`.

**Data flow**: HubSpot contact data + Chorus transcripts → temp JSON files via `$RUNNER_TEMP` → prompt assembly → Claude API → output JSON → HubSpot write-back.

**AI model**: `claude-sonnet-4-6`. System prompt enforces Australian English, no em/en dashes as separators, raw JSON output only.

**Key difference from the rebuild-prompt template**: 8 emails (not 7), no SDR call notes output, additional HubSpot token fields for contact relationship context (`years_in_crm`, `outreach_attempt_count`, `secondary_contact_name`, `related_contacts`, `name_of_target_role` with freshness date).

## Constraints

- **Tech stack**: Python 3.12, GitHub Actions ubuntu-latest
- **Dependencies**: `hubspot-api-client>=12.0.0`, `requests>=2.31.0`, `beautifulsoup4>=4.12.0`, `anthropic>=0.30.0`, `tiktoken>=0.7.0`, `tenacity>=9.0.0`
- **Secrets required**: `HUBSPOT_API_KEY`, `CHORUS_API_TOKEN`, `ANTHROPIC_API_KEY`, `TEAMS_WEBHOOK_URL`
- **HubSpot properties**: Custom properties for `email_1`–`email_8`, `subject_1`–`subject_8`, `asotos_generated_date` must be created before first run
- **Retry policy**: Retry on 429 + 5xx only; max 6 attempts, 60s cap; pass `max_retries=0` to Anthropic SDK before applying tenacity
- **Prompt template**: `staff_domain_ASOTOS_prompt.md` → becomes `prompt_template.md` in the project; uses `{{token.name}}` syntax

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Use `$RUNNER_TEMP` for inter-step data | Isolates secrets from workspace, avoids git commit of contact data | — Pending |
| `industry.market_intelligence` and `company.observable_signals` as pre-populated tokens | Avoids runtime web scraping latency/rate limits; simpler pipeline | — Pending |
| Secondary contact selection computed in `compute_campaign_tokens.py` | Keeps `fetch_hubspot.py` focused on raw data retrieval | — Pending |
| 8 email output (not 7) | ASOTOS is a 50-day / 8-touch sequence per campaign spec | — Pending |
| No SDR notes in output | ASOTOS has no call component; no SDR notes needed | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-12 after initialization*
