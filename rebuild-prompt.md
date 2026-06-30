# Build Prompt: GitHub Actions AI Email Campaign Automation

Use this prompt to have Claude build a similar automated outreach campaign system from scratch. Replace the sections marked **[CUSTOMISE]** with your own details.

---

## The Prompt

```
Build me a GitHub Actions pipeline that generates personalised AI outreach email campaigns for sales contacts, then writes the results back to HubSpot. Here is everything you need to know.

---

## WHAT IT DOES

When triggered, the pipeline:
1. Pulls a contact's full data from HubSpot (properties, email history, meeting notes)
2. Fetches call transcripts from Chorus AI (a call recording tool) for context
3. Builds a prompt and calls Claude API to generate a personalised email sequence + SDR call notes
4. Writes the generated content back to HubSpot contact properties and creates a note on the contact record

The trigger is a Make.com scenario that fires a GitHub Actions `workflow_dispatch` event when a contact is added to a specific HubSpot list.

---

## ARCHITECTURE

### Trigger Layer
- **Make.com** watches a HubSpot list for new contacts
- When triggered, it fires a GitHub Actions `workflow_dispatch` via the GitHub API
- Passes two inputs: `contact_id` (HubSpot numeric ID) and `contact_email`

### GitHub Actions Workflow (`.github/workflows/campaign.yml`)
- Runs on `ubuntu-latest`
- Working directory: the project subfolder (e.g. `MY_PROJECT/`)
- Secrets: `HUBSPOT_API_KEY`, `CHORUS_API_TOKEN`, `ANTHROPIC_API_KEY`
- Data flows between steps via `$RUNNER_TEMP` temp files (JSON)

Steps in order:
1. `fetch_hubspot.py` — fetches contact data, writes `hubspot_contact.json` to RUNNER_TEMP
2. `fetch_chorus.py` — fetches call transcripts, writes `chorus_transcripts.json` to RUNNER_TEMP
3. `compute_eofy_tokens.py` — [CUSTOMISE: your timing/context logic], writes `campaign_tokens.json`
4. `generate_campaign.py` — builds prompt, calls Claude API, writes `campaign_output.json`
5. `write_hubspot.py` — reads `campaign_output.json`, writes properties + note back to HubSpot
6. Upload `campaign_output.json` as a GitHub Actions artifact (7-day retention)
7. On failure: copy `failed_contacts.json` from RUNNER_TEMP and upload as `failed-contacts` artifact
8. On failure: POST Teams/Slack notification via webhook

### Python Scripts (all in `scripts/`)
- Python 3.12
- Dependencies: `hubspot-api-client>=12.0.0`, `requests>=2.31.0`, `beautifulsoup4>=4.12.0`, `anthropic>=0.30.0`, `tiktoken>=0.7.0`, `tenacity>=9.0.0`

---

## SCRIPT DETAILS

### scripts/fetch_hubspot.py
Fetches contact properties, engagement history (emails + meetings), deal stage, and owner names.

```python
# Key contact properties to fetch: [CUSTOMISE — list the HubSpot property names you need]
properties = [
    "firstname", "lastname", "email", "jobtitle", "company",
    "industry", "num_employees", "city", "country", "website",
    "hubspot_owner_id",
    # ADD YOUR CUSTOM PROPERTIES HERE
]
```

Also fetches:
- All email engagements from the past 12 months (strips HTML from bodies)
- All meeting engagements from the past 12 months (notes, internal notes, attendees)
- CRM meeting objects via v4 associations API (covers scheduler-created meetings)
- Chorus conversation IDs extracted from meeting notes via regex
- Owner first names resolved from owner IDs via `client.crm.owners.owners_api.get_by_id()`

Writes to `$RUNNER_TEMP/hubspot_contact.json`:
```json
{
  "contact_properties": { "firstname": "...", ... },
  "deal_stage": "...",
  "email_history": [...],
  "meeting_engagements": [...],
  "chorus_conversation_ids": [...]
}
```

### scripts/fetch_chorus.py
Fetches call transcripts from Chorus AI. Silent fallback on 404/401/timeout — pipeline continues with empty transcripts.

Sources for conversation IDs (merged, deduped):
1. IDs extracted from HubSpot meeting notes (regex on `chorus.ai/meeting/XXXXXXXX`)
2. Manual override via `INPUT_CHORUS_IDS` env var (comma-separated, for testing)
3. Search via Chorus v3/engagements API by company name

Transcript fetch: `GET https://chorus.ai/api/v1/conversations/{id}?fields=recording.utterances`

Writes to `$RUNNER_TEMP/chorus_transcripts.json` as array of transcript objects.

### scripts/compute_campaign_tokens.py  [CUSTOMISE THIS ENTIRELY]
Computes any campaign-specific tokens that require runtime calculation (e.g. timing context, days to deadline).

Example: for an EOFY campaign it computes:
- `current_date`: today's date
- `eofy_timing_context`: one of `pre_eofy_full` / `pre_eofy_compressed` / `post_eofy`
- `days_to_eofy`: integer days until 30 June

Writes to `$RUNNER_TEMP/campaign_tokens.json`.

### scripts/generate_campaign.py
Builds the prompt from a template file, calls Claude API, parses JSON output.

Key logic:
- Reads `hubspot_contact.json`, `chorus_transcripts.json`, `campaign_tokens.json` from RUNNER_TEMP
- Builds `activity_history` string from emails + meetings + transcripts (labelled blocks)
- Substitutes `{{token.name}}` placeholders in `prompt_template.md`
- Calls `claude-sonnet-4-6` (or your chosen model) with `max_tokens=16384`
- System prompt enforces: Australian English, no em/en dashes as separators, return only raw JSON
- Post-processes output: strips banned punctuation, validates required keys
- Writes `campaign_output.json` to RUNNER_TEMP

### scripts/write_hubspot.py
Writes AI-generated content back to HubSpot.

Writes as contact properties (all overwrite on each run):
- `subject_1` through `subject_7` — email subject lines
- `email_1` through `email_7` — email bodies (up to 65,000 chars each)
- `[YOUR_SDR_NOTES_PROP]` — SDR call notes as plain text [CUSTOMISE]
- `[YOUR_GENERATED_DATE_PROP]` — date of generation [CUSTOMISE]

Creates a HubSpot note (engagement) on the contact:
- Rich HTML note body with campaign brief + SDR call notes
- Uses POST to `https://api.hubapi.com/engagements/v1/engagements`
- Creates a NEW note on each run (does not overwrite previous notes)
- Note failure is non-fatal (logs warning, continues)

---

## RETRY & ERROR HANDLING

All four scripts implement exponential backoff retry using `tenacity`:

```python
from tenacity import retry, stop_after_attempt, stop_after_delay, wait_random_exponential, retry_if_exception

class RetryAfterWait:
    def __call__(self, retry_state):
        exc = retry_state.outcome.exception()
        if exc and hasattr(exc, 'response') and exc.response is not None:
            retry_after = exc.response.headers.get('Retry-After')
            if retry_after:
                return float(retry_after)
        return 0

RETRY_KWARGS = dict(
    retry=retry_if_exception(lambda e: _is_retryable(e)),
    wait=wait_random_exponential(multiplier=1, min=1, max=60) | RetryAfterWait(),
    stop=stop_after_attempt(6) | stop_after_delay(60),
    reraise=True,
)
```

Retry on: 429 and 5xx status codes only. 4xx (except 429) = permanent failure, no retry.

HubSpot SDK: check `e.status` on `ApiException`.
Anthropic SDK: pass `max_retries=0` to disable built-in retry before adding tenacity.
requests: check `e.response.status_code` on `HTTPError`.

On unrecovered failure, each script writes a DLQ file before re-raising:
```python
def write_dlq(contact_id, contact_email, failed_step, error_message, retry_count):
    record = {
        "contact_id": contact_id,
        "contact_email": contact_email,
        "failed_step": failed_step,
        "error_message": str(error_message)[:2000],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "retry_count": retry_count,
    }
    path = os.path.join(os.environ.get("RUNNER_TEMP", "."), "failed_contacts.json")
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
```

Failure workflow steps (both `if: failure()`):
1. Copy `$RUNNER_TEMP/failed_contacts.json` to workspace, upload as artifact
2. POST Teams/Slack notification with contact_email, failed_step, error excerpt, run log link

---

## PROMPT TEMPLATE (prompt_template.md)  [CUSTOMISE THIS ENTIRELY]

The prompt template uses `{{token.name}}` placeholders. The generate script substitutes these before calling Claude. 

Available token categories:
- `contact.*` — all contact properties from HubSpot
- `crm.full_activity_history` — assembled email thread + meeting notes + call transcripts
- `campaign.*` — campaign-specific runtime tokens (e.g. timing context)

**[CUSTOMISE]** Replace the entire prompt_template.md with your own campaign brief. The JSON output schema Claude returns must match the keys your write_hubspot.py script expects.

The current system generates:
```json
{
  "reasoning": {
    "drop_reason_classification": "...",
    "eofy_timing_context": "...",
    "meeting_evidence_check": "...",
    ...
  },
  "email_1": { "subject": "...", "body": "..." },
  "email_2": { "subject": "...", "body": "..." },
  ...
  "email_7": { "subject": "...", "body": "..." },
  "sdr_call_notes": {
    "quick_brief": "...",
    "the_hook": "...",
    "call_1_day6": { "opening_line": "...", "diagnostic_questions": [...], ... },
    ...
  }
}
```

---

## HUBSPOT CUSTOM PROPERTIES TO CREATE

Before first run, create these custom contact properties in HubSpot (Settings > Properties > Contact):

**[CUSTOMISE]** Replace with your own property names and types.

Current properties used:
- `subject_1` through `subject_7` — Single-line text
- `email_1` through `email_7` — Multi-line text
- `eofy26_sdr_notes` — Multi-line text
- `eofy26_generated_date` — Date

---

## GITHUB SECRETS REQUIRED

- `HUBSPOT_API_KEY` — HubSpot Private App token (needs contacts read/write, engagements read/write, owners read)
- `CHORUS_API_TOKEN` — Chorus token-based auth (format: `Token XXXXXXXX`)
- `ANTHROPIC_API_KEY` — Anthropic API key
- `TEAMS_WEBHOOK_URL` — Microsoft Teams incoming webhook URL (or Slack webhook)

---

## FILE STRUCTURE

```
MY_PROJECT/                         ← project subfolder (set as working-directory in workflow)
├── scripts/
│   ├── fetch_hubspot.py
│   ├── fetch_chorus.py
│   ├── compute_campaign_tokens.py  ← CUSTOMISE for your campaign
│   ├── generate_campaign.py
│   └── write_hubspot.py
├── prompt_template.md              ← CUSTOMISE: your AI prompt
├── requirements.txt
└── CLAUDE.md                       ← (optional) project notes for Claude Code

.github/
└── workflows/
    └── campaign.yml                ← GitHub Actions workflow
```

---

## WHAT TO CUSTOMISE

1. **`prompt_template.md`** — Replace entirely with your campaign brief. Keep the `{{token.name}}` syntax for variable substitution. Define the JSON output schema Claude should return.

2. **`scripts/compute_campaign_tokens.py`** — Any runtime tokens your campaign needs (dates, context labels, etc.)

3. **`scripts/write_hubspot.py`** — Update `PROP_SDR_NOTES`, `PROP_GENERATED_DATE`, `EMAIL_PROPS` to match your HubSpot property names. Update `build_note_body()` to render your output schema.

4. **`scripts/generate_campaign.py`** — Update the `tokens` dict to include any new `{{token.name}}` placeholders you added to the prompt template.

5. **`.github/workflows/campaign.yml`** — Update the `INPUT_CONTACT_EMAIL` env var on each step (needed for DLQ). Update `working-directory` and artifact paths.

6. **HubSpot custom properties** — Create the properties that match your property names before first run.

7. **Make.com scenario** — Build a watcher that detects your HubSpot list membership change and calls the GitHub Actions API: `POST https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches` with `{ "ref": "main", "inputs": { "contact_id": "...", "contact_email": "..." } }`.

---

Now build this system. Start with the folder structure, then implement each script in order, then the workflow YAML. Ask me if you need to clarify the prompt template schema or any HubSpot property names.
```
