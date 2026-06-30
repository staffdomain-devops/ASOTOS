# ASOTOS Campaign Pipeline — Roadmap

**Project:** ASOTOS Email Campaign Pipeline
**Milestone:** v1.0
**Total Phases:** 6
**Requirements Coverage:** 31/31 v1 requirements

## Overview

The pipeline is built in six sequential phases, each delivering a complete, independently testable capability. The order is driven by data flow: the workflow must exist before scripts can run; data must be fetched before it can be computed; tokens must be computed before Claude can be called; output must be validated before it is written to HubSpot; and failure handling wraps the entire system last. Phases 1–5 form the happy-path pipeline. Phase 6 adds the safety net that makes it production-ready.

## Phases

- [ ] **Phase 1: Scaffold + Trigger** — Workflow YAML, project structure, shared lib utilities, and prompt template; pipeline fires from GitHub UI
- [ ] **Phase 2: Data Fetch** — `fetch_hubspot.py` and `fetch_chorus.py` produce real contact data and transcripts in `$RUNNER_TEMP`
- [ ] **Phase 3: Token Computation** — `compute_campaign_tokens.py` derives all prompt tokens with freshness tiers and strict undefined validation
- [ ] **Phase 4: Campaign Generation** — `generate_campaign.py` calls Claude, validates output, post-processes, and writes `campaign_output.json`
- [ ] **Phase 5: HubSpot Write-Back** — `write_hubspot.py` writes 8 subjects, 8 email bodies, generated date, and a CRM note to HubSpot
- [ ] **Phase 6: Failure Handling + Observability** — DLQ records, failure artifacts, Teams webhook, and campaign artifact upload on success

## Phase Details

### Phase 1: Scaffold + Trigger
**Goal:** A runnable GitHub Actions workflow exists with correct structure, secrets, shared utilities, and the ASOTOS prompt template — ready to accept a Make.com trigger
**Depends on:** Nothing (first phase)
**Requirements:** TRIG-01, PROJ-01, PROJ-02, PROJ-03
**Success Criteria** (what must be TRUE):
  1. Manually triggering the workflow from the GitHub Actions UI with a `contact_id` and `contact_email` causes the job to start and complete without errors (even though data scripts are stubs)
  2. `prompt_template.md` exists in the repo root and contains all `{{token.name}}` placeholders from `staff_domain_ASOTOS_prompt.md`
  3. `scripts/lib/` contains `api_client.py`, `dlq_writer.py`, and `file_io.py` importable by all main scripts
  4. `requirements.txt` lists all 9 dependencies with exact `==` version pins and installs cleanly on `ubuntu-latest`
**Plans:** 2 plans

Plans:
- [ ] 01-01-PLAN.md — Workflow YAML, project layout, and requirements.txt
- [ ] 01-02-PLAN.md — Shared lib utilities and prompt template migration

### Phase 2: Data Fetch
**Goal:** Real HubSpot contact data (properties + engagement history + deals + owner names, all with `updatedAt` timestamps) and Chorus AI transcripts (or an explicit sentinel) are written to `$RUNNER_TEMP` as structured JSON files
**Depends on:** Phase 1
**Requirements:** HUB-01, HUB-02, HUB-03, HUB-04, HUB-05, HUB-06, HUB-07, HUB-08, CHO-01, CHO-02
**Success Criteria** (what must be TRUE):
  1. Running the workflow for a known test contact produces `hubspot_contact.json` in `$RUNNER_TEMP` containing all required properties, their values, and their `updatedAt` timestamps
  2. Meeting notes containing a Chorus URL cause `chorus_transcripts.json` to contain the fetched transcript; meeting notes without a Chorus URL produce the explicit sentinel `{"transcript_available": false}`
  3. A Chorus 404 or 401 does not fail the pipeline — the workflow step exits 0 with the sentinel written
  4. Engagement history counts in the output JSON match what is visible in the HubSpot contact timeline (spot-check)
**Plans:** 2 plans

Plans:
- [ ] 02-01-PLAN.md — `fetch_hubspot.py` — properties, history, deals, owners, updatedAt
- [ ] 02-02-PLAN.md — `fetch_chorus.py` — regex extraction, transcript fetch, sentinel fallback

### Phase 3: Token Computation
**Goal:** `compute_campaign_tokens.py` reads the raw fetch outputs and produces a validated `campaign_tokens.json` containing every token the prompt requires, with freshness tiers applied and strict undefined checking enforced
**Depends on:** Phase 2
**Requirements:** TOK-01, TOK-02, TOK-03, TOK-04, TOK-05
**Success Criteria** (what must be TRUE):
  1. `campaign_tokens.json` is written with secondary contact correctly selected (peer seniority, recency, excludes EAs/DNC) when related contacts exist; falls back gracefully when none qualify
  2. `contact.years_in_crm` and `contact.outreach_attempt_count` are computed correctly against known test contact values
  3. `crm.full_activity_history` contains labelled blocks for emails, meetings, and Chorus transcripts in the correct format
  4. Running the script with a deliberately missing required token causes the pipeline step to exit non-zero with an explicit error message identifying the missing token (StrictUndefined enforced)
**Plans:** 1 plan

Plans:
- [ ] 03-01-PLAN.md — `compute_campaign_tokens.py` — secondary contact, years in CRM, outreach count, activity history, StrictUndefined substitution

### Phase 4: Campaign Generation
**Goal:** `generate_campaign.py` assembles the populated prompt, calls `claude-sonnet-4-6`, validates the JSON response against schema, checks `stop_reason`, post-processes em/en dashes, and writes `campaign_output.json` — rejecting and retrying on any validation failure
**Depends on:** Phase 3
**Requirements:** GEN-01, GEN-02, GEN-03, GEN-04, GEN-05, GEN-06, REL-02
**Success Criteria** (what must be TRUE):
  1. A successful end-to-end run produces `campaign_output.json` containing a `reasoning` block and exactly 8 email objects each with `subject` and `body` fields
  2. Deliberately returning a truncated response (by temporarily setting `max_tokens` very low) causes the step to fail with an explicit `stop_reason: max_tokens` error, not a silent JSON parse error
  3. A schema-invalid Claude response triggers up to 2 retries with the error injected; after exhaustion the step exits non-zero and a DLQ record is written
  4. No em dashes or en dashes appear in any `subject` or `body` field of the output JSON
**Plans:** TBD

Plans:
- [ ] 04-01: `generate_campaign.py` — prompt assembly, Claude call, Pydantic validation, stop_reason check, retry logic, em-dash post-processing
**UI hint**: no

### Phase 5: HubSpot Write-Back
**Goal:** `write_hubspot.py` reads `campaign_output.json` and writes all 8 subjects, 8 email bodies, `asotos_generated_date`, and a CRM note to the HubSpot contact — completing the end-to-end pipeline
**Depends on:** Phase 4
**Requirements:** WRITE-01, WRITE-02, WRITE-03, WRITE-04
**Success Criteria** (what must be TRUE):
  1. After a full pipeline run, the HubSpot contact record shows `subject_1` through `subject_8` and `email_1` through `email_8` properties populated with the generated content
  2. `asotos_generated_date` is set on the contact record to today's date
  3. A new note appears on the HubSpot contact timeline containing the reasoning summary and an email preview
  4. A note creation failure (e.g., missing notes API scope) logs a warning but does not fail the workflow step — the property writes succeed regardless
**Plans:** TBD

Plans:
- [ ] 05-01: `write_hubspot.py` — atomic PATCH for properties, POST note with contact association, non-fatal note error handling

### Phase 6: Failure Handling + Observability
**Goal:** Every failure mode is captured and surfaced — failed contacts are written to a DLQ artifact, Teams is notified with actionable context, and successful runs upload the campaign output as a retained artifact
**Depends on:** Phase 5
**Requirements:** REL-01, REL-03, REL-04, REL-05
**Success Criteria** (what must be TRUE):
  1. Intentionally breaking `fetch_chorus.py` (e.g., bad endpoint) causes the workflow to upload a `failed-contacts` artifact containing a JSON record with `contact_id`, `contact_email`, `failed_step`, `error_message`, and `timestamp`
  2. The same failure triggers a Teams webhook notification containing the contact email, failed step name, error excerpt, and a direct link to the run log
  3. A successful pipeline run produces a `campaign-output-{contact_id}` artifact visible in the GitHub Actions run summary, retained for 7 days
  4. All external API calls across all scripts (HubSpot, Chorus, Claude) only retry on 429 and 5xx responses; a 401 fails immediately without retrying
**Plans:** TBD

Plans:
- [ ] 06-01: Tenacity retry config, DLQ writer integration across all scripts, failure artifact upload, Teams notification, success artifact upload

## Dependency Map

Phase 1 must complete before any other phase — it provides the workflow definition, shared lib modules, and `requirements.txt` that all scripts depend on.

Phase 2 must complete before Phase 3 — token computation reads the JSON files written by the fetch scripts.

Phase 3 must complete before Phase 4 — campaign generation requires a fully validated `campaign_tokens.json`.

Phase 4 must complete before Phase 5 — the write-back script reads `campaign_output.json`.

Phase 6 wraps all prior phases — the DLQ writer, artifact upload, and Teams notification are added to scripts and the workflow YAML after the happy path is confirmed end-to-end in Phase 5. REL-01 (retry policy) is implemented in `lib/api_client.py` during Phase 1 scaffold but exercised in every subsequent phase.

## Progress

**Execution Order:** 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Scaffold + Trigger | 0/2 | Not started | - |
| 2. Data Fetch | 0/2 | Not started | - |
| 3. Token Computation | 0/1 | Not started | - |
| 4. Campaign Generation | 0/1 | Not started | - |
| 5. HubSpot Write-Back | 0/1 | Not started | - |
| 6. Failure Handling + Observability | 0/1 | Not started | - |
