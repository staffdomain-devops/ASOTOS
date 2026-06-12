# Architecture: GitHub Actions AI Email Campaign Pipeline (ASOTOS)

**Project:** ASOTOS for Staff Domain
**Researched:** 2026-06-12
**Confidence:** HIGH — all major claims verified against official GitHub Actions documentation and current sources

---

## System Overview

ASOTOS is a sequential data-enrichment pipeline triggered on-demand via `workflow_dispatch`. Make.com fires a POST to the GitHub REST API, passing a `contact_id` (and optionally a `campaign_id`). A single GitHub Actions job runs five Python scripts in series, each consuming the outputs of the previous, before writing the final 8-email sequence back to HubSpot.

**Trigger chain:**

```
HubSpot Contact Enrollment
  → Make.com Scenario (HTTP module)
    → GitHub REST API POST /repos/.../actions/workflows/.../dispatches
      → workflow_dispatch fires
        → GitHub Actions Job: generate-campaign
          → fetch_hubspot.py
          → fetch_chorus.py
          → compute_campaign_tokens.py
          → generate_campaign.py  (calls Claude API)
          → write_hubspot.py
          → upload artifact + notify
```

The pipeline is intentionally a single job with five sequential steps, not five separate jobs. This is the correct design for this use case (explained in Decision Rationale).

---

## Component Map

### External Actors

| Actor | Direction | What It Does |
|-------|-----------|--------------|
| Make.com | Inbound trigger | Fires `workflow_dispatch` via GitHub REST API with `contact_id` and `campaign_id` inputs |
| HubSpot API | Read + Write | Source of contact, activity history, deal data; destination for campaign output |
| Chorus AI API | Read | Source of call transcripts and conversation intelligence |
| Anthropic Claude API | Read | Receives assembled prompt + tokens, returns 8-email JSON |
| Teams Webhook | Write | Receives failure notifications |

### Internal Components

| Component | Type | Responsibility |
|-----------|------|----------------|
| `.github/workflows/generate_campaign.yml` | Workflow definition | Orchestrates all steps, holds env wiring, concurrency group, failure routing |
| `scripts/fetch_hubspot.py` | Python script | Authenticates to HubSpot, fetches contact fields + activity history + deals, writes `hubspot_contact.json` |
| `scripts/fetch_chorus.py` | Python script | Authenticates to Chorus AI, fetches transcripts for contact domain/email, writes `chorus_transcripts.json` |
| `scripts/compute_campaign_tokens.py` | Python script | Reads both JSON files, computes derived tokens (state classification, freshness tiers, secondary contact selection), writes `campaign_tokens.json` |
| `scripts/generate_campaign.py` | Python script | Assembles prompt from all prior JSON, calls Claude API, writes `campaign_output.json` |
| `scripts/write_hubspot.py` | Python script | Reads `campaign_output.json`, writes 8-email sequence to HubSpot (custom object or notes/tasks), marks contact enrolled |
| `lib/api_client.py` | Shared utility | Authenticated HTTP client with tenacity retry logic, shared by all scripts |
| `lib/dlq_writer.py` | Shared utility | Writes structured failure records to `$RUNNER_TEMP/dlq_entry.json`; consumed by the failure notification step |
| `lib/file_io.py` | Shared utility | Typed read/write helpers for temp JSON files using `$RUNNER_TEMP` paths; centralises path construction |

### Component Boundaries

```
┌─────────────────────────────────────────────────────────┐
│  GitHub Actions Runner                                   │
│                                                          │
│  ┌──────────────┐    RUNNER_TEMP/*.json    ┌──────────┐  │
│  │ Python       │ ──────────────────────► │ Python   │  │
│  │ Scripts      │ ◄────────────────────── │ Scripts  │  │
│  │ (read/write) │                         │ (next)   │  │
│  └──────────────┘                         └──────────┘  │
│           │                                     │        │
│           ▼                                     ▼        │
│  ┌─────────────────────────────────────────────────┐    │
│  │  lib/ (shared: api_client, dlq_writer, file_io)│    │
│  └─────────────────────────────────────────────────┘    │
│                                                          │
└─────────────────────────────────────────────────────────┘
         │                │                  │
         ▼                ▼                  ▼
    HubSpot API     Chorus AI API     Anthropic API
```

---

## Data Flow

### Input: Make.com → GitHub Actions

Make.com sends a POST to:
```
POST https://api.github.com/repos/OWNER/REPO/actions/workflows/generate_campaign.yml/dispatches
Authorization: Bearer <GITHUB_PAT>
Content-Type: application/json

{
  "ref": "main",
  "inputs": {
    "contact_id": "12345678",
    "campaign_id": "ASOTOS"
  }
}
```

The workflow receives these as `${{ inputs.contact_id }}` and `${{ inputs.campaign_id }}`.

**Note (HIGH confidence):** As of February 2026, the workflow dispatch API returns the run ID in the response. Make.com can optionally poll `/repos/.../actions/runs/{run_id}` for status.

### Inputs → Environment Variables → Python

Workflow inputs are wired to environment variables at the job level:

```yaml
jobs:
  generate-campaign:
    env:
      CONTACT_ID: ${{ inputs.contact_id }}
      CAMPAIGN_ID: ${{ inputs.campaign_id }}
      HUBSPOT_API_KEY: ${{ secrets.HUBSPOT_API_KEY }}
      CHORUS_API_KEY: ${{ secrets.CHORUS_API_KEY }}
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      TEAMS_WEBHOOK_URL: ${{ secrets.TEAMS_WEBHOOK_URL }}
      RUNNER_TEMP: ${{ runner.temp }}
```

Python scripts access these via `os.environ.get("CONTACT_ID")`. Secrets are automatically masked in GitHub Actions logs; they never appear in log output.

**Wire secrets at job level, not step level.** This prevents repetition across 5+ steps and ensures every script in the job has access without re-declaration.

### Inter-Step Data Flow: `$RUNNER_TEMP`

`$RUNNER_TEMP` resolves to `/home/runner/work/_temp` on GitHub-hosted runners. It is:
- Shared across all steps within the same job
- Cleared at the start and end of each job (not between steps)
- Not shared between jobs
- Not available inside Docker container actions (not relevant here)

**This is the correct pattern for this pipeline.** All five scripts run in the same job, so `$RUNNER_TEMP` is the right inter-step mechanism. The data never needs to outlive the job; if it does (for debugging), use the artifact upload step.

```
Step 1 fetch_hubspot.py      → writes  $RUNNER_TEMP/hubspot_contact.json
Step 2 fetch_chorus.py       → writes  $RUNNER_TEMP/chorus_transcripts.json
Step 3 compute_campaign_tokens.py
                             → reads   $RUNNER_TEMP/hubspot_contact.json
                             → reads   $RUNNER_TEMP/chorus_transcripts.json
                             → writes  $RUNNER_TEMP/campaign_tokens.json
Step 4 generate_campaign.py  → reads   $RUNNER_TEMP/campaign_tokens.json
                             → reads   $RUNNER_TEMP/hubspot_contact.json  (for prompt assembly)
                             → writes  $RUNNER_TEMP/campaign_output.json
Step 5 write_hubspot.py      → reads   $RUNNER_TEMP/campaign_output.json
                             → writes  HubSpot (external)
```

### Failure Path Data Flow

When any step fails, the DLQ writer and notification step run:

```
Any step fails
  → dlq_writer.py writes $RUNNER_TEMP/dlq_entry.json
    {
      "contact_id": "...",
      "campaign_id": "...",
      "failed_step": "fetch_chorus",
      "error": "...",
      "run_url": "...",
      "timestamp": "..."
    }
  → upload-artifact (if: failure()) uploads $RUNNER_TEMP/*.json
    artifact name: "debug-run-${{ github.run_id }}"
    retention-days: 14
  → Teams notification step (if: failure()) POSTs webhook payload
    includes: contact_id, run URL, failed step name, error excerpt
```

### Output: GitHub Actions → HubSpot

`write_hubspot.py` PATCHes/POSTs back to HubSpot using the HubSpot API. The exact endpoint depends on where emails are stored (custom object vs engagement). This is a pipeline-design decision resolved during Phase 1 implementation.

---

## Build Order

Dependencies between components drive the build sequence. Each phase produces a runnable (if partial) pipeline.

### Phase 1: Scaffold + Trigger Plumbing

**Goal:** Workflow fires from Make.com, runner boots, secrets are accessible.

Build in this order:
1. Create `.github/workflows/generate_campaign.yml` with `workflow_dispatch` trigger, input definitions, concurrency group, and job skeleton
2. Add `requirements.txt` (requests, anthropic, tenacity, python-dotenv)
3. Create `lib/file_io.py` — typed helpers for `$RUNNER_TEMP` path construction and JSON read/write
4. Create `lib/api_client.py` — authenticated HTTP client with tenacity retry decorator; placeholder implementations for HubSpot, Chorus, Anthropic
5. Smoke test: trigger from GitHub UI with a test `contact_id`, confirm runner boots and env vars are populated

**Why first:** All subsequent scripts depend on the shared lib. Workflow plumbing must exist before scripts can run.

### Phase 2: Data Fetch Scripts

**Goal:** Real contact and transcript data lands in `$RUNNER_TEMP`.

Build in this order:
1. `scripts/fetch_hubspot.py` — fetch contact properties + full activity history + deals; write `hubspot_contact.json`
2. `scripts/fetch_chorus.py` — fetch transcripts by contact email/domain; write `chorus_transcripts.json`; handle gracefully when no transcripts exist (write empty array, do not fail)

**Why before tokens:** `compute_campaign_tokens.py` is pure computation over the fetched data. Separating fetch from compute makes each script independently testable.

### Phase 3: Token Computation

**Goal:** Structured, validated tokens ready for Claude prompt assembly.

1. `scripts/compute_campaign_tokens.py` — implement all Data Honesty Rules from the prompt spec: STATE classification, freshness tier assignment, secondary contact selection logic; write `campaign_tokens.json`

**Why separate script:** This is the most complex business logic. Isolating it means it can be unit-tested with fixture JSON without hitting any external API.

### Phase 4: Campaign Generation

**Goal:** Claude produces 8-email JSON.

1. `scripts/generate_campaign.py` — assemble prompt from tokens + contact JSON, call Claude API with correct model/parameters, parse and validate JSON response, write `campaign_output.json`
2. Add response validation: confirm all 8 email keys present, subject + body non-empty

**Why after tokens:** The prompt assembly depends on the `campaign_tokens.json` schema being stable. Validate that schema in Phase 3 before building the Claude integration.

### Phase 5: HubSpot Write-Back

**Goal:** Pipeline completes end-to-end.

1. `scripts/write_hubspot.py` — read `campaign_output.json`, write to HubSpot (confirm target object/endpoint in Phase 1 research spike), mark contact enrolled

### Phase 6: Failure Handling + Observability

**Goal:** Pipeline fails safely, notifications fire, debug artifacts persist.

1. `lib/dlq_writer.py` — structured failure record writer
2. Add `if: failure()` artifact upload step to workflow
3. Add `if: failure()` Teams notification step to workflow
4. Test failure path by intentionally breaking `fetch_chorus.py` and confirming artifact + notification

**Why last:** Failure handling wraps a working pipeline. Building it before the happy path is debugged creates noise.

---

## Decision Rationale

### 1. Single Job, Multiple Steps (not multiple jobs)

**Decision:** All five scripts run as steps in one job.

**Rationale:** Steps within a job share the runner filesystem, including `$RUNNER_TEMP`. If the pipeline were split into multiple jobs, each job would run on a fresh runner instance. Data would then need to move via artifacts (upload + download), which adds latency, complexity, and failure modes. For a sequential pipeline with no parallelism opportunities, single-job is strictly better.

**Exception:** If a future requirement needed parallel fetch (HubSpot and Chorus fetched simultaneously), two fetch jobs with `needs` dependency on a shared setup job would be appropriate. That opportunity does not exist in the current spec.

**Source:** GitHub Actions community discussion — "One big job vs multiple jobs" (confirmed: single job preferred when steps are sequential and share state).

### 2. `$RUNNER_TEMP` is the Right Inter-Step Pattern

**Decision:** Use `$RUNNER_TEMP` for all inter-step JSON files.

**Rationale:**
- Scoped to the job, cleaned up automatically at job end
- Not visible to other workflow runs (security boundary)
- Faster than writing to `$GITHUB_WORKSPACE` (no git contamination risk)
- Standard documented pattern for ephemeral inter-step data

**Alternative considered: Step outputs (`GITHUB_OUTPUT`)**
Step outputs are string-only and size-limited (suitable for a single ID or flag, not multi-KB JSON). Not appropriate for structured data payloads.

**Alternative considered: `$GITHUB_WORKSPACE`**
`$GITHUB_WORKSPACE` is the repository checkout directory. Writing pipeline intermediates there risks accidental git staging and clutters the workspace. Use it only for files that should be committed back.

**Alternative considered: Artifacts**
Artifacts are designed for cross-job and cross-workflow data sharing. Within a single job, artifacts are over-engineered and slower. Use them only for post-run debugging (failure artifact upload) or when data must outlive the run.

**Source:** Official GitHub Actions docs + nesin.io RUNNER_TEMP breakdown (confirmed: "cleared at start and end of each job, job-specific and not shared between jobs").

### 3. Secrets via Job-Level `env`, Not Step-Level

**Decision:** Wire all secrets and dispatch inputs as environment variables at the `jobs.<job_id>.env` level.

**Rationale:** All five Python scripts need the same credentials. Declaring at job level eliminates repetition, reduces human error, and makes the credential surface auditable in one place. Python reads them via `os.environ.get()`. GitHub Actions automatically masks secret values in all log output.

**Security note:** Never pass secrets as command-line arguments. Command-line arguments are visible in process listings. Env vars are not exposed to other processes on the runner.

**Source:** GitHub Docs "Using secrets in GitHub Actions" — confirmed masking behavior and env var injection pattern.

### 4. Concurrency Group Scoped to `contact_id`

**Decision:** Use `concurrency: group: "asotos-${{ inputs.contact_id }}"` with `cancel-in-progress: false`.

**Rationale:** Make.com could fire the same `contact_id` twice in quick succession (retry logic, human error, race condition). A `contact_id`-scoped concurrency group ensures only one pipeline runs per contact at a time. `cancel-in-progress: false` means the second trigger is queued, not discarded — safe for email generation.

**Source:** GitHub Actions concurrency docs confirm `inputs` context is valid in concurrency expressions.

### 5. `if: failure()` over `if: always()` for Notifications

**Decision:** Use `if: failure()` for the Teams notification step and artifact upload.

**Rationale:** `if: always()` fires on cancellation as well as failure, which creates alert noise. `if: failure()` fires only when a preceding step has failed, which is the correct signal for a DLQ-style notification. For the artifact upload specifically, `if: failure()` is also correct — artifacts from successful runs are not needed for debugging.

**Exception:** If success audit logging is required later, a separate `if: success()` step can post to a different Teams channel or a log store.

**Source:** GitHub Actions notification blog posts confirm `if: failure()` is the standard pattern for alerting-only steps.

### 6. Shared `lib/` Module, Not Duplicated Utilities

**Decision:** Place retry logic (`api_client.py`), DLQ writing (`dlq_writer.py`), and file I/O helpers (`file_io.py`) in a `lib/` directory, imported by all scripts.

**Rationale:** Five scripts need the same retry decorator, the same `$RUNNER_TEMP` path construction, and the same DLQ record format. Duplication creates divergence — if retry behaviour needs updating, five files need editing. A shared lib is imported with `sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))` in each script.

**Retry library:** Use `tenacity` for exponential backoff with jitter. Configuration: `wait_exponential(multiplier=1, min=2, max=30)`, `stop_after_attempt(5)`, reraise on final failure. Appropriate for HubSpot (429 rate limit), Chorus (intermittent 5xx), and Anthropic (overload errors).

**Source:** tenacity documentation + GitHub community patterns for Python retry.

### 7. `workflow_dispatch` Input Schema

**Decision:** Define exactly these inputs in the workflow:

```yaml
on:
  workflow_dispatch:
    inputs:
      contact_id:
        description: "HubSpot Contact ID"
        required: true
        type: string
      campaign_id:
        description: "Campaign identifier (default: ASOTOS)"
        required: false
        type: string
        default: "ASOTOS"
```

`contact_id` is required and drives all downstream data fetches. `campaign_id` is optional with a default, allowing the workflow to be reused for future campaigns without a Make.com config change.

**Make.com integration:** In Make.com's HTTP module, set method `POST`, URL to the dispatch endpoint, headers `Authorization: Bearer <PAT>` and `Content-Type: application/json`, body as JSON with `ref` and `inputs`. As of February 2026, the API response includes the `run_id`, which Make.com can store for status polling.

---

## Workflow Skeleton (Reference)

```yaml
name: ASOTOS Campaign Generator

on:
  workflow_dispatch:
    inputs:
      contact_id:
        description: "HubSpot Contact ID"
        required: true
        type: string
      campaign_id:
        description: "Campaign ID"
        required: false
        type: string
        default: "ASOTOS"

concurrency:
  group: "asotos-${{ inputs.contact_id }}"
  cancel-in-progress: false

jobs:
  generate-campaign:
    runs-on: ubuntu-latest
    env:
      CONTACT_ID: ${{ inputs.contact_id }}
      CAMPAIGN_ID: ${{ inputs.campaign_id }}
      HUBSPOT_API_KEY: ${{ secrets.HUBSPOT_API_KEY }}
      CHORUS_API_KEY: ${{ secrets.CHORUS_API_KEY }}
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      TEAMS_WEBHOOK_URL: ${{ secrets.TEAMS_WEBHOOK_URL }}

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
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

      - name: Generate campaign (Claude)
        run: python scripts/generate_campaign.py

      - name: Write campaign to HubSpot
        run: python scripts/write_hubspot.py

      - name: Upload debug artifacts
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: "debug-${{ github.run_id }}-${{ inputs.contact_id }}"
          path: "${{ runner.temp }}/*.json"
          retention-days: 14
          if-no-files-found: warn

      - name: Notify Teams on failure
        if: failure()
        uses: actions/github-script@v7
        env:
          TEAMS_WEBHOOK_URL: ${{ secrets.TEAMS_WEBHOOK_URL }}
        with:
          script: |
            const payload = {
              "@type": "MessageCard",
              "themeColor": "FF0000",
              "title": "ASOTOS Pipeline Failed",
              "text": `Contact: ${process.env.CONTACT_ID} | Run: ${context.runId}`,
              "potentialAction": [{
                "@type": "OpenUri",
                "name": "View Run",
                "targets": [{ "os": "default", "uri": `${context.serverUrl}/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId}` }]
              }]
            };
            await fetch(process.env.TEAMS_WEBHOOK_URL, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload)
            });
```

---

## Scalability Considerations

| Concern | Current (1-10 contacts/day) | Medium (100+/day) | High (1000+/day) |
|---------|----------------------------|-------------------|-----------------|
| Concurrency | Per-contact group prevents duplicates; GitHub-hosted runners handle burst | Monitor GitHub Actions concurrency limits (20 concurrent jobs on Free, 60 on Team) | Consider self-hosted runners or splitting across repos |
| HubSpot API rate limits | 150 req/10s (Private Apps); tenacity retry handles burst | Implement token-bucket throttle in `api_client.py` | Batch contact fetches before dispatching |
| Anthropic API latency | Single call per run ~10-30s; acceptable | Monitor prompt token count; Claude max context far exceeds ASOTOS prompt | No change needed at this scale |
| Artifact storage | 14-day retention on failure only; minimal storage | Fine | Fine |
| Chorus API | No published rate limits; tenacity retry sufficient | Implement per-domain deduplication before fetching | Cache transcripts if same contact re-enrolled |

---

## Sources

- [GitHub Docs: Store and share data from a workflow](https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/storing-and-sharing-data-from-a-workflow)
- [GitHub Docs: Concurrency](https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency)
- [GitHub Docs: Using secrets in GitHub Actions](https://docs.github.com/en/actions/security-guides/using-secrets-in-github-actions)
- [GitHub Docs: Workflow syntax — workflow_dispatch](https://docs.github.com/actions/using-workflows/workflow-syntax-for-github-actions)
- [GitHub Docs: REST API — workflow dispatch](https://docs.github.com/en/rest/actions/workflows)
- [GitHub Changelog: Workflow dispatch API returns run IDs (Feb 2026)](https://github.blog/changelog/2026-02-19-workflow-dispatch-api-now-returns-run-ids/)
- [GitHub Docs: Contexts — inputs context](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts)
- [DEV Community: Understanding GitHub Actions Working Directory](https://dev.to/jajera/understanding-github-actions-working-directory-550o)
- [Nesin.io: RUNNER_TEMP in GitHub Actions](https://nesin.io/blog/temp-directory-path-github-actions)
- [actions/upload-artifact README](https://github.com/actions/upload-artifact)
- [OneUptime: Workflow dispatch inputs](https://oneuptime.com/blog/post/2025-12-20-workflow-dispatch-inputs-github-actions/view)
- [OneUptime: GitHub Actions notifications](https://oneuptime.com/blog/post/2025-12-20-github-actions-notifications/view)
- [Tenacity documentation](https://tenacity.readthedocs.io/)
- [Earthly Blog: GitHub Actions artifacts](https://earthly.dev/blog/github-action-artifacts/)
