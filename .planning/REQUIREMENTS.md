# Requirements: ASOTOS Email Campaign Pipeline

**Defined:** 2026-06-12
**Core Value:** When triggered for a contact, produce and store 8 correctly personalised emails in HubSpot

## v1 Requirements

### Trigger

- [ ] **TRIG-01**: GitHub Actions `workflow_dispatch` accepts `contact_id` (HubSpot numeric ID) and `contact_email` as inputs, triggered by Make.com via GitHub API

### HubSpot Data Fetch

- [ ] **HUB-01**: Fetch contact properties including: `firstname`, `lastname`, `email`, `jobtitle`, `company`, `industry`, `numberofemployees`, `city`, `country`, `website`, `hubspot_owner_id`, `name_of_target_role`, `name_of_target_role_last_updated`, `company_locations`, `years_in_crm`, `outreach_attempt_count`, `related_contacts`, `secondary_contact_name`
- [ ] **HUB-02**: Fetch all email engagement history (past 12 months) with HTML stripped from bodies
- [ ] **HUB-03**: Fetch all meeting engagement history (past 12 months) with notes, internal notes, and attendees
- [ ] **HUB-04**: Fetch CRM meeting objects via v4 associations API (covers scheduler-created meetings)
- [ ] **HUB-05**: Fetch associated deals/opportunities (closed-won, closed-lost, open) for context in prompt
- [ ] **HUB-06**: Fetch related contacts at the same company to support secondary contact selection logic
- [ ] **HUB-07**: Resolve owner first names from `hubspot_owner_id` via owners API
- [ ] **HUB-08**: Every HubSpot field pulled must include its `updatedAt` timestamp for freshness tier calculation

### Chorus AI Fetch

- [ ] **CHO-01**: Extract Chorus conversation IDs from HubSpot meeting notes via regex (`chorus.ai/meeting/XXXXXXXX`); only fetch Chorus transcripts when IDs are found
- [ ] **CHO-02**: Silent fallback on 404 / 401 / timeout — pipeline continues with empty transcripts array; Chorus absence never causes pipeline failure

### Token Computation

- [ ] **TOK-01**: Apply secondary contact selection logic: peer-level seniority, prefer `hs_lead_status=CONNECTED`, prefer most recently contacted, exclude EAs/admins, exclude DNC/opted-out contacts
- [ ] **TOK-02**: Compute `contact.years_in_crm` from contact create date to today
- [ ] **TOK-03**: Compute `contact.outreach_attempt_count` from engagement history
- [ ] **TOK-04**: Assemble `crm.full_activity_history` string from emails + meetings + Chorus transcripts as labelled blocks
- [ ] **TOK-05**: Substitute all `{{token.name}}` placeholders in `prompt_template.md` using strict undefined mode — missing required tokens cause explicit pipeline failure (not silent empty substitution)

### Campaign Generation

- [ ] **GEN-01**: Call Claude API (`claude-sonnet-4-6`) with assembled prompt; `max_tokens=16000`; system prompt enforces Australian English, no em/en dashes as separators, raw JSON output only
- [ ] **GEN-02**: Validate JSON response against schema: `reasoning` block + `email_1` through `email_8` each with `subject` and `body` fields
- [ ] **GEN-03**: Check `stop_reason` — raise error if `max_tokens` (truncated response returns HTTP 200 but is invalid)
- [ ] **GEN-04**: On validation failure, retry with error message injected (up to 2 retries); DLQ on exhaustion
- [ ] **GEN-05**: Post-process output: strip em dashes and en dashes from all email bodies and subjects
- [ ] **GEN-06**: Write `campaign_output.json` to `$RUNNER_TEMP`

### HubSpot Write-Back

- [ ] **WRITE-01**: Write `subject_1`–`subject_8` as single-line text contact properties (overwrite on each run)
- [ ] **WRITE-02**: Write `email_1`–`email_8` as multi-line text contact properties (overwrite on each run, up to 65,000 chars each)
- [ ] **WRITE-03**: Write `asotos_generated_date` as date contact property
- [ ] **WRITE-04**: Create a new HubSpot note engagement on the contact with reasoning summary + email preview (uses CRM notes API `/crm/v3/objects/notes` with `associationTypeId: 202`; non-fatal if note creation fails)

### Reliability & Observability

- [ ] **REL-01**: All external API calls use tenacity exponential backoff retry — retryable: 429 and 5xx only; permanent failure on all other 4xx; max 6 attempts, 60s total cap
- [ ] **REL-02**: Anthropic SDK initialised with `max_retries=0` to prevent double-retry with tenacity
- [ ] **REL-03**: On unrecovered failure, each script writes a DLQ JSON record (`contact_id`, `contact_email`, `failed_step`, `error_message`, `timestamp`) to `$RUNNER_TEMP/failed_contacts.json`
- [ ] **REL-04**: On workflow failure: copy `failed_contacts.json` to workspace and upload as `failed-contacts` artifact; POST Teams webhook notification with contact_email, failed_step, error excerpt, and run log link
- [ ] **REL-05**: Upload `campaign_output.json` as `campaign-output-{contact_id}` artifact (7-day retention) on every successful run

### Project Structure

- [ ] **PROJ-01**: `prompt_template.md` contains the full ASOTOS campaign prompt (from `staff_domain_ASOTOS_prompt.md`) with `{{token.name}}` placeholders
- [ ] **PROJ-02**: `requirements.txt` uses exact version pins (`==`) for all 7 dependencies for reproducible CI
- [ ] **PROJ-03**: Shared retry/DLQ utilities extracted to `scripts/lib/` to avoid duplication across the 5 main scripts

## v2 Requirements

### Concurrency & Reliability

- **CONC-01**: Concurrency guard per `contact_id` in workflow (`concurrency: group: "asotos-{contact_id}"`) to prevent duplicate Make.com triggers
- **APPR-01**: Manual approval gate via GitHub Environments before HubSpot write-back (reviewer inspects artifact before any CRM write)

### Optimisation

- **OPT-01**: Prompt caching on static system prompt block (Anthropic `cache_control`) for ~90% cost reduction on repeated contacts
- **OPT-02**: Job cost summary step (token counts from API response → logged as step summary)

### Documentation

- **DOC-01**: Make.com HTTP module setup guide (endpoint, auth, input mapping, 204 acceptance configuration)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Email sending | HubSpot sequences handle delivery — not this pipeline's responsibility |
| Runtime web scraping for `company.observable_signals` / `industry.market_intelligence` | Avoids latency/rate limit risk; these tokens are pre-populated externally or passed as HubSpot properties |
| SDR call notes output | ASOTOS has no call component |
| Campaign analytics UI | HubSpot reporting covers this natively |
| Real-time streaming to clients | Batch job model; Make.com polls for completion |
| OAuth / SAML auth | API key/token secrets via GitHub Secrets are sufficient |
| Vector database / LangChain / agent frameworks | Unnecessary complexity for a single-contact batch job |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| TRIG-01 | Phase 1 | Pending |
| PROJ-01 | Phase 1 | Pending |
| PROJ-02 | Phase 1 | Pending |
| PROJ-03 | Phase 1 | Pending |
| HUB-01 | Phase 2 | Pending |
| HUB-02 | Phase 2 | Pending |
| HUB-03 | Phase 2 | Pending |
| HUB-04 | Phase 2 | Pending |
| HUB-05 | Phase 2 | Pending |
| HUB-06 | Phase 2 | Pending |
| HUB-07 | Phase 2 | Pending |
| HUB-08 | Phase 2 | Pending |
| CHO-01 | Phase 2 | Pending |
| CHO-02 | Phase 2 | Pending |
| TOK-01 | Phase 3 | Pending |
| TOK-02 | Phase 3 | Pending |
| TOK-03 | Phase 3 | Pending |
| TOK-04 | Phase 3 | Pending |
| TOK-05 | Phase 3 | Pending |
| GEN-01 | Phase 4 | Pending |
| GEN-02 | Phase 4 | Pending |
| GEN-03 | Phase 4 | Pending |
| GEN-04 | Phase 4 | Pending |
| GEN-05 | Phase 4 | Pending |
| GEN-06 | Phase 4 | Pending |
| WRITE-01 | Phase 5 | Pending |
| WRITE-02 | Phase 5 | Pending |
| WRITE-03 | Phase 5 | Pending |
| WRITE-04 | Phase 5 | Pending |
| REL-01 | Phase 6 | Pending |
| REL-02 | Phase 4 | Pending |
| REL-03 | Phase 6 | Pending |
| REL-04 | Phase 6 | Pending |
| REL-05 | Phase 6 | Pending |

**Coverage:**
- v1 requirements: 31 total
- Mapped to phases: 31
- Unmapped: 0 ✓

---
*Requirements defined: 2026-06-12*
*Last updated: 2026-06-12 after initial definition*
