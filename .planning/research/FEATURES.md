# Feature Landscape: ASOTOS AI Email Campaign Pipeline

**Domain:** GitHub Actions AI outreach pipeline — HubSpot CRM + Chorus AI + Claude API
**Project:** ASOTOS for Staff Domain — 8-email re-engagement sequence, 180+ day cold B2B prospects
**Researched:** 2026-06-12
**Overall confidence:** HIGH (stack is well-documented; patterns verified across multiple authoritative sources)

---

## Table of Contents

1. [Pipeline Reliability](#pipeline-reliability)
2. [AI Quality](#ai-quality)
3. [CRM Integration](#crm-integration)
4. [Observability](#observability)
5. [Template Management](#template-management)
6. [Feature Dependencies](#feature-dependencies)
7. [MVP Recommendation](#mvp-recommendation)
8. [Anti-Features](#anti-features)
9. [Sources](#sources)

---

## Pipeline Reliability

These features are required for the pipeline to be safe to run against live CRM data. Missing any one of these creates either data corruption risk or silent failure risk.

### Table Stakes

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Per-contact idempotency guard** | Re-runs from workflow failure must not double-write notes or re-send sequences to contacts already processed | Medium | Use a `run_id` + `contact_id` composite key written as a HubSpot contact property (`asotos_last_run_id`). Check before processing each contact. |
| **Structured error handling with exit codes** | GitHub Actions fails silently on unhandled exceptions; Python `sys.exit(1)` must be explicit | Low | Every API call wrapped in try/except; failures bubble to step-level failure. Never swallow exceptions. |
| **Exponential backoff with jitter on API calls** | HubSpot API: 100 req/10s limit. Claude API: concurrency limits. Chorus AI: unspecified rate limits | Low | Use `tenacity` library. Max 3 retries, base 2s, max 30s, jitter 0–5s. Covers transient 429s and 5xx. |
| **Dead-letter JSON for failed contacts** | When a contact fails processing after retries, it must be captured rather than silently skipped | Low | Write to `artifacts/failed_contacts.json` per run. Fail the step but continue to next contact (unless failure rate exceeds threshold). |
| **Failure rate circuit breaker** | If >20% of contacts in a batch fail, abort rather than continue with potentially corrupt data | Low | Count failures in-memory during run; if threshold crossed, write DLQ artifact and raise hard failure. |
| **GitHub Actions artifact upload on every run** | All generated content, processing results, and run metadata must survive job completion | Low | Use `actions/upload-artifact@v4`. Two artifacts per run: `run-manifest.json` (metadata) and `generated-emails/` (all LLM outputs before any send). |
| **Dry-run mode** | Must be able to generate all emails and validate against CRM without writing anything | Low | Controlled by `workflow_dispatch` input `dry_run: true`. Skips all write operations; artifacts still uploaded for review. |
| **Manual approval gate before CRM write** | AI-generated content must be human-reviewed before it touches the live CRM or triggers sends | Medium | Implement using GitHub Environments with required reviewers. Workflow pauses between generation job and write job. Reviewer inspects artifact before approving. |
| **Workflow dispatch with typed inputs** | Operators need runtime control over batch size, target segment, and dry-run flag | Low | `workflow_dispatch` inputs: `contact_limit` (number), `dry_run` (boolean), `segment_filter` (string). GitHub validates types at trigger time. |

**Dependency chain:** Idempotency guard → Run manifest artifact → Manual approval gate → CRM write

---

## AI Quality

These features directly determine whether the generated emails are good enough to use. The first three are table stakes. The remaining two are differentiators.

### Table Stakes

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **JSON schema validation on LLM output** | Claude returns JSON for structured email data; malformed or incomplete output must be caught before it reaches the CRM | Low | Define a Pydantic model for the expected output shape. Validate every response. On validation failure: retry with error context injected into prompt (max 2 retries), then DLQ. Keep nesting to 2 levels max — deeper schemas increase LLM error rates. |
| **Token budget enforcement per contact** | Unbounded input context leads to unpredictable cost and occasional context-exceeded errors | Low | Count tokens before sending using `client.messages.count_tokens()` (the only accurate method — tiktoken undercounts Claude tokens). Truncate Chorus transcript to fit a defined budget (e.g., 2,000 tokens max for transcript content). |
| **Model version pinning** | `claude-sonnet-4-5` behaviour differs from `claude-haiku-3-5`; unpinned aliases can break prompt behaviour silently | Low | Hardcode model string in config: `claude-sonnet-4-5-20251022`. Log model used in run manifest. Never use unpinned aliases in production. |

### Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Prompt caching for system prompt + persona block** | The system prompt (persona, tone, sequence rules, company context) is identical across all contacts in a run. Caching it yields ~90% cost reduction on that portion of every API call | Low | Minimum 1,024 tokens required for caching on Sonnet-class models. Mark system prompt with `cache_control: {"type": "ephemeral", "ttl": "1h"}` using 1-hour TTL for a full batch run (batch completes in under 1 hour). Monitor `cache_read_input_tokens` to confirm hits. |
| **Reasoning field in output schema** | Placing a `reasoning` field before the `subject_line` and `body` fields in the output JSON schema forces chain-of-thought before committing to copy. Measurably improves output quality on nuanced personalisation tasks. | Low | Schema: `{reasoning: str, subject_line: str, preview_text: str, body_html: str, tone_notes: str}`. Strip `reasoning` before writing to CRM — it is for validation/audit only. |
| **Per-email tone classification** | Tag each generated email with a tone label (e.g., `direct`, `empathetic`, `curiosity`) derived from the LLM's own assessment. Enables downstream A/B analysis of which tone performs best with cold Australian B2B prospects. | Low | Add `tone_label` to output schema. No extra API call required — LLM self-classifies during generation. Store in run manifest. |
| **Batch API for cost reduction** | For non-urgent generation runs (next-business-day SLA acceptable), the Anthropic Batch API cuts token costs by 50% across all usage | Medium | Not suitable if same-day review and send is required. Good fit for scheduled weekend batch runs. Returns results asynchronously; pipeline must poll for completion. |

**Key constraint on caching:** Cache prefix must be 100% identical across all calls in a run. Any dynamic content (contact name, transcript) must be in the `user` message, not the `system` block. Static content (persona, tone guidelines, sequence structure, Australian B2B context) goes in the cacheable system block.

---

## CRM Integration

### Table Stakes

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Write AI-generated email to HubSpot as a Note (engagement), not a contact property** | Notes surface in the contact timeline with a timestamp — this is the canonical location for generated-content audit trails. Contact properties store current state (single value); notes preserve history. Reviewers can see all generated emails without querying custom properties. | Low | POST to `/crm/v3/objects/notes` with `hs_note_body` (HTML or rich text), `hs_timestamp`, and association to contact via `associationTypeId: 202`. Body can be up to 65,536 characters. |
| **Write status flag to contact property** | Searchable/filterable in HubSpot: `asotos_sequence_status` (enum: `generated`, `approved`, `sent`, `suppressed`). Notes are not filterable. | Low | Use Properties API to define a custom enumeration property on Contact object. Update via PATCH to `/crm/v3/objects/contacts/{id}`. |
| **Write run metadata to contact property** | `asotos_last_run_date` (date) and `asotos_last_run_id` (string) enable idempotency checks and troubleshooting without querying notes. | Low | Two additional custom contact properties. Required for idempotency guard (see Pipeline Reliability). |
| **Contact filter: 180+ day silence check** | Must verify prospect has had no activity in HubSpot for 180+ days before including in sequence. Last activity date is a native HubSpot property. | Low | Filter using `lastmodifieddate` or `notes_last_updated` < (today - 180 days) in the HubSpot search API query. |
| **Association: Note to Contact AND Company** | Notes associated only to contact are invisible on the company record. In B2B, account-level visibility matters — reps work at the company level. | Low | Include two associations in the POST body: one to contact (`associationTypeId: 202`), one to company (`associationTypeId: 190`). |
| **HubSpot timestamp accuracy** | Notes created with incorrect `hs_timestamp` appear at the bottom of the timeline (pre-2000 effectively). Must use current UTC timestamp. | Low | Set `hs_timestamp` to `datetime.utcnow().isoformat() + "Z"`. Validate in output schema. |

### Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Sequence enrollment trigger via CRM property** | Rather than calling a send API directly, set `asotos_sequence_status = approved` and use a HubSpot workflow to enroll in a sequence. This decouples email sending from the pipeline, preserves HubSpot's native send-time optimisation, and keeps unsubscribe handling in HubSpot. | Medium | Requires a HubSpot workflow to be pre-configured (outside this pipeline). Pipeline only writes a property; HubSpot owns send logic. Preferred over pipeline-initiated sends. |
| **Chorus transcript summary as note** | Store the raw Chorus transcript excerpt (key moments, objections, topics) that informed the email generation as a separate note, linked to the same contact. Creates a traceable audit: "this email was generated based on this transcript content." | Low | Second note POST per contact, tagged with `[ASOTOS SOURCE]` prefix in body. Adds ~2 API calls per contact. |

**The notes-vs-properties decision in summary:**
- Notes (engagements): use for AI-generated email bodies, source transcript excerpts — anything that is a timestamped event and needs timeline visibility
- Custom properties: use for status flags, run IDs, dates — anything that needs to be filterable, searchable, or reportable in HubSpot lists/workflows
- Never write long-form AI content to a contact property — the 65,536-character limit applies to both, but properties are single-value (overwritten on each run); notes are append-only

---

## Observability

### Table Stakes

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Run manifest artifact (JSON)** | Every run must produce a machine-readable record of what was processed, what succeeded, what failed, and what was generated | Low | `artifacts/run_manifest.json`: `{run_id, timestamp, model, prompt_version, contacts_processed, contacts_succeeded, contacts_failed, total_input_tokens, cache_hit_tokens, cache_miss_tokens, total_cost_usd}`. Upload with `actions/upload-artifact@v4`. |
| **Generated emails artifact (structured)** | All LLM outputs must be persisted before any CRM write or send. Enables review, rollback, and debugging without querying the CRM. | Low | `artifacts/generated_emails/{contact_id}.json` per contact. Schema: `{contact_id, email_number, subject_line, preview_text, body_html, reasoning, tone_label, generated_at, model, tokens_used}`. |
| **Structured step-level logging** | GitHub Actions log streams are the primary debugging surface. Unstructured `print()` statements make diagnosis slow. | Low | Use Python `logging` with JSON formatter. Log: contact ID, step name, duration, token counts, API response codes. Never log PII (full name, email address) — log contact ID only. |
| **Token cost summary in job summary** | Operators need to know what each run cost. GitHub Actions job summaries render markdown in the UI. | Low | Write to `$GITHUB_STEP_SUMMARY` at end of generation step. Format: table with contacts processed, total tokens, cache hit rate %, estimated cost in AUD. |
| **Failure notification** | Silent failures on a production data pipeline are unacceptable | Low | Use GitHub Actions `if: failure()` step to send a Slack webhook or email notification. Include run ID, failure count, DLQ artifact link. |

### Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Cache hit rate tracking** | Confirms that prompt caching is working. If cache hit rate drops to 0%, the system prompt changed (prompt version bump) or the TTL expired between jobs — both require investigation. | Low | Derived from `cache_read_input_tokens / (cache_read_input_tokens + cache_creation_input_tokens)`. Log in run manifest and step summary. Alert if rate < 50% on runs with >10 contacts. |
| **Per-contact token breakdown** | Enables identifying outlier contacts whose transcripts are exceptionally long and consuming disproportionate budget | Low | Store in each `generated_emails/{contact_id}.json`. Aggregate in run manifest. |
| **Prompt version in run manifest** | When a prompt is updated, historical runs can be correlated with the prompt version that produced them. Essential for A/B analysis of prompt changes. | Low | Store prompt version string (e.g., `v1.2.0`) in run manifest and in each generated email artifact. Derived from the template file's git tag or a manually maintained `PROMPT_VERSION` constant. |

---

## Template Management

### Table Stakes

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **`{{token.name}}` substitution in prompt templates** | The 8-email sequence has static structural copy (context, instructions) and dynamic per-contact slots (prospect name, company, transcript excerpt, days since last contact). These must be cleanly separated. | Low | Use Python's `string.Template` with `${variable}` syntax, or Jinja2 for conditional logic. Store template files in `prompts/` directory as `.txt` or `.j2` files. Render at runtime with contact data. |
| **Prompt templates as versioned files in git** | Prompts are code. They must be tracked, reviewed in PRs, and rolled back when they produce bad output. Storing them in a database or external registry adds complexity without benefit at this scale. | Low | `prompts/sequence_generator_v1.j2`, `prompts/system_persona_v1.txt`. Commit alongside code. Prompt version = git tag or semantic version string in filename. |
| **Separation of static and dynamic prompt content** | Static content (persona, tone rules, sequence structure, Australian B2B context, all 8 email briefs) must be in the cacheable system block. Dynamic content (contact name, company, transcript, days silent) must be in the user message. Mixing them invalidates the cache. | Low | This is an architectural constraint from the Claude API caching design. Template files must enforce this split. Two template files per generation call: `system_prompt.j2` (static) and `user_message.j2` (dynamic). |
| **Token budget check before prompt submission** | A long Chorus transcript inserted into the user message can exceed token budget. Must count tokens before sending, not after failure. | Low | Use `client.messages.count_tokens()` with the rendered prompt. If over budget, truncate transcript to fit (keep first N and last N words of transcript — opening and closing of calls are most informative). |
| **Prompt smoke test in CI** | A prompt change that breaks JSON schema validation should be caught before it runs against live contacts | Medium | GitHub Actions workflow: on push to `prompts/`, run smoke test with 3 synthetic contacts. Validate output against Pydantic schema. Fail the PR check if validation fails. |

### Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **All 8 email briefs in one system prompt** | Rather than 8 separate API calls (one per email in the sequence), pass all 8 email briefs in the system prompt and request all 8 emails in a single Claude call per contact. Dramatically reduces API calls from 8N to N (where N = contact count). The system prompt's 8 briefs are cached across all contacts. | Medium | Requires careful schema design: output is a list of 8 email objects. Each object: `{email_number, reasoning, subject_line, preview_text, body_html, tone_label}`. Increases per-contact output tokens but eliminates 7x the API call overhead. |
| **Prompt change log** | A human-readable `PROMPT_CHANGELOG.md` tracking what changed in each prompt version and why. Not automated — maintained manually. Enables reconstructing why output quality changed between runs. | Low | Simple markdown file. Not a system feature — a process discipline. Takes 5 minutes per prompt change. |

---

## Feature Dependencies

```
Idempotency guard
  └── requires: contact property write (asotos_last_run_id)
  └── requires: run manifest artifact

JSON schema validation
  └── requires: Pydantic output model
  └── required by: DLQ for failed contacts

Token budget enforcement
  └── required by: prompt caching (must know token count to confirm >1024 threshold)
  └── required by: cost tracking in run manifest

Prompt caching
  └── requires: static/dynamic template separation
  └── requires: token count > 1024 for system block
  └── enables: cache hit rate tracking (observability)

Manual approval gate
  └── requires: generated emails artifact (reviewer needs to see output)
  └── requires: GitHub Environment with required reviewers
  └── gates: CRM note write + status property update

CRM note write
  └── requires: manual approval gate (in non-dry-run mode)
  └── requires: HubSpot note association (contact + company)
  └── requires: timestamp accuracy (UTC)

Sequence enrollment trigger
  └── requires: status property write (asotos_sequence_status = approved)
  └── requires: HubSpot workflow pre-configured externally

Prompt smoke test in CI
  └── requires: versioned prompt files in git
  └── requires: Pydantic output schema
  └── gates: prompt changes merged to main
```

---

## MVP Recommendation

Build in this order. Each layer enables the next.

**Layer 1 — Safe pipeline skeleton (before any LLM calls)**
1. Workflow dispatch with `dry_run`, `contact_limit` inputs
2. HubSpot contact pull with 180-day silence filter
3. Run manifest artifact structure (empty shell, filled progressively)
4. Idempotency guard (read `asotos_last_run_id` property)
5. Structured JSON logging

**Layer 2 — LLM generation (single email first, then all 8)**
6. Prompt templates with static/dynamic split (system + user files)
7. Token budget check before submission
8. Single Claude API call per contact, all 8 emails in one response
9. Pydantic JSON schema validation with retry on failure
10. Generated emails artifact upload

**Layer 3 — CRM write (gated)**
11. Manual approval gate via GitHub Environment
12. HubSpot note write (email body + source transcript note)
13. Status property write (`asotos_sequence_status = generated → approved`)
14. Run manifest finalisation with token costs

**Layer 4 — Optimisation (after first successful run)**
15. Prompt caching with 1-hour TTL
16. Cache hit rate monitoring in job summary
17. Prompt smoke test in CI
18. Prompt changelog process

**Defer until after first production run:**
- Batch API (adds async complexity; save for cost-optimisation phase)
- Sequence enrollment trigger via HubSpot workflow (configure HubSpot side separately)
- Per-tone A/B analysis (needs data from multiple runs first)

---

## Anti-Features

These are features to explicitly not build. Each one is listed because it is a common suggestion that adds cost or complexity without proportionate value for this specific pipeline.

### Over-Engineering

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Dedicated message queue (SQS, PubSub)** | The pipeline runs on a schedule or manual trigger, not continuously. GitHub Actions is the scheduler and orchestrator. A queue adds infrastructure, auth, and monitoring overhead. | Use GitHub Actions artifact files as the "queue" between jobs. Failed contacts go in `failed_contacts.json`. |
| **Separate microservices per pipeline stage** | This is a ~200-contact batch that runs periodically, not a high-throughput stream. Microservices add network hops, auth tokens, and failure surfaces. | Single Python script per GitHub Actions job. Three jobs max: `extract`, `generate`, `write`. |
| **LangChain or agent orchestration framework** | ASOTOS has no tool use, no multi-step reasoning chains, no retrieval-augmented generation. LangChain adds abstraction over a simple API call with no benefit here. | Direct Anthropic SDK calls. One API call per contact. |
| **Vector database / semantic search** | No retrieval step. The relevant context (Chorus transcript, HubSpot properties) is pulled directly from APIs. | Pass context directly in the prompt. |
| **Streaming response handling** | Streaming is for real-time UIs. This is a batch pipeline. Streaming adds complexity (partial JSON, buffer management) with no UX benefit. | Use synchronous `client.messages.create()`. |
| **Real-time web scraping of prospect company sites** | Scraping adds latency, legal risk (Australian privacy law, robots.txt), and breakage when sites change. The data you'd get rarely improves email quality enough to justify it. | Use Chorus transcript and HubSpot properties as the sole personalisation data sources. If LinkedIn data is needed, use a pre-pulled data enrichment step — not runtime scraping. |
| **Multi-agent debate / adversarial review pattern** | Two LLM agents arguing about email quality multiplies token costs with no guaranteed quality improvement. Adds circular failure modes. | Single generation call with a `reasoning` field in the output schema. Human reviewer is the quality gate. |

### Over-Personalisation

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **More than 3 personalisation signals per email** | Over-personalisation reads as surveillance to recipients. "Three personal facts, two company mentions, a recent social post" degrades reply rates. | Use 2 signals max: (1) reference to past conversation topic from Chorus, (2) role/industry framing from HubSpot. |
| **Runtime LinkedIn scraping for "recent activity" signals** | Adds legal risk, rate limit complexity, and the signal degrades fast (posts become stale). | If social signals are desired, add LinkedIn data as a pre-enriched HubSpot property — not a runtime lookup. |

### Infrastructure Complexity

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Dedicated prompt management SaaS (LangSmith, Helicone, etc.)** | Adds a third-party dependency, auth complexity, and monthly cost for what is essentially a versioned text file. | Git-versioned `.j2` template files with a `PROMPT_CHANGELOG.md`. Zero cost, full history. |
| **Database for run state** | SQLite or Postgres adds infra management. GitHub Actions artifacts + HubSpot contact properties cover all required state. | Run manifest artifact for run-level state; HubSpot properties for contact-level state. |
| **OpenTelemetry tracing** | Appropriate for multi-service distributed systems. This is a single-tenant batch job. | GitHub Actions structured logs + run manifest artifact. Sufficient observability for this scale. |

---

## Sources

**Claude API / Prompt Caching:**
- [Prompt Caching — Claude API Docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — HIGH confidence, official documentation
- [Claude API Cost Optimization: Caching, Batching, and 60% Token Reduction — DEV Community](https://dev.to/whoffagents/claude-api-cost-optimization-caching-batching-and-60-token-reduction-in-production-3n49) — MEDIUM confidence, verified against official docs

**HubSpot CRM API:**
- [Engagements | Notes — HubSpot Developer Docs](https://developers.hubspot.com/docs/api-reference/crm-notes-v3/guide) — HIGH confidence, official documentation
- [HubSpot Custom Objects and Properties Architecture](https://www.hyphadev.io/blog/complete-guide-hubspot-crm-data-architecture) — MEDIUM confidence
- [Creating a note and associating to a contact — HubSpot Community](https://community.hubspot.com/t5/APIs-Integrations/Here-s-how-to-create-a-note-on-a-contact-through-the-API/m-p/1064339) — MEDIUM confidence, community-verified pattern

**LLM Output Validation:**
- [Pydantic for AI Validation](https://zenvanriel.com/ai-engineer-blog/pydantic-ai-validation/) — MEDIUM confidence
- [LLM Structured Output Validation — Reliable Patterns](https://www.glukhov.org/llm-performance/benchmarks/llm-structured-output-validation-python/) — MEDIUM confidence
- [Enforce and Validate LLM Output with Pydantic — Xebia](https://xebia.com/blog/enforce-and-validate-llm-output-with-pydantic/) — MEDIUM confidence

**GitHub Actions Pipeline Patterns:**
- [GitHub Actions Production Architecture — Markaicode](https://markaicode.com/architecture/github-actions-production-system-design-architecture/) — MEDIUM confidence
- [Manual Approval in GitHub Actions — Thomas Stringer](https://trstringer.com/github-actions-manual-approval/) — MEDIUM confidence
- [Dead-Letter Queue Patterns — OneUptime](https://oneuptime.com/blog/post/2026-02-09-dead-letter-queue-patterns/view) — MEDIUM confidence

**Prompt Template Management:**
- [Prompt Versioning and Change Management in Production AI Systems — TianPan.co](https://tianpan.co/blog/2026-03-13-prompt-versioning-change-management-production) — MEDIUM confidence
- [Prompt Versioning and Management Guide — LaunchDarkly](https://launchdarkly.com/blog/prompt-versioning-and-management/) — MEDIUM confidence

**Chorus AI API:**
- [Chorus API Documentation](https://api-docs.chorus.ai/) — MEDIUM confidence (direct API docs, access unverified)
- [Chorus API Integration Patterns — Truto](https://truto.one/integrations/detail/chorus/) — MEDIUM confidence

**B2B Email Best Practices:**
- [B2B Cold Email Best Practices 2026 — Cleverly](https://www.cleverly.co/blog/b2b-cold-email-engagement-strategies-best-practices) — MEDIUM confidence
- [7 Costly AI Cold Email Personalization Mistakes — Instantly.ai](https://instantly.ai/blog/7-costly-ai-cold-email-personalization-mistakes-startup-founders-must-avoid/) — MEDIUM confidence

**Anti-Patterns:**
- [AI Agent Anti-Patterns — Allen Chan, Medium](https://achan2013.medium.com/ai-agent-anti-patterns-part-1-architectural-pitfalls-that-break-enterprise-agents-before-they-32d211dded43) — MEDIUM confidence
