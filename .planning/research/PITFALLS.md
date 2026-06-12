# Domain Pitfalls: GitHub Actions + LLM + CRM Email Campaign Pipeline

**Domain:** GitHub Actions automated email generation pipeline (Python, HubSpot, Chorus AI, Anthropic Claude, Make.com)
**Project:** ASOTOS for Staff Domain — 8-email re-engagement campaign
**Researched:** 2026-06-12
**Confidence:** HIGH (all major claims verified against official docs or multiple credible sources)

---

## Critical Pitfalls

Mistakes that cause rewrites, silent data corruption, or damaged client relationships.

---

### PITFALL-C1: LLM Output Written to HubSpot Without Validation Gate

**Severity:** HIGH
**Risk area:** LLM Output Validation

**What goes wrong:** Claude returns a structurally valid JSON object (schema-compliant due to constrained decoding) but the *content* is wrong — hallucinated job titles, fabricated call dates, invented topics. This writes confidently incorrect facts to HubSpot contact properties. Because the write succeeds with HTTP 200, nothing in the pipeline signals failure.

**Why it happens:** Anthropic's Structured Outputs guarantee format compliance, not factual accuracy. The official docs state explicitly: "The models can and may still hallucinate occasionally, so you might get perfectly formatted incorrect answers." Schema enforcement at the token level does not prevent the model from inventing data it was never given.

**Consequences:**
- Personalized email references a call that never happened or occurred years ago
- State classification (A/B/C) is wrong, sending wrong message sequence to real prospect
- Once written to HubSpot with overwrite semantics, the correct prior value is permanently gone

**Prevention:**
1. Add a post-generation validation step that cross-references every factual claim in the LLM output against the source data passed in (date ranges, contact names, topics). Any claim not traceable to input data is a hallucination signal.
2. Validate required fields are non-empty and within expected value ranges (e.g., `state` is one of `A`, `B`, `C`).
3. Implement a confidence gate: if the Chorus transcript was absent (404/401 path) and the output still contains transcript-derived claims, reject the output.
4. Write to HubSpot only after validation passes. Log the raw LLM output separately for auditability before the gate.

**Detection:** Pre-write validator that diffs the output's factual claims against the source context passed to the prompt.

**Phase:** Core pipeline build — validation gate must ship with the first working end-to-end run, not added as a follow-on.

---

### PITFALL-C2: Missing Token Silent Substitution (`{{token.name}}` Becomes Empty String)

**Severity:** HIGH
**Risk area:** Prompt Template

**What goes wrong:** Python's `str.format()` and Jinja2's default Undefined class silently replace missing tokens with an empty string. An email goes out with "Hi , we noticed your team at  has been..." instead of raising an error. The problem is invisible in logs because no exception is raised.

**Why it happens:** Jinja2's default Undefined evaluates to an empty string in string context. Python `.format()` with a dict using `**kwargs` raises `KeyError` only if the key is absent from the dict entirely — but if the source data returned `None` or `""` for the field, the substitution silently succeeds with an empty or null value. The `{{token.name}}` syntax used in this project's prompt templates is especially prone to this if values are fetched from HubSpot properties that exist but contain no data.

**Consequences:**
- Emails send with blank contact names, missing company names, garbled opening lines
- If the blank token is inside the prompt sent to Claude, the LLM may fabricate a plausible-sounding value, creating a hallucination disguised as a data problem

**Prevention:**
1. Use Jinja2 with `StrictUndefined` — any missing variable raises `UndefinedError` immediately.
2. Before template rendering, assert that every expected token is present AND non-empty. Treat `None`, `""`, and `"N/A"` as missing unless the field is explicitly optional.
3. Define a manifest of required vs. optional tokens per email template variant. Fail loudly with a named exception (`MissingTokenError`) that logs which token was absent and for which contact ID.
4. Write a unit test that renders every template with a partial context and asserts it raises, not silently substitutes.

**Detection:** Missing required fields in HubSpot property fetch response. Zero-length strings in rendered prompt before submission to Claude.

**Phase:** Template engine implementation — the manifest and strict-mode validation must be built before any end-to-end run.

---

### PITFALL-C3: Stale CRM Data Presented as Current in Email

**Severity:** HIGH
**Risk area:** Data Freshness / CRM Field Age

**What goes wrong:** The pipeline reads a HubSpot contact property (e.g., `last_meeting_date`, `last_activity_summary`) and passes it directly to Claude as present-tense context. If that property was last updated 14 months ago, Claude produces an email that says "we spoke recently about..." when no contact has occurred in over a year. The recipient knows this is false. The relationship is damaged.

**Why it happens:** HubSpot stores the last *written* value with a timestamp, but the pipeline fetches the value without checking when it was written. The Data Honesty Rules in the prompt's data freshness tiers can only work if the pipeline actually attaches the field's `updatedAt` timestamp alongside the value.

**Consequences:**
- Emails reference stale events as if they are recent
- State A/B/C classification could be wrong if the underlying evidence (last engagement) is stale
- The "no recent activity" framing that should trigger State C is skipped entirely

**Prevention:**
1. When fetching HubSpot contact properties, always request the `updatedAt` metadata for each property alongside the value. Use the `propertiesWithHistory` endpoint or include `updatedAt` via the v3 properties API.
2. In the context object passed to Claude, attach the age of every data point in human-readable form: `"last_meeting_date": "2024-11-03 (7 months ago)"`.
3. Enforce freshness tiers in the pre-prompt assembly layer, not just in the prompt instructions. If `last_activity_date` is over N days, set a flag that forces Claude to use State C framing regardless of other signals.
4. Never pass a field to Claude as "current" if its `updatedAt` is older than the freshness threshold for that tier.

**Detection:** Add a `data_freshness_report` to the job summary: for each contact processed, log which fields were in which freshness tier.

**Phase:** HubSpot data fetching layer — must be designed as "value + age" from the first fetch implementation.

---

### PITFALL-C4: Tenacity Retrying Permanent Errors (401, 400, 422)

**Severity:** HIGH
**Risk area:** tenacity retry configuration

**What goes wrong:** A misconfigured tenacity decorator retries `AuthenticationError` (HTTP 401) or `BadRequestError` (HTTP 400/422) against the Anthropic or HubSpot API. Each retry is a wasted API call, and for a 401, it may trigger account lockout or flag the integration as compromised. The pipeline stalls for the full retry duration (e.g., 10 attempts × exponential backoff = minutes) before eventually raising the same permanent error.

**Why it happens:** The common beginner configuration `@retry(stop=stop_after_attempt(5))` retries on *all* exceptions. The `retry_if_exception_type(Exception)` default catches every subclass of Exception, including non-transient HTTP errors. Anthropic's 401 (invalid API key), 400 (malformed request), and 422 (schema validation failure) are permanent errors that will not resolve on retry.

**Consequences:**
- A bad API key wastes 5+ API calls and delays job failure by minutes
- A HubSpot 422 (invalid property value) retries silently until exhaustion, masking a schema mismatch bug
- In a batch of 8 emails, a permanent error on contact 1 could consume the retry budget and delay the failure report

**Prevention:**
1. Define explicit retryable vs. non-retryable exception classes:
   - **Retry:** `anthropic.RateLimitError` (429), `anthropic.APIStatusError` with status 529 (overload), `anthropic.APIConnectionError`, HubSpot 429 with `Retry-After`
   - **Never retry:** `anthropic.AuthenticationError` (401), `anthropic.BadRequestError` (400), `anthropic.PermissionDeniedError` (403), HubSpot 422 (invalid property), HubSpot 400 (bad request)
2. Use `retry_if_exception_type` with a specific allowlist, not a blocklist. Fail-fast on permanent errors.
3. For 529 (Anthropic server overload), use a longer initial backoff (minimum 4 seconds) with jitter. For 429 (rate limit), honor the `retry-after` header value.
4. Add `before_sleep` logging so each retry attempt is visible in the job log.

**Detection:** Monitor job duration. If a single contact takes longer than 2 minutes, a permanent error is being retried.

**Phase:** Anthropic and HubSpot client wrappers — retry logic must be finalized before integration testing.

---

## Moderate Pitfalls

---

### PITFALL-M1: HubSpot Engagements Pagination Drops Records

**Severity:** MEDIUM
**Risk area:** HubSpot API

**What goes wrong:** The pipeline fetches engagement history by iterating pages using the `after` cursor. If the code stops when it receives an empty `paging` key or stops after a fixed number of pages, it silently misses engagements beyond the first page. A contact with 12 engagements but a page size of 10 has their last 2 engagements silently ignored — and those might be the most recent ones.

**Why it happens:** HubSpot's v3 pagination uses cursor-based `after` tokens. Community reports confirm the `after` parameter has historically behaved inconsistently across endpoints (returning out-of-order or duplicate results during 2024-2025 incidents). The Engagements v1 endpoint and the newer CRM Engagements v3 API have different pagination behaviors, and mixing them creates subtle off-by-one gaps.

**Consequences:**
- Last contact date is derived from an incomplete engagement history
- State classification uses a call that appears to be the most recent but isn't
- The freshness tier calculation is wrong because the actual most recent engagement was on a skipped page

**Prevention:**
1. Implement a `while paging.get("next", {}).get("after")` loop — only stop when the API explicitly returns no `next` cursor, not when you've fetched N pages.
2. Sort engagements by `lastUpdated` descending on the client side after fetching all pages, not by arrival order.
3. Cap page fetches at a sensible maximum (e.g., 50 pages) and log a warning if that cap is reached — it indicates unusually deep history that needs attention.
4. During development, compare paginated engagement counts against the contact's engagement count visible in the HubSpot UI for 5-10 test contacts.

**Detection:** Log the total engagement count returned per contact. Cross-reference against HubSpot UI for sample contacts during QA.

**Phase:** HubSpot data fetching layer.

---

### PITFALL-M2: Chorus AI 404/401 Causes Silent Wrong Classification

**Severity:** MEDIUM
**Risk area:** Chorus AI integration

**What goes wrong:** The requirement says Chorus AI 404/401 errors should "silently continue." But if the pipeline continues with no transcript and doesn't explicitly mark the transcript as absent in the context passed to Claude, Claude may still produce a "we discussed X on your last call" statement because it has no instruction telling it a transcript was unavailable.

**Why it happens:** The design intent is to gracefully handle missing transcripts, but "silently continue" is ambiguous about what gets passed to Claude. If the transcript slot in the context is simply left empty (`"transcript": ""`), Claude may interpret empty string as "no transcript provided" and hallucinate a summary based on other context clues.

**Consequences:**
- Email references a conversation that was never fetched
- State classification is based on zero call evidence, but Claude classifies it as State A because the prompt framing implied a call exists
- The Data Honesty Rules in the prompt are undermined if the transcript-absent signal is not explicit

**Prevention:**
1. Use a sentinel value, not empty string: `"transcript": null` or `"transcript_available": false` with an accompanying `"transcript_status": "unavailable_401"` field.
2. The prompt must have an explicit conditional instruction: "If `transcript_available` is false, do not reference the content of any call. Base state classification only on HubSpot engagement dates."
3. Log every Chorus 404/401 with the contact ID and conversation ID that failed. This creates an audit trail if an email is challenged.
4. Handle Chorus auth token expiry (401) differently from missing conversation (404): a 401 should trigger a circuit breaker that stops all subsequent Chorus calls in the run (the token is expired for all contacts), whereas a 404 is contact-specific.

**Detection:** Per-run report: number of contacts where transcript was available vs. unavailable vs. Chorus auth failure.

**Phase:** Chorus AI integration layer.

---

### PITFALL-M3: Anthropic `max_tokens` Truncation Produces Invalid Structured Output

**Severity:** MEDIUM
**Risk area:** Anthropic Claude API

**What goes wrong:** The LLM reaches `max_tokens` mid-response. With standard JSON prompting, this truncates the JSON body, causing a parse failure. With Structured Outputs (constrained decoding), the output is also cut off — `stop_reason` is `"max_tokens"` and the official docs confirm "incomplete responses don't guarantee schema compliance" in this case. The pipeline receives a partial response that passes the format check but has truncated field values.

**Why it happens:** Email generation prompts that include full Chorus transcripts, 12 months of engagement history, and 8-email variant logic are large. Setting `max_tokens` conservatively to control cost creates a collision with response length. The `stop_reason` check is frequently omitted because developers test with short prompts and never hit the limit.

**Consequences:**
- A truncated email draft is written to HubSpot as the final output
- The truncation point may fall inside a field value, producing gibberish at the end of the email body
- Because HTTP status is 200 and JSON parses (partial objects can be valid JSON fragments), the error goes undetected without an explicit `stop_reason` check

**Prevention:**
1. Always check `response.stop_reason`. If `stop_reason == "max_tokens"`, treat it as a hard failure and do not write the output.
2. Set `max_tokens` to at least 2× the longest expected email output (include all 8 variants). Log the actual output token count per run.
3. For the Chorus transcript path, truncate the transcript to a maximum token budget *before* sending to Claude, not after. Summarize older transcripts rather than including full text.
4. Add a post-generation length check: if the generated email body is under N characters, flag it as suspiciously short.

**Detection:** `stop_reason` field in every API response. Output token count in usage metrics.

**Phase:** Anthropic client wrapper — stop_reason checking must be a required field in the response validation layer.

---

### PITFALL-M4: Prompt Cache Invalidation Driven by Per-Contact Dynamic Data

**Severity:** MEDIUM
**Risk area:** Anthropic Claude API / Prompt Caching

**What goes wrong:** The pipeline constructs a system prompt that includes the Data Honesty Rules, STATE classification instructions, and the email templates — this is a good candidate for prompt caching. But if any dynamic per-contact data (contact name, company, transcript snippet) is embedded in the cached system prompt prefix rather than in the user message, every contact triggers a cache miss. The pipeline pays full input token cost for every contact and may hit rate limits on large batches.

**Why it happens:** Developers building the prompt often concatenate all context into one large string for simplicity. The cache key is the exact prefix up to the `cache_control` breakpoint — one character difference invalidates it.

**Consequences:**
- 8-email generation per contact at full token cost instead of cached cost
- Significantly higher API spend
- Increased latency per contact due to uncached large prompt processing
- Risk of hitting input token rate limits on large batches

**Prevention:**
1. Structure the prompt with a deterministic, static system prompt containing only the rules, classification logic, and template structure. Mark this with `cache_control: {type: "ephemeral"}`.
2. All per-contact data (CRM fields, transcript, engagement history) goes in the user turn only.
3. Never include timestamps, request IDs, or any value that changes per contact in the cached prefix.
4. Verify caching is working by checking `cache_read_input_tokens` in the usage response. If it is always 0, the cache is not being hit.

**Detection:** `response.usage.cache_read_input_tokens` should be non-zero on all contacts after the first in a batch.

**Phase:** Anthropic client wrapper / prompt architecture.

---

### PITFALL-M5: HubSpot Property Write Fails Silently on Type Mismatch

**Severity:** MEDIUM
**Risk area:** HubSpot API

**What goes wrong:** The pipeline writes a string value to a HubSpot property that is defined as a `date`, `number`, or `enumeration` (picklist) type. HubSpot returns a 400 or 422 with a message like "value was not one of the allowed options" or "field name value is invalid." If this error is only logged at WARNING level (not raising), the contact record is partially updated — some fields written before the failure are committed, fields after are not.

**Why it happens:** HubSpot property types are defined in the portal and are not always self-documenting in the API schema. A `text` field in the UI is a `string` in the API, but a dropdown/select field requires the internal option value (not the display label). Date properties expect ISO 8601 format at midnight UTC. If the LLM output contains a human-readable date string like "November 3, 2024," writing it directly to a `date` property fails.

**Consequences:**
- Partial writes: some properties updated, others silently skipped
- Overwrite semantics means the next successful run may overwrite the partial state with new partial state — no clean previous value exists
- Property type errors disguised as application errors waste retry cycles

**Prevention:**
1. Define a property schema manifest for all HubSpot properties the pipeline writes to. Include: property name, internal type, valid values for enumerations.
2. Validate LLM output against this manifest before attempting the write. Coerce dates to ISO 8601. Validate enum values against the allowed options list.
3. Write all properties for a contact in a single PATCH request (HubSpot supports bulk property updates per contact). This is atomic — either all or none succeed for that request.
4. Treat any 4xx on the HubSpot write as a hard failure with full details logged (contact ID, property name, value attempted, error body).

**Detection:** Log the full HTTP response body on every non-2xx HubSpot write. A silent 422 on property type is the most common invisible failure in this pipeline.

**Phase:** HubSpot write layer.

---

### PITFALL-M6: Make.com Duplicate Trigger on Retry

**Severity:** MEDIUM
**Risk area:** Make.com → GitHub Actions trigger

**What goes wrong:** Make.com's HTTP module for triggering `workflow_dispatch` retries on network timeout or on a non-2xx response. The GitHub API for `workflow_dispatch` returns HTTP 204 (No Content) on success. If Make.com's HTTP module is configured to expect HTTP 200, it treats 204 as a failure and retries — triggering two GitHub Actions runs for the same contact batch. Both runs fetch the same contacts and overwrite HubSpot properties, the second run winning and potentially overwriting a first run's correct output with a second (potentially different) LLM output.

**Why it happens:** Make.com HTTP module defaults vary; 204 is distinct from 200 and some configurations treat it as an error. The GitHub `workflow_dispatch` API consistently returns 204. Without a concurrency group on the workflow, two triggered runs execute in parallel.

**Consequences:**
- Same HubSpot contact properties written twice with potentially different LLM outputs (non-deterministic)
- Double API cost for the duplicate run
- If the first run is mid-write when the second starts, properties may be in a half-first-run, half-second-run state

**Prevention:**
1. Configure Make.com's HTTP module to accept 204 as a success response code explicitly.
2. Add a GitHub Actions concurrency group scoped to the workflow:
   ```yaml
   concurrency:
     group: asotos-campaign-pipeline
     cancel-in-progress: false
   ```
   `cancel-in-progress: false` ensures a queued duplicate waits rather than starting while another run is in progress. Since runs are idempotent by intent, queuing is safe; a duplicate will overwrite with the same data.
3. Include an idempotency key (e.g., batch timestamp or run UUID) in `workflow_dispatch` inputs. The pipeline should log this key and detect if the same key was processed in the last N hours.

**Detection:** GitHub Actions run history — two runs triggered within seconds of each other for the same workflow is the fingerprint.

**Phase:** Make.com integration setup and workflow YAML configuration.

---

### PITFALL-M7: GitHub Actions Secrets Leaked via Debug Logging

**Severity:** MEDIUM
**Risk area:** GitHub Actions secrets management

**What goes wrong:** During debugging, a developer adds `print(os.environ)`, `run: env`, or logs a full API response that contains an access token in a header echo. GitHub masks the exact secret strings registered in Settings → Secrets, but any transformation of those secrets (base64 encoding, URL encoding, substring extraction, or inclusion in a dict that is then `str()`'d) produces output that is *not* masked. That output persists in the Actions log for the default 90-day retention period.

**Why it happens:** The masking is exact-string matching against the raw secret value. Python's logging of complex objects (dicts, response objects) often includes the secret in a transformed form. Specifically, the `anthropic.APIError` exception object includes request headers in its `__str__` representation, which contains the `x-api-key` header.

**Consequences:**
- API keys for Anthropic, HubSpot, and Chorus AI are visible in logs
- Default 90-day artifact/log retention window = 90-day exposure window
- If the repository is public or the Actions logs are accessible to third parties, the keys are compromised

**Prevention:**
1. Never log `os.environ`, full HTTP request headers, or exception `__repr__`/`__str__` for API client exceptions without filtering.
2. Use `::add-mask::` for any dynamically constructed secret derivative:
   ```python
   print(f"::add-mask::{derived_value}")
   ```
3. Set log retention to 7-14 days at the repository level (sufficient for debugging purposes).
4. Wrap all Anthropic/HubSpot/Chorus exception handlers to log only the status code and sanitized message, never the full exception object.
5. Use GitHub Environments with environment-scoped secrets to limit which jobs can access each secret.

**Detection:** Audit the Actions log for any run that contains the first 4 characters of a known secret string. Set up a secret scanning rule if using GitHub Advanced Security.

**Phase:** Pipeline infrastructure setup and code review checklist.

---

## Minor Pitfalls

---

### PITFALL-L1: `workflow_dispatch` Boolean Inputs Are Strings, Not Booleans

**Severity:** LOW
**Risk area:** GitHub Actions

**What goes wrong:** A `workflow_dispatch` input declared as `type: boolean` arrives in the Python script as the string `"true"` or `"false"`, not Python `True`/`False`. A check like `if dry_run:` is always truthy because a non-empty string is truthy in Python. `if dry_run == True:` fails silently because `"true" != True`.

**Prevention:** Coerce all boolean `workflow_dispatch` inputs explicitly: `dry_run = inputs.get("dry_run", "false").lower() == "true"`. This is required regardless of the declared type in the YAML.

**Phase:** Workflow YAML inputs handling — document and enforce in code review.

---

### PITFALL-L2: HubSpot API Burst Limit Miscounted on Engagement Pagination

**Severity:** LOW
**Risk area:** HubSpot API rate limits

**What goes wrong:** The pipeline makes 3-5 API calls per contact (fetch properties, fetch engagements page 1, fetch engagements page 2, write properties). At 10 contacts per batch, that is 30-50 requests. HubSpot's burst limit is 100 requests per 10 seconds for Professional tier. A tight loop with no inter-request sleep can consume the entire burst window on the fetch phase alone, causing 429 errors on the write phase.

**Prevention:** Add a minimal sleep between contact iterations (100ms is sufficient). Honor the `Retry-After` header on 429 responses. Log the current request count in debug mode.

**Phase:** Pipeline orchestration loop.

---

### PITFALL-L3: Chorus Conversation ID Extraction Fragility

**Severity:** LOW
**Risk area:** Chorus AI integration

**What goes wrong:** If the conversation ID is extracted by parsing a HubSpot property that stores a Chorus meeting URL (e.g., regex on `https://chorus.ai/meeting/XXXXX`), any change to the URL format (Chorus has historically changed their URL structure following the ZoomInfo acquisition) silently produces no match and the pipeline proceeds as if no transcript is available, without logging why.

**Prevention:** Extract and validate the conversation ID with an explicit regex with a named capture group. If the regex fails to match, log the raw URL value and the failure reason — never silently produce `None`. Test the regex against at least three URL format variants.

**Phase:** Chorus integration layer.

---

### PITFALL-L4: RUNNER_TEMP Sensitive File Persistence

**Severity:** LOW
**Risk area:** GitHub Actions runner hygiene

**What goes wrong:** If the pipeline writes intermediate data (transcript content, contact PII, email drafts) to `$RUNNER_TEMP` or a temp file in the workspace, GitHub Actions does not guarantee this directory is wiped between runs on self-hosted runners. On GitHub-hosted runners, the entire VM is destroyed, but on self-hosted runners (if used now or in the future), temp files from a previous run are readable by the next run.

**Prevention:** Always write temp files to `tempfile.mkdtemp()` with `finally: shutil.rmtree(tmpdir)` cleanup. Do not use bare `open("temp.json", "w")` in the workspace directory. If using GitHub-hosted runners exclusively, this is low risk but worth enforcing as a code standard.

**Phase:** File I/O utilities.

---

### PITFALL-L5: HubSpot API Versioning — New Date-Format Version System

**Severity:** LOW
**Risk area:** HubSpot API version deprecation

**What goes wrong:** HubSpot introduced a new versioning scheme using date-format versions (`/2026-03/`) with an 18-month support window per version, replacing the v1/v2/v3 system. The Contact Lists API v1 sunset was extended to April 30, 2026. The Engagements API still partially relies on v1 endpoints for some operations. Code written against v1 endpoints may break when sunset dates are reached without warning.

**Prevention:** Audit which HubSpot API endpoints the pipeline uses and check their current deprecation status on the HubSpot Developer Changelog. Prefer CRM v3 endpoints and the new date-versioned APIs where available. Set a calendar reminder to check the changelog every 6 months.

**Phase:** HubSpot client implementation — document endpoint versions used.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|---|---|---|
| HubSpot data fetch layer | Stale data without timestamps (C3), incomplete pagination (M1) | Always fetch `updatedAt` with property values; exhaust cursor pagination |
| Chorus AI integration | Silent wrong classification when transcript missing (M2), fragile ID extraction (L3) | Explicit sentinel for absent transcript; named-capture regex |
| Prompt template engine | Silent token substitution (C2) | Jinja2 StrictUndefined; pre-render manifest validation |
| Anthropic client wrapper | max_tokens truncation (M3), cache invalidation (M4), retry on permanent errors (C4) | stop_reason check; static cached prefix; explicit retry allowlist |
| LLM output validation | Hallucinated facts written to HubSpot (C1), property type mismatch (M5) | Post-generation cross-reference validator; property schema manifest |
| HubSpot write layer | Partial writes from type errors (M5) | Single atomic PATCH; 4xx = hard failure |
| Workflow YAML / Make.com | Duplicate triggers (M6), boolean input coercion (L1), secret leaks (M7) | Concurrency group; explicit 204 acceptance; no env logging |
| Runner configuration | RUNNER_TEMP file persistence (L4) | tempfile.mkdtemp + finally cleanup |

---

## Sources

- Anthropic Structured Outputs official docs: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- Anthropic Python SDK (Context7): /anthropics/anthropic-sdk-python
- Tenacity docs (Context7): /websites/tenacity_readthedocs_io_en
- HubSpot API usage guidelines: https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines
- HubSpot Community — pagination out-of-order bug: https://community.hubspot.com/t5/APIs-Integrations/BUG-API-with-Pagination-return-out-of-order-and-duplicated-rows/m-p/1192296
- HubSpot API v1 sunset timeline: https://developers.hubspot.com/changelog/extension-contact-lists-api-v1-sunset-moved-to-april-30-2026
- Anthropic 529 vs 429 retry strategies: https://www.respan.ai/articles/anthropic-api-rate-limits
- Prompt caching gotchas: https://gu-log.vercel.app/en/posts/en-sp-112-20260313-anthropic-prompt-caching-2026-update
- Jinja2 StrictUndefined: https://alexwlchan.net/2022/strict-jinja/
- GitHub Actions boolean input coercion: https://github.com/orgs/community/discussions/9343
- GitHub Actions concurrency control: https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency
- GitHub Actions secrets masking limitations: https://www.karimrahal.com/2023/01/05/github-actions-leaking-secrets/
- LLM output validation in production: https://dev.to/vhub_systems_ed5641f65d59/how-to-validate-llm-outputs-in-production-before-they-break-your-pipeline-ahl
- Chorus AI API authentication: https://api-docs.chorus.ai/
