# Research Summary: ASOTOS GitHub Actions AI Email Campaign Pipeline

**Project:** ASOTOS -- 8-email re-engagement campaign for Staff Domain (Australian B2B offshore staffing)
**Synthesized:** 2026-06-12
**Research confidence:** HIGH across stack and architecture; MEDIUM on Chorus AI specifics

---

## Executive Summary

ASOTOS is a linear, single-job GitHub Actions pipeline triggered on-demand by Make.com via workflow_dispatch. It fetches a HubSpot contact and Chorus AI call transcripts, computes personalisation tokens, calls Claude to generate all 8 re-engagement emails in a single API call, and writes output back to HubSpot as CRM notes and contact properties. The pipeline processes one contact per run.

The research strongly validates the chosen stack. Every library version is current and pinned. The architecture -- five Python scripts sharing state through RUNNER_TEMP JSON files -- is correct for a sequential single-contact pipeline with no parallelism requirements. There is no case for async, microservices, queues, or LLM orchestration frameworks at this scale. The prompt caching constraint (static system prompt, dynamic user message) aligns naturally with the personalisation model.

The highest-risk areas are data integrity, not technology. Stale HubSpot data passed as current context, silent Jinja2 token substitution, and unvalidated LLM output written to the CRM are plausible failure modes that produce professionally damaging emails. These must be designed out from the first end-to-end run.

---

## 1. Recommended Stack

All versions confirmed via PyPI JSON API on 2026-06-12. Pin exactly -- ephemeral runners silently upgrade on floor specs (>=).

| Package | Pinned Version | Role |
|---|---|---|
| hubspot-api-client | ==12.0.0 | HubSpot CRM read/write (v3 Notes API, v4 Associations) |
| anthropic | ==0.109.1 | Claude API client; set max_retries=0 to let tenacity own all retry |
| requests | ==2.34.2 | Chorus AI REST calls only (HubSpot SDK has its own HTTP transport) |
| beautifulsoup4 | ==4.15.0 | Strip HTML from HubSpot rich-text fields before prompt injection |
| lxml | latest stable | Faster bs4 parser backend; add alongside bs4 |
| tiktoken | ==0.13.0 | Token pre-count using cl100k_base (~10% conservative budget margin) |
| tenacity | ==9.1.4 | Unified retry orchestration across all three APIs |

**Model:** claude-sonnet-4-6 (stable dateless ID confirmed; no date suffix required)
**max_tokens:** 16000 (3x headroom for 8 emails at 300-500 words each)
**GitHub Actions:** ubuntu-latest, actions/upload-artifact@v4, actions/checkout@v4, actions/setup-python@v5

Key tooling exclusions:
- No httpx, asyncio, or LangChain: sequential pipeline gains nothing from them
- No LangSmith or Helicone: git-versioned .j2 templates replace prompt management SaaS at zero cost
- No streaming: complete JSON needed before CRM write; streaming adds complexity with no benefit
- HubSpot Private App token only: legacy API key and OAuth both excluded

---

## 2. Table Stakes Features

These must ship in the first working end-to-end run. Missing any one creates data corruption or silent failure risk.

**Pipeline reliability:**
- Per-contact idempotency guard via asotos_last_run_id HubSpot property: prevents double-writes on re-run
- Exponential backoff with explicit retryable-only predicates via tenacity with reraise=True
- Dead-letter JSON at RUNNER_TEMP/failed_contacts.json: failed contacts captured, not silently skipped
- workflow_dispatch inputs: contact_id (required), campaign_id (optional, default ASOTOS)
- Concurrency group scoped to contact_id with cancel-in-progress: false: prevents duplicate runs
- Debug artifact upload on failure: all RUNNER_TEMP/*.json retained 14 days
- Teams notification on failure: silent failures unacceptable on a production CRM pipeline

**AI quality:**
- Pydantic JSON schema validation on every LLM response: malformed output never reaches HubSpot
- stop_reason check on every Claude response: max_tokens result treated as hard failure
- All 8 email briefs in one system prompt, all 8 emails in one API call per contact (8N calls reduced to N)
- Post-generation cross-reference validator: every factual claim must trace to input data

**CRM integration:**
- Write emails to HubSpot as Notes (append-only, timeline-visible), not contact properties (overwrite)
- Write status flag: asotos_sequence_status (enum: generated, approved, sent)
- Note associated to BOTH contact (associationTypeId: 202) AND company (associationTypeId: 190)
- 180-day silence filter on contact pull
- Always fetch updatedAt alongside property values; attach field age in human-readable form to Claude context

**Template management:**
- Jinja2 StrictUndefined: missing tokens raise UndefinedError, never silently produce empty string
- Pre-render manifest validation: every required token present and non-empty before prompt submission
- Static/dynamic split: static content in system prompt only, dynamic content in user message only

---
## 3. Architecture Pattern

**Pattern:** Single-job sequential pipeline with RUNNER_TEMP as inter-step data bus.

Trigger chain:

  HubSpot contact enrollment
    -> Make.com HTTP module (POST to GitHub dispatch API)
      -> workflow_dispatch fires with contact_id + campaign_id
        -> Job: generate-campaign (single job, five steps)
            fetch_hubspot.py           -> RUNNER_TEMP/hubspot_contact.json
            fetch_chorus.py            -> RUNNER_TEMP/chorus_transcripts.json
            compute_campaign_tokens.py -> RUNNER_TEMP/campaign_tokens.json
            generate_campaign.py       -> RUNNER_TEMP/campaign_output.json
            write_hubspot.py           -> HubSpot CRM (notes + properties)
        -> if failure: upload RUNNER_TEMP artifacts + Teams notification

**Why single job:** Steps share RUNNER_TEMP. Multiple jobs require artifact upload/download -- adding latency and failure surfaces with no benefit for a sequential pipeline.

**Secrets wiring:** All secrets and dispatch inputs at jobs.<job_id>.env level. Single declaration, all scripts inherit, no repetition.

**Shared lib/ modules:**
- lib/api_client.py -- authenticated HTTP client with tenacity retry decorators for all three APIs
- lib/dlq_writer.py -- structured failure record writer (contact_id, failed_step, error, run_url, timestamp)
- lib/file_io.py -- typed RUNNER_TEMP path construction and JSON read/write helpers

**Data flow:**
- fetch_hubspot.py: contact properties + updatedAt timestamps + exhaustive cursor-paginated engagement history + owner info
- fetch_chorus.py: transcripts array OR explicit sentinel {transcript_available: false, transcript_status: unavailable_404} -- never empty string
- compute_campaign_tokens.py: Data Honesty Rules, STATE classification (A/B/C), freshness tiers; pure computation, no external API calls
- generate_campaign.py: static cached system prompt + dynamic user message, Claude API, Pydantic validation, stop_reason check, cross-reference validator
- write_hubspot.py: single atomic PATCH for all properties + POST notes to contact and company

**Build order:** Phase 1 Scaffold -> Phase 2 Data fetch -> Phase 3 Token computation -> Phase 4 Generation -> Phase 5 Write-back -> Phase 6 Failure handling

---

## 4. Top Pitfalls to Avoid

Ordered by severity. Prevention required before first production run.

**PITFALL-C1 (CRITICAL): Unvalidated LLM output written to HubSpot**
Claude can hallucinate factual claims while returning schema-valid JSON. HTTP 200 from HubSpot does not signal content correctness. Once written with overwrite semantics, the correct prior value is permanently gone.
Prevention: post-generation cross-reference validator checking every factual claim against source data. If Chorus transcript was absent and output contains transcript-derived claims, reject. Write only after validation passes.

**PITFALL-C2 (CRITICAL): Silent Jinja2 token substitution**
Missing or null HubSpot properties silently produce blank tokens unless Jinja2 StrictUndefined is set. The LLM may fabricate a plausible value, creating a hallucination disguised as a data problem.
Prevention: StrictUndefined mode plus a pre-render manifest asserting every required token is present and non-empty.

**PITFALL-C3 (CRITICAL): Stale CRM data presented as current**
HubSpot properties fetched without checking updatedAt. A 14-month-old last_meeting_date passes to Claude as current-tense context, producing emails that reference meetings as recent when they are not.
Prevention: always fetch updatedAt alongside property values; attach field age to Claude context in human-readable form; enforce freshness tiers in pre-prompt assembly code, not only in prompt instructions.

**PITFALL-C4 (CRITICAL): Tenacity retrying permanent errors**
Default tenacity configs retry ALL exceptions including 401 (bad API key), 400, and 422. A 401 retried 6 times wastes calls and may flag the integration.
Prevention: explicit retryable allowlist (429, 529, 500/502/503/504 only); fail-fast on all 4xx permanent errors.

**PITFALL-M2 (HIGH): Silent wrong state classification when Chorus transcript missing**
Passing empty string when Chorus returns 404/401 causes Claude to hallucinate call summaries. A Chorus 401 means token expired for all contacts in the run.
Prevention: explicit sentinel {transcript_available: false}. Prompt must conditionally exclude transcript-derived claims. Chorus 401 = run-level circuit breaker, not per-contact fallback.

**PITFALL-M3 (HIGH): max_tokens truncation produces invalid structured output**
Claude hitting max_tokens returns stop_reason max_tokens. Truncated JSON may still parse and pass schema validation. Error goes undetected without explicit check.
Prevention: always check stop_reason; treat anything other than end_turn as hard failure. Truncate Chorus transcripts to token budget BEFORE sending.

**PITFALL-M6 (HIGH): Make.com duplicate trigger on GitHub 204 response**
GitHub workflow_dispatch returns HTTP 204, not 200. Make.com configured for 200 treats 204 as failure and retries, triggering two runs for the same contact.
Prevention: explicitly accept 204 as success in Make.com HTTP module. Concurrency group with cancel-in-progress: false as safety net.

---
## 5. Key Decisions Made

test content
## 5. Key Decisions Made

test line
## 5. Key Decisions Made

Research-driven decisions that constrain the design. Do not revisit without strong cause.

| Decision | Rationale | Confidence |
|---|---|---|
| Single job, five sequential steps | Steps share RUNNER_TEMP; no parallelism opportunity; multi-job adds artifact overhead | HIGH |
| All 8 emails in one Claude call | Reduces calls from 8N to N; 8 briefs in cacheable system prompt | HIGH |
| RUNNER_TEMP as inter-step data bus | Scoped to job, auto-cleaned, not git-contaminated; GITHUB_OUTPUT is string-only | HIGH |
| Secrets at job-level env, not step-level | Single declaration, all scripts inherit, prevents repetition | HIGH |
| max_retries=0 on Anthropic() client | SDK DEFAULT_MAX_RETRIES=2; without disabling, tenacity causes up to 12 actual API calls | HIGH |
| Notes for email bodies, properties for flags | Notes append-only and timeline-visible; properties filterable | HIGH |
| Static/dynamic prompt split in templates | Cache key is exact prefix match; dynamic data in system block = 0% cache hit rate | HIGH |
| HubSpot Private App token only | Legacy API key deprecated; OAuth unnecessary for server-to-server | HIGH |
| Chorus 404/401 = explicit sentinel, not empty string | Empty string causes Claude to hallucinate call summaries | HIGH |
| Chorus 401 = run-level circuit breaker | Token expired for all contacts; per-contact fallback masks credential failure | MEDIUM |
| Note associated to contact AND company | B2B account visibility: contact-only notes invisible on company record | HIGH |
| hs_timestamp in epoch milliseconds as string | Wrong timestamp puts notes at bottom of timeline | HIGH |

---

## 6. Open Questions

Items requiring validation or decisions before or during execution.

| Question | Risk if wrong | When to resolve |
|---|---|---|
| Does HubSpot portal have crm.objects.notes.write scope in Private App picker? Some tiers hide it. | Cannot write campaign notes to CRM | Before Phase 1 ends |
| Confirm associationTypeId 202 for note-to-contact in this portal | Notes not linked to contact | Phase 5: call GET /crm/v4/associations/notes/contacts/labels |
| Chorus auth header format: raw token or Bearer prefix? | All Chorus calls return 401 | Phase 2: validate against GET https://chorus.ai/api/v1/me |
| Chorus transcript endpoint /v3/engagements/{id} still current post-ZoomInfo acquisition? | No transcripts retrieved | Phase 2: smoke test with known conversation ID |
| Exact HubSpot property for 180-day filter (lastmodifieddate vs notes_last_updated vs custom)? | Wrong contacts selected or excluded | Phase 2: inspect contact schema in portal |
| Does STATE classification (A/B/C) need stakeholder sign-off before first run? | Wrong sequence sent to real prospects | Before Phase 4 |
| Make.com HTTP module: accepts 2xx broadly or requires explicit 204 config? | Duplicate triggers on every Make.com fire | Phase 1: test against staging |
| Manual approval gate (GitHub Environment with required reviewers) in scope for v1? | AI content writes to live CRM without human review | Decision before Phase 5 build |

---

## 7. Phase Implications

### Phase 1: Scaffold + Trigger Plumbing
**Deliver:** Workflow fires from Make.com, runner boots, secrets accessible, shared lib foundation exists.
**Build:** Workflow YAML with workflow_dispatch, concurrency group, job-level env; requirements.txt; lib/file_io.py, lib/api_client.py (placeholder impls), lib/dlq_writer.py.
**Pitfalls:** M6 (concurrency group in YAML from day one), M7 (never log os.environ or full exception objects), L1 (coerce boolean inputs explicitly).
**Validation:** Test Make.com accepts 204 as success. Confirm secrets resolve in runner.

### Phase 2: Data Fetch Scripts
**Deliver:** Real contact and transcript data in RUNNER_TEMP with updatedAt on every property.
**Build:** fetch_hubspot.py (properties + updatedAt + exhaustive cursor pagination: while paging.next.after loop); fetch_chorus.py (explicit sentinel on 404/401, circuit breaker on 401).
**Pitfalls:** C3 (always fetch updatedAt), M1 (exhaust cursor pagination), M2 (sentinel not empty string), L3 (named-capture regex for Chorus URL).
**Validation:** Confirm Chorus auth header format. Confirm 180-day filter property name. Cross-reference engagement counts vs HubSpot UI.

### Phase 3: Token Computation
**Deliver:** Structured campaign_tokens.json ready for prompt assembly; business logic isolated and unit-testable without external API calls.
**Build:** compute_campaign_tokens.py with STATE classification, freshness tiers from updatedAt values, Data Honesty Rules enforcement.
**Pitfalls:** C3 (freshness tiers enforced in code, not only in prompt text).
**Validation:** Review STATE classification logic with campaign owner before Phase 4.

### Phase 4: Campaign Generation
**Deliver:** Claude produces validated 8-email JSON written to campaign_output.json.
**Build:** generate_campaign.py with Jinja2 StrictUndefined templates (system/user split), tiktoken budget pre-check, Claude call with cache_control on system block, Pydantic validation, stop_reason check, cross-reference validator.
**Pitfalls:** C1 (cross-reference validator required), C2 (StrictUndefined + manifest), C4 (explicit retry allowlist, max_retries=0), M3 (stop_reason check), M4 (no dynamic data in cached system prefix).
**Research flag:** Well-documented patterns; no additional research needed.

### Phase 5: HubSpot Write-Back
**Deliver:** Pipeline completes end-to-end with notes and properties in HubSpot.
**Build:** write_hubspot.py with single atomic PATCH, POST notes with contact + company associations, status property update (asotos_sequence_status = generated), idempotency guard.
**Pitfalls:** C1 (write only after validation passes), M5 (single atomic PATCH; 4xx = hard failure with full error body logged; validate enum values against property schema manifest).
**Validation:** Confirm associationTypeId 202 and notes scope. Test on single non-production contact first.

### Phase 6: Failure Handling + Observability
**Deliver:** Pipeline fails safely, artifacts persist, Teams notification fires, job summary shows token cost in AUD.
**Build:** if-failure artifact upload (14-day retention), Teams notification via actions/github-script@v7, token cost table to GITHUB_STEP_SUMMARY, run manifest JSON.
**Pitfalls:** M7 (sanitize exceptions -- never log full Anthropic APIError which contains x-api-key header), L4 (use tempfile.mkdtemp + finally cleanup).
**Validation:** Intentionally break fetch_chorus.py; confirm artifact upload + Teams notification fire.

---

## Confidence Assessment

| Area | Confidence | Notes |
|---|---|---|
| Python library versions | HIGH | Confirmed via PyPI JSON API on 2026-06-12 |
| GitHub Actions patterns | HIGH | Official docs confirmed; all key patterns verified |
| Claude API: model ID, caching, output validation | HIGH | Official Anthropic docs confirmed |
| HubSpot SDK auth + notes write pattern | HIGH | SDK README and official HubSpot developer docs |
| HubSpot note association type ID 202 | MEDIUM | Community sources; verify via GET /crm/v4/associations/notes/contacts/labels |
| HubSpot crm.objects.notes scope availability | MEDIUM | May be hidden in UI on some subscription tiers |
| Chorus AI auth header format | MEDIUM | Third-party connector docs only; validate on first run |
| Chorus AI transcript endpoint | LOW-MEDIUM | Inferred from community references; official docs inaccessible |
| Chorus AI rate limits | LOW | Not found in any public source; conservative tenacity is the only mitigation |

**Overall: HIGH** -- Architecture, stack, and build order are well-grounded. Chorus endpoint and HubSpot scope availability need first-run validation, with explicit checkpoints in Phase 2 and Phase 5.

---

## Sources (Aggregated)

**Official documentation (HIGH confidence):**
- Anthropic model overview, prompt caching, structured outputs: platform.claude.com
- GitHub Actions concurrency, secrets, artifacts, workflow dispatch, inputs context: docs.github.com
- HubSpot CRM Notes v3 API, Properties API, usage guidelines, deprecation changelog: developers.hubspot.com
- Anthropic Python SDK source (DEFAULT_MAX_RETRIES=2 confirmed): Context7
- Tenacity documentation: tenacity.readthedocs.io
- GitHub Changelog: workflow dispatch API returns run IDs (Feb 2026)
- actions/upload-artifact README: github.com/actions/upload-artifact

**Community-verified (MEDIUM confidence):**
- HubSpot note-to-contact association type 202 pattern: HubSpot Community
- HubSpot cursor pagination out-of-order bug: HubSpot Community
- Chorus auth header format (raw token, no Bearer prefix): Nexla connector docs (third-party)
- Jinja2 StrictUndefined pattern: alexwlchan.net
- GitHub Actions boolean input coercion: GitHub Community discussions
- LLM output validation patterns: Pydantic AI, Xebia, Glukhov.org
- GitHub Actions secrets masking limitations: karimrahal.com

**Inferred / unverified (LOW-MEDIUM confidence):**
- Chorus transcript endpoint /v3/engagements/: community references; official Chorus docs inaccessible
- Chorus rate limits: not found in any public source
