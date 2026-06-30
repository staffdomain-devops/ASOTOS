# Phase 2: Data Fetch — Research

**Researched:** 2026-06-12
**Domain:** HubSpot CRM v3/v4 API (contacts, engagements, deals, owners, associations) + Chorus AI REST API
**Confidence:** HIGH for HubSpot SDK patterns; MEDIUM for Chorus AI (no accessible official docs — validated by community + STACK.md)

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| HUB-01 | Fetch 18 contact properties with updatedAt timestamps | `get_by_id` with `properties` list + `propertiesWithHistory` for select fields; `updated_at` on response object |
| HUB-02 | Fetch all email engagements (past 12 months), HTML stripped | CRM search `associations.contact` EQ filter on `/crm/v3/objects/emails/search`; BeautifulSoup4 `get_text()` |
| HUB-03 | Fetch all meeting engagements (past 12 months), notes + attendees | CRM search `associations.contact` EQ filter on `/crm/v3/objects/meetings/search`; meeting properties listed |
| HUB-04 | Fetch CRM meeting objects via v4 associations API | `client.crm.associations.v4.basic_api.get_page("contacts", contact_id, "meetings")` then batch-read meeting objects |
| HUB-05 | Fetch associated deals (closed-won, closed-lost, open) | v4 associations get_page for deals, then batch read with `dealstage` property |
| HUB-06 | Fetch related contacts at same company | 1) get contact's `associatedcompanyid`, 2) get company's associated contacts via v4 associations |
| HUB-07 | Resolve owner first name from `hubspot_owner_id` | `client.crm.owners.owners_api.get_by_id(owner_id)` returns `firstName`, `lastName` |
| HUB-08 | Every field must include updatedAt timestamp | Object-level `updated_at` on each fetched record; `propertiesWithHistory` for contact-level property timestamps |
| CHO-01 | Extract Chorus IDs from meeting notes, fetch transcript | Regex on `hs_meeting_body` + `hs_internal_meeting_notes`; `GET https://chorus.ai/api/v3/engagements/{id}` |
| CHO-02 | Silent fallback on 404/401/timeout | Wrap Chorus call in try/except; write `{"transcript_available": false}` sentinel on any failure; `chorus_retry` decorator from lib handles 429/5xx |
</phase_requirements>

---

## Summary

Phase 2 implements two scripts that pull real data from HubSpot and Chorus AI and write it to `$RUNNER_TEMP` as structured JSON. The Phase 1 scaffold already provides the shared lib (`api_client.py`, `file_io.py`, `dlq_writer.py`) and stub scripts that these real implementations will replace.

`fetch_hubspot.py` makes 5-7 API calls per contact: (1) contact properties, (2) email engagements search, (3) meeting engagements search via CRM search, (4) CRM meeting objects via v4 associations (covers scheduler-created meetings missed by the search approach), (5) deals via v4 associations then batch-read, (6) company contacts via v4 associations (for related contacts), (7) owner name resolution. All fetched objects expose an `updated_at` field at the top-level response object — this is what HUB-08 requires. For individual contact property timestamps, `propertiesWithHistory` returns a history array with `timestamp` per change.

`fetch_chorus.py` extracts Chorus conversation IDs from meeting notes using the regex `chorus.ai/meeting/(\w+)`, makes one `GET /api/v3/engagements/{id}` call per ID found, and writes either the transcript data or the explicit sentinel. Every failure mode — 404, 401, timeout, connection error — writes the sentinel and exits 0. The Chorus auth header is a raw token value with no `Bearer` prefix, as locked in CLAUDE.md and verified in STACK.md.

**Primary recommendation:** Use CRM search with `associations.contact` EQ filter for emails and meetings (covers logged engagements); also use v4 associations for meetings to cover HubSpot Meetings Scheduler-created records that may not appear in search results. Write a single `hubspot_contact.json` that contains all data Phase 3 needs.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| HubSpot contact properties | API/Backend (`fetch_hubspot.py`) | — | Python script calls HubSpot REST; no browser/frontend layer |
| HubSpot email + meeting engagements | API/Backend (`fetch_hubspot.py`) | — | CRM search endpoint; all fetching happens in the runner script |
| HubSpot deals + owner resolution | API/Backend (`fetch_hubspot.py`) | — | v4 associations API + owners API |
| Chorus transcript fetch | API/Backend (`fetch_chorus.py`) | — | Raw HTTP GET to Chorus REST; lib `chorus_retry` wraps it |
| Regex ID extraction | API/Backend (`fetch_chorus.py`) | — | Runs on meeting note text fetched in `fetch_hubspot.py` |
| Inter-script data bus | Runner filesystem (`$RUNNER_TEMP`) | — | Same GitHub Actions job; `file_io.py` handles all reads/writes |

---

## Standard Stack

### Core (all already in requirements.txt from Phase 1)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| hubspot-api-client | ==12.0.0 | HubSpot v3/v4 CRM API | [VERIFIED: STACK.md, PyPI 2026-06-12] Only version with full v4 Associations + CRM Notes v3 |
| requests | ==2.34.2 | Chorus AI REST calls | [VERIFIED: STACK.md, PyPI 2026-06-12] Only HTTP client needed for Chorus |
| beautifulsoup4 | ==4.15.0 | Strip HTML from `hs_email_html` / `hs_email_text` | [VERIFIED: STACK.md, PyPI 2026-06-12] `get_text()` is canonical HTML-to-plaintext |
| lxml | ==5.3.0 | Fast bs4 parser backend | [ASSUMED] — Phase 1 already noted this; verify with `pip index versions lxml` |
| tenacity | ==9.1.4 | Retry decorators (from lib/api_client.py) | [VERIFIED: STACK.md] Already implemented in Phase 1; imported, not re-implemented |

No new packages required — all dependencies are already in `requirements.txt`.

---

## Architecture Patterns

### System Architecture Diagram

```
GitHub Actions Job (generate-campaign)
  |
  v
Step: python scripts/fetch_hubspot.py
  |
  |-- HubSpot API (Private App bearer token)
  |     GET /crm/v3/objects/contacts/{id}?properties=...&propertiesWithHistory=...
  |         -> contact properties + change timestamps
  |     POST /crm/v3/objects/emails/search
  |         filter: associations.contact EQ {contact_id}, hs_timestamp GTE 12-months-ago
  |         -> email engagement objects (subject, text, html, direction, timestamp)
  |     POST /crm/v3/objects/meetings/search
  |         filter: associations.contact EQ {contact_id}, hs_timestamp GTE 12-months-ago
  |         -> meeting engagement objects (body, internal notes, start_time, outcome)
  |     GET /crm/v4/associations/contacts/{id}/meetings (v4 associations)
  |         -> meeting IDs (includes scheduler-created meetings)
  |         -> batch-read meeting properties for IDs not already in search results
  |     GET /crm/v4/associations/contacts/{id}/deals
  |         -> deal IDs
  |         -> batch-read deals (dealname, dealstage, amount, closedate)
  |     GET /crm/v4/associations/contacts/{id}/companies
  |         -> company ID
  |     GET /crm/v4/associations/companies/{company_id}/contacts
  |         -> sibling contact IDs (related contacts at same company)
  |         -> batch-read sibling contacts (firstname, lastname, jobtitle, hs_lead_status)
  |     GET /crm/owners/v2/{owner_id}
  |         -> firstName, lastName of contact owner
  |
  v
  $RUNNER_TEMP/hubspot_contact.json
  |
  v
Step: python scripts/fetch_chorus.py
  |
  |-- Read: $RUNNER_TEMP/hubspot_contact.json
  |     -> scan meeting_notes for chorus.ai/meeting/(\w+) URLs
  |
  |-- Chorus AI API (raw token, no Bearer)
  |     GET https://chorus.ai/api/v3/engagements/{conversation_id}
  |         on 404/401/timeout -> write sentinel, exit 0
  |         on success -> extract transcript text
  |
  v
  $RUNNER_TEMP/chorus_transcripts.json
```

### Output File Schemas

**`$RUNNER_TEMP/hubspot_contact.json`** (written by `fetch_hubspot.py`):
```json
{
  "contact_id": "12345678",
  "fetched_at": "2026-06-12T10:00:00Z",
  "properties": {
    "firstname": "Jane",
    "lastname": "Smith",
    "email": "jane@example.com",
    "jobtitle": "VP of Sales",
    "company": "Acme Corp",
    "industry": "Technology",
    "numberofemployees": "500",
    "city": "Sydney",
    "country": "Australia",
    "website": "https://acme.com",
    "hubspot_owner_id": "41629779",
    "name_of_target_role": "Director of Sales",
    "name_of_target_role_last_updated": "2025-11-01T00:00:00Z",
    "company_locations": "Sydney, Melbourne",
    "years_in_crm": "2.5",
    "outreach_attempt_count": "3",
    "related_contacts": "",
    "secondary_contact_name": ""
  },
  "properties_updated_at": {
    "firstname": "2025-06-01T08:30:00Z",
    "jobtitle": "2025-03-15T14:00:00Z",
    "name_of_target_role": "2025-11-01T10:00:00Z"
  },
  "contact_updated_at": "2025-11-01T10:00:00Z",
  "contact_created_at": "2023-01-15T09:00:00Z",
  "email_engagements": [
    {
      "id": "987654",
      "hs_timestamp": "2025-12-10T14:30:00Z",
      "hs_email_subject": "Following up",
      "hs_email_text": "Hi Jane, wanted to follow up...",
      "hs_email_direction": "EMAIL",
      "hs_email_status": "SENT",
      "updated_at": "2025-12-10T14:30:05Z"
    }
  ],
  "meeting_engagements": [
    {
      "id": "111222",
      "hs_timestamp": "2025-11-05T09:00:00Z",
      "hs_meeting_title": "Discovery call",
      "hs_meeting_body": "Discussed Q1 priorities. Chorus: https://chorus.ai/meeting/abc123",
      "hs_internal_meeting_notes": "Strong signal on expansion",
      "hs_meeting_start_time": "2025-11-05T09:00:00Z",
      "hs_meeting_end_time": "2025-11-05T09:30:00Z",
      "hs_meeting_outcome": "COMPLETED",
      "updated_at": "2025-11-05T09:30:10Z"
    }
  ],
  "deals": [
    {
      "id": "555666",
      "dealname": "Acme Corp Expansion",
      "dealstage": "closedwon",
      "amount": "25000",
      "closedate": "2025-06-30",
      "updated_at": "2025-07-01T12:00:00Z"
    }
  ],
  "related_contacts_detail": [
    {
      "id": "99887766",
      "firstname": "Bob",
      "lastname": "Jones",
      "jobtitle": "Director of IT",
      "email": "bob@example.com",
      "hs_lead_status": "CONNECTED",
      "updated_at": "2025-10-01T08:00:00Z"
    }
  ],
  "owner": {
    "id": "41629779",
    "firstName": "Alex",
    "lastName": "Chen",
    "email": "alex@staffdomain.com"
  }
}
```

**`$RUNNER_TEMP/chorus_transcripts.json`** — transcript found:
```json
{
  "transcript_available": true,
  "conversations": [
    {
      "conversation_id": "abc123",
      "meeting_title": "Discovery call",
      "date_time": "2025-11-05T09:00:00Z",
      "transcript": "Participant 1: Hello, thanks for joining..."
    }
  ]
}
```

**`$RUNNER_TEMP/chorus_transcripts.json`** — sentinel (no IDs found or all fetches failed):
```json
{
  "transcript_available": false,
  "conversations": []
}
```

### Recommended Project Structure (Phase 2 additions)
```
scripts/
├── lib/                       # Phase 1 — unchanged
│   ├── api_client.py          # hubspot_retry, chorus_retry imported by Phase 2 scripts
│   ├── dlq_writer.py
│   └── file_io.py
├── fetch_hubspot.py           # Phase 2: REPLACED (was stub, now real)
└── fetch_chorus.py            # Phase 2: REPLACED (was stub, now real)
```

### Pattern 1: HubSpot Client Initialisation
**What:** Standard HubSpot SDK client with Private App token
**When to use:** Top of `fetch_hubspot.py`

```python
# Source: /hubspot/hubspot-api-python — Context7 verified
from hubspot import HubSpot
from hubspot.crm.contacts.exceptions import ApiException

client = HubSpot(access_token=os.environ["HUBSPOT_API_KEY"])
```

### Pattern 2: Contact Properties Fetch with updatedAt
**What:** Fetch contact with specific properties AND property history for timestamp tracking
**When to use:** HUB-01, HUB-08 — get both current values and timestamps

Two approaches, both needed:

**Approach A — Top-level `updated_at` on the contact object (object-level timestamp):**
```python
# Source: /hubspot/hubspot-api-python — Context7 verified
contact = client.crm.contacts.basic_api.get_by_id(
    contact_id=contact_id,
    properties=[
        "firstname", "lastname", "email", "jobtitle", "company", "industry",
        "numberofemployees", "city", "country", "website", "hubspot_owner_id",
        "name_of_target_role", "name_of_target_role_last_updated",
        "company_locations", "years_in_crm", "outreach_attempt_count",
        "related_contacts", "secondary_contact_name",
        "associatedcompanyid",  # needed for related contacts fetch
        "createdate",           # needed for TOK-02 years_in_crm computation
    ],
)
# contact.updated_at is a datetime; contact.created_at is a datetime
# contact.properties is a dict of {property_name: value}
```

**Approach B — Per-property timestamps via propertiesWithHistory:**
```python
# Source: developers.hubspot.com — confirmed via web search
# Note: propertiesWithHistory cannot be combined with properties in batch read,
# but can be used on single-object get_by_id
contact_with_history = client.crm.contacts.basic_api.get_by_id(
    contact_id=contact_id,
    properties_with_history=["name_of_target_role", "jobtitle"],
)
# contact_with_history.properties_with_history is a dict of:
# { "name_of_target_role": [{"value": "...", "timestamp": "...", "sourceType": "...", ...}] }
# Use the first history entry's timestamp as the "last updated" for that property
```

**Recommendation:** Call `get_by_id` twice — once with `properties` (all 18), once with `properties_with_history` for the handful of fields where per-property timestamp matters (name_of_target_role, jobtitle). The object-level `updated_at` covers HUB-08 for most fields.

### Pattern 3: CRM Search for Email Engagements (HUB-02)
**What:** Search all email objects associated with the contact, past 12 months
**When to use:** Fetch email history for `fetch_hubspot.py`

```python
# Source: developers.hubspot.com search guide — VERIFIED
from hubspot.crm.objects.emails import ApiException as EmailApiException
import datetime

twelve_months_ago_ms = str(int(
    (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=365)).timestamp() * 1000
))

@hubspot_retry
def fetch_email_engagements(client, contact_id):
    from hubspot.crm.objects.emails import PublicObjectSearchRequest
    all_emails = []
    after = None

    while True:
        req = PublicObjectSearchRequest(
            filter_groups=[{
                "filters": [
                    {
                        "propertyName": "associations.contact",
                        "operator": "EQ",
                        "value": str(contact_id),
                    },
                    {
                        "propertyName": "hs_timestamp",
                        "operator": "GTE",
                        "value": twelve_months_ago_ms,
                    },
                ]
            }],
            properties=[
                "hs_timestamp", "hs_email_subject",
                "hs_email_text", "hs_email_html",
                "hs_email_direction", "hs_email_status",
            ],
            limit=100,
            after=after,
        )
        resp = client.crm.objects.emails.search_api.do_search(
            public_object_search_request=req
        )
        all_emails.extend(resp.results)
        if resp.paging and resp.paging.next and resp.paging.next.after:
            after = resp.paging.next.after
        else:
            break

    return all_emails
```

**Note on HTML stripping (HUB-02):** `hs_email_html` may contain the full rich HTML; `hs_email_text` is the plaintext version. Always prefer `hs_email_text`; fall back to stripping `hs_email_html` if text is empty:
```python
# Source: beautifulsoup4 docs — VERIFIED
from bs4 import BeautifulSoup

def strip_html(html_body: str) -> str:
    if not html_body:
        return ""
    soup = BeautifulSoup(html_body, "lxml")
    return soup.get_text(separator=" ", strip=True)
```

### Pattern 4: CRM Search for Meeting Engagements (HUB-03)
**What:** Search all meeting objects associated with the contact, past 12 months
**When to use:** Fetch meeting history for `fetch_hubspot.py`

```python
# Source: developers.hubspot.com search guide — VERIFIED
@hubspot_retry
def fetch_meeting_engagements(client, contact_id):
    from hubspot.crm.objects.meetings import PublicObjectSearchRequest
    all_meetings = []
    after = None

    while True:
        req = PublicObjectSearchRequest(
            filter_groups=[{
                "filters": [
                    {
                        "propertyName": "associations.contact",
                        "operator": "EQ",
                        "value": str(contact_id),
                    },
                    {
                        "propertyName": "hs_timestamp",
                        "operator": "GTE",
                        "value": twelve_months_ago_ms,
                    },
                ]
            }],
            properties=[
                "hs_timestamp", "hs_meeting_title", "hs_meeting_body",
                "hs_internal_meeting_notes", "hs_meeting_start_time",
                "hs_meeting_end_time", "hs_meeting_outcome",
                "hs_meeting_external_url", "hubspot_owner_id",
            ],
            limit=100,
            after=after,
        )
        resp = client.crm.objects.meetings.search_api.do_search(
            public_object_search_request=req
        )
        all_meetings.extend(resp.results)
        if resp.paging and resp.paging.next and resp.paging.next.after:
            after = resp.paging.next.after
        else:
            break

    return all_meetings
```

### Pattern 5: v4 Associations for CRM Meetings (HUB-04)
**What:** Fetch meeting IDs via v4 associations (covers scheduler-booked meetings that may not appear in search)
**When to use:** Supplement Pattern 4 — deduplicate by ID then batch-read any new meetings

```python
# Source: /hubspot/hubspot-api-python — Context7 verified
@hubspot_retry
def fetch_meeting_ids_v4(client, contact_id):
    """Get meeting IDs via v4 associations (catches scheduler-created meetings)."""
    meeting_ids = []
    after = None

    while True:
        resp = client.crm.associations.v4.basic_api.get_page(
            object_type="contacts",
            object_id=str(contact_id),
            to_object_type="meetings",
            after=after,
            limit=500,
        )
        for result in resp.results:
            meeting_ids.append(result.to_object_id)
        if resp.paging and resp.paging.next and resp.paging.next.after:
            after = resp.paging.next.after
        else:
            break

    return meeting_ids
```

After getting IDs, batch-read any IDs not already in the search results:
```python
# Source: /hubspot/hubspot-api-python — Context7 verified
from hubspot.crm.objects.meetings import BatchReadInputSimplePublicObjectId

def batch_read_meetings(client, meeting_ids, properties):
    inputs = [{"id": str(mid)} for mid in meeting_ids]
    req = BatchReadInputSimplePublicObjectId(
        properties=properties,
        inputs=inputs,
    )
    resp = client.crm.objects.meetings.batch_api.read(
        batch_read_input_simple_public_object_id=req
    )
    return resp.results
```

### Pattern 6: Deals via v4 Associations + Batch Read (HUB-05)
**What:** Get deal IDs associated with contact, then batch-read deal properties
**When to use:** Fetch deals for `fetch_hubspot.py`

```python
# Source: /hubspot/hubspot-api-python — Context7 verified
@hubspot_retry
def fetch_deals(client, contact_id):
    # Step 1: Get deal IDs
    deal_ids = []
    after = None
    while True:
        resp = client.crm.associations.v4.basic_api.get_page(
            object_type="contacts",
            object_id=str(contact_id),
            to_object_type="deals",
            after=after,
            limit=500,
        )
        for result in resp.results:
            deal_ids.append(result.to_object_id)
        if resp.paging and resp.paging.next and resp.paging.next.after:
            after = resp.paging.next.after
        else:
            break

    if not deal_ids:
        return []

    # Step 2: Batch-read deal properties
    from hubspot.crm.deals import BatchReadInputSimplePublicObjectId
    req = BatchReadInputSimplePublicObjectId(
        properties=["dealname", "dealstage", "amount", "closedate", "pipeline"],
        inputs=[{"id": str(did)} for did in deal_ids],
    )
    resp = client.crm.deals.batch_api.read(
        batch_read_input_simple_public_object_id=req
    )
    return resp.results
```

Deal stage values for filtering: `closedwon`, `closedlost` — all other stages = "open". [ASSUMED — exact stage internal IDs are portal-specific; the script should pass all deals to Phase 3 and let the token computation script classify by stage string value.]

### Pattern 7: Related Contacts at Same Company (HUB-06)
**What:** Find other contacts at the same company, for secondary contact selection in Phase 3
**When to use:** When `associatedcompanyid` is non-null on the target contact

```python
# Source: /hubspot/hubspot-api-python — Context7 verified (company contacts via v4 associations)
@hubspot_retry
def fetch_related_contacts(client, contact_id, company_id):
    """Get contacts at the same company, excluding the primary contact."""
    sibling_ids = []
    after = None

    while True:
        resp = client.crm.associations.v4.basic_api.get_page(
            object_type="companies",
            object_id=str(company_id),
            to_object_type="contacts",
            after=after,
            limit=500,
        )
        for result in resp.results:
            if str(result.to_object_id) != str(contact_id):
                sibling_ids.append(result.to_object_id)
        if resp.paging and resp.paging.next and resp.paging.next.after:
            after = resp.paging.next.after
        else:
            break

    if not sibling_ids:
        return []

    # Batch-read sibling contact properties
    from hubspot.crm.contacts import BatchReadInputSimplePublicObjectId
    req = BatchReadInputSimplePublicObjectId(
        properties=["firstname", "lastname", "jobtitle", "email",
                    "hs_lead_status", "lastmodifieddate"],
        inputs=[{"id": str(cid)} for cid in sibling_ids[:100]],  # cap at 100
    )
    resp = client.crm.contacts.batch_api.read(
        batch_read_input_simple_public_object_id=req
    )
    return resp.results
```

**Note:** If `associatedcompanyid` is missing on the primary contact, write `related_contacts_detail: []` and continue. Do not fail the pipeline.

### Pattern 8: Owner Name Resolution (HUB-07)
**What:** Resolve owner first name from `hubspot_owner_id` property
**When to use:** After fetching contact properties, when `hubspot_owner_id` is non-null

```python
# Source: developers.hubspot.com owners API — VERIFIED
@hubspot_retry
def fetch_owner(client, owner_id):
    """Returns owner firstName, lastName, email. Returns None if not found."""
    try:
        owner = client.crm.owners.owners_api.get_by_id(owner_id=int(owner_id))
        return {
            "id": owner.id,
            "firstName": owner.first_name,
            "lastName": owner.last_name,
            "email": owner.email,
        }
    except Exception:
        return None  # Owner lookup is non-fatal
```

Response fields confirmed: `id`, `email`, `firstName`, `lastName`, `teams`, `userId`, `createdAt`, `updatedAt`. [VERIFIED: developers.hubspot.com owners guide]

### Pattern 9: Chorus AI Transcript Fetch (CHO-01, CHO-02)
**What:** Extract Chorus IDs from meeting notes, fetch transcript, handle all failure modes silently
**When to use:** In `fetch_chorus.py`

```python
# Source: CLAUDE.md (auth format locked), STACK.md (endpoint), Nexla docs (auth verification)
import re
import requests

CHORUS_ID_PATTERN = re.compile(r"chorus\.ai/meeting/(\w+)")

def extract_chorus_ids(meeting_engagements: list) -> list:
    """Extract unique Chorus conversation IDs from meeting note fields."""
    ids = []
    for meeting in meeting_engagements:
        props = meeting.properties if hasattr(meeting, "properties") else meeting
        for field in ["hs_meeting_body", "hs_internal_meeting_notes"]:
            text = props.get(field) or ""
            ids.extend(CHORUS_ID_PATTERN.findall(text))
    return list(dict.fromkeys(ids))  # deduplicate, preserve order

@chorus_retry
def _fetch_single_transcript(conversation_id: str, token: str) -> dict:
    """Raises requests.HTTPError on non-2xx. chorus_retry handles 429/5xx only."""
    url = f"https://chorus.ai/api/v3/engagements/{conversation_id}"
    headers = {
        "Authorization": token,   # Raw token — NO 'Bearer' prefix (CLAUDE.md)
        "Content-Type": "application/json",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()

def fetch_chorus_transcripts(chorus_ids: list, token: str) -> dict:
    """
    Fetch transcripts for all Chorus IDs.
    Any 404, 401, timeout, or connection error on any single ID writes to
    conversations list as-is with available_for_id: false, but does not
    fail the function.

    Returns the full output dict to write to chorus_transcripts.json.
    """
    if not chorus_ids:
        return {"transcript_available": False, "conversations": []}

    conversations = []
    any_success = False

    for cid in chorus_ids:
        try:
            data = _fetch_single_transcript(cid, token)
            conversations.append({
                "conversation_id": cid,
                "meeting_title": data.get("title", ""),
                "date_time": data.get("date_time", ""),
                "transcript": data.get("transcript", ""),
                "available": True,
            })
            any_success = True
        except requests.exceptions.HTTPError as e:
            # 404 = recording deleted or no access; 401 = auth problem
            # Both are silent per CHO-02; do not retry (chorus_retry already exhausted 429/5xx)
            status = e.response.status_code if e.response is not None else "unknown"
            conversations.append({
                "conversation_id": cid,
                "available": False,
                "error_status": status,
            })
        except Exception:
            # Connection timeout, DNS failure, etc — all silent
            conversations.append({
                "conversation_id": cid,
                "available": False,
                "error_status": "connection_error",
            })

    return {
        "transcript_available": any_success,
        "conversations": conversations,
    }
```

### Anti-Patterns to Avoid

- **Hardcoding RUNNER_TEMP path:** Never `open("/home/runner/work/_temp/hubspot_contact.json", "w")` — always use `file_io.write_json("hubspot_contact.json", data)`. [CITED: ARCHITECTURE.md, CLAUDE.md]
- **Stopping pagination on a fixed page count:** Always loop until `paging.next.after` is absent. [CITED: PITFALLS.md PITFALL-M1]
- **Silent empty substitution for Chorus failure:** Never write `""` or `None` to the transcript field. Always write the sentinel dict `{"transcript_available": false, "conversations": []}`. [CITED: PITFALLS.md PITFALL-M2]
- **Including `hs_body_preview_html` in email search properties:** This property is not supported in search response fields — request `hs_email_html` or `hs_email_text` instead. [VERIFIED: HubSpot search docs]
- **Calling `raise_for_status()` outside the `chorus_retry` decorator:** The retry decorator only fires on `HTTPError`. If you don't call `raise_for_status()`, Chorus 5xx responses are invisible to tenacity. Always call `resp.raise_for_status()` immediately after `requests.get()`.
- **Logging `HUBSPOT_API_KEY` or `CHORUS_API_TOKEN` in exceptions:** Wrap API exceptions to log only `status_code`, never the full `repr()` of the exception object which may include auth headers. [CITED: PITFALLS.md PITFALL-M7]
- **Using `propertiesWithHistory` and `properties` together in batch read:** The HubSpot batch read API does not support both simultaneously — use them in separate `get_by_id` calls. [VERIFIED: HubSpot batch read docs]

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Exponential backoff with jitter | Custom retry loop | `hubspot_retry` / `chorus_retry` from `lib/api_client.py` | Already implemented in Phase 1; adding custom retry creates two retry paths that interact unpredictably |
| HubSpot API pagination | Manual offset counter | `while paging.next.after` cursor loop | HubSpot uses cursor-based pagination — offset pagination silently drops or duplicates records [CITED: PITFALL-M1] |
| HTML-to-text from email bodies | Regex tag stripping | `BeautifulSoup(html, "lxml").get_text()` | Regex fails on malformed HTML, nested tags, encoded entities — bs4 handles all edge cases |
| JSON file writing | `open(path, "w") + json.dump` | `file_io.write_json(filename, data)` | Centralises RUNNER_TEMP path construction; prevents hardcoded path bugs |
| Chorus auth header | OAuth or Bearer prefix | `"Authorization": raw_token` | Chorus explicitly expects no prefix; adding Bearer causes 401 |

---

## Common Pitfalls

### Pitfall 1: Pagination Cursor Loop Must Be Exhaust-Until-Done (CRITICAL)
**What goes wrong:** Code does `for page in range(10)` instead of `while cursor`. Stops after 10 pages. A contact with 12+ email engagements silently loses the last page(s). The most recent engagement may be on the final page.
**Why it happens:** Fixed-page iteration feels safe; HubSpot pages are typically small (10-100 items), so it "works in testing" with few engagements.
**How to avoid:** Always: `while True: ... if not paging.next.after: break`. Cap at 50 pages with a warning log (not a silent stop) to detect anomalous contacts. [CITED: PITFALLS.md PITFALL-M1]
**Warning signs:** Engagement count in output JSON is a round multiple of the page limit (100, 200, etc.) — this is a pagination cap fingerprint.

### Pitfall 2: v4 Associations `to_object_id` Type Mismatch
**What goes wrong:** `result.to_object_id` is returned as an integer; HubSpot SDK methods and batch read inputs expect a string. `get_page()` returns `int`, batch read inputs take `{"id": str}`.
**Why it happens:** SDK inconsistency between association retrieval (returns int) and batch read (accepts str).
**How to avoid:** Always `str(result.to_object_id)` before passing to any batch read or subsequent API call. [ASSUMED — observed in community posts; verify during implementation]

### Pitfall 3: CRM Search Does Not Cover Scheduler Meetings (HUB-04)
**What goes wrong:** Using only the CRM search with `associations.contact` EQ filter for meetings misses meetings created via the HubSpot Meetings Scheduler tool (booked links). These meetings exist as CRM objects but their creation path differs.
**Why it happens:** Scheduler-booked meetings are stored as CRM meeting objects with a different `hs_activity_type` that may not be indexed by the search endpoint in all portal configurations.
**How to avoid:** ALWAYS run both approaches: (1) CRM search for logged meetings, (2) v4 associations `get_page` for all linked meeting objects. Deduplicate by `id` before processing. [ASSUMED — pattern based on known HubSpot data model; HUB-04 requirement explicitly calls for v4 associations]
**Warning signs:** Known HubSpot-scheduled meetings not appearing in the output JSON.

### Pitfall 4: Chorus ID Regex Must Match All URL Formats
**What goes wrong:** Regex `chorus.ai/meeting/(\w+)` fails if URLs include query parameters, subdomain variants, or ZoomInfo-era URL changes. No match = no transcript without a log trace.
**Why it happens:** URL format may vary (`chorus.ai/meeting/ID?...`, `app.chorus.ai/meeting/ID`).
**How to avoid:** Use a permissive regex: `r"chorus\.ai/meeting/([\w-]+)"` (added hyphen for potential ID variants). Log the raw note text when no match found, at DEBUG level. Never silently return empty result. [CITED: PITFALLS.md PITFALL-L3]
**Warning signs:** `chorus_ids` list is always empty despite meeting notes existing in HubSpot.

### Pitfall 5: `updated_at` vs Property-Level Timestamp (HUB-08 Scoping)
**What goes wrong:** Phase 3 needs freshness tiers per-property, but the contact object's `updated_at` only tells you when ANY property changed last. A contact's `jobtitle` may have changed 2 years ago even though `updated_at` is recent (due to a different property change).
**Why it happens:** The top-level `updated_at` is the contact-level modification timestamp, not a per-property timestamp.
**How to avoid:** For properties where freshness matters to Phase 3 (at minimum: `name_of_target_role`, `name_of_target_role_last_updated`, `jobtitle`), also call `get_by_id` with `properties_with_history` and store the first history entry's `timestamp` in `properties_updated_at`. Store both: `contact_updated_at` (overall) and `properties_updated_at` (per-property dict for selected fields). [CITED: REQUIREMENTS.md HUB-08, CLAUDE.md Data Freshness]
**Warning signs:** Phase 3 freshness tiers are all identical for every contact, or all triggered as "current" despite stale CRM data.

### Pitfall 6: Chorus 401 Is a Token Problem, Not a Contact Problem
**What goes wrong:** A 401 from Chorus means the token is expired or invalid for the entire account — not just for this one conversation. Treating 401 the same as 404 (per-contact silent fallback) hides an account-level auth failure.
**Why it happens:** Both 401 and 404 look like "can't get transcript for this ID" from the code's perspective.
**How to avoid:** Log `WARNING: Chorus auth failure (401) for conversation_id={cid} — token may be invalid for all Chorus calls` separately from a 404 skip. Write the sentinel for the current run, but surface the 401 clearly in the log so operators can detect token expiry. Do NOT fail the pipeline (per CHO-02), but do NOT silently swallow the signal. [CITED: PITFALLS.md PITFALL-M2]
**Warning signs:** Every contact run produces `transcript_available: false` without any 404 noise — all 401s are being silently swallowed.

---

## Code Examples

### Complete `fetch_hubspot.py` Script Structure
```python
# Source: CLAUDE.md (DLQ + retry patterns), Pattern 1-8 above
#!/usr/bin/env python3
"""fetch_hubspot.py — Phase 2: fetch HubSpot contact data."""
import os
import sys
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

from api_client import hubspot_retry
from dlq_writer import write_dlq
from file_io import write_json, read_json

from hubspot import HubSpot
from hubspot.crm.contacts.exceptions import ApiException

CONTACT_ID = os.environ.get("CONTACT_ID", "")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")


def main():
    client = HubSpot(access_token=os.environ["HUBSPOT_API_KEY"])
    print(f"[fetch_hubspot] fetching contact_id={CONTACT_ID}")

    # 1. Contact properties
    contact = fetch_contact_properties(client, CONTACT_ID)

    # 2. Email engagements (past 12 months)
    emails = fetch_email_engagements(client, CONTACT_ID)

    # 3. Meeting engagements (CRM search path)
    meetings_search = fetch_meeting_engagements(client, CONTACT_ID)

    # 4. Meeting objects (v4 associations path — HUB-04)
    meeting_ids_v4 = fetch_meeting_ids_v4(client, CONTACT_ID)
    # Deduplicate against search results, then batch-read new ones
    meetings = merge_meetings(client, meetings_search, meeting_ids_v4)

    # 5. Deals
    deals = fetch_deals(client, CONTACT_ID)

    # 6. Related contacts at same company
    company_id = contact.properties.get("associatedcompanyid")
    related = fetch_related_contacts(client, CONTACT_ID, company_id) if company_id else []

    # 7. Owner name
    owner_id = contact.properties.get("hubspot_owner_id")
    owner = fetch_owner(client, owner_id) if owner_id else None

    # 8. Assemble output
    output = assemble_output(contact, emails, meetings, deals, related, owner)
    write_json("hubspot_contact.json", output)
    print(f"[fetch_hubspot] wrote hubspot_contact.json ({len(emails)} emails, {len(meetings)} meetings)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        write_dlq(CONTACT_ID, CONTACT_EMAIL, "fetch_hubspot", str(e))
        raise SystemExit(1)
```

### Complete `fetch_chorus.py` Script Structure
```python
# Source: CLAUDE.md (auth + sentinel pattern), Pattern 9 above
#!/usr/bin/env python3
"""fetch_chorus.py — Phase 2: fetch Chorus AI transcripts."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

from api_client import chorus_retry
from dlq_writer import write_dlq
from file_io import write_json, read_json

CONTACT_ID = os.environ.get("CONTACT_ID", "")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")
SENTINEL = {"transcript_available": False, "conversations": []}


def main():
    print(f"[fetch_chorus] processing contact_id={CONTACT_ID}")

    # Read meeting notes from hubspot_contact.json written by fetch_hubspot.py
    hubspot_data = read_json("hubspot_contact.json")
    meetings = hubspot_data.get("meeting_engagements", [])

    chorus_ids = extract_chorus_ids(meetings)
    print(f"[fetch_chorus] found {len(chorus_ids)} Chorus IDs")

    if not chorus_ids:
        write_json("chorus_transcripts.json", SENTINEL)
        return

    token = os.environ.get("CHORUS_API_TOKEN", "")
    result = fetch_chorus_transcripts(chorus_ids, token)
    write_json("chorus_transcripts.json", result)
    print(f"[fetch_chorus] transcript_available={result['transcript_available']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # DLQ + sentinel both written so downstream steps can continue
        write_dlq(CONTACT_ID, CONTACT_EMAIL, "fetch_chorus", str(e))
        write_json("chorus_transcripts.json", SENTINEL)
        raise SystemExit(1)
```

### `updated_at` Field Paths Summary
```python
# For each object type, the updated_at timestamp is at:

# Contact object (from get_by_id):
contact.updated_at          # datetime object, overall contact modified_at
contact.created_at          # datetime object, for years_in_crm computation

# Email engagement object (from search):
email_result.updated_at     # datetime; also email_result.properties["hs_timestamp"]

# Meeting engagement object (from search):
meeting_result.updated_at   # datetime; also meeting_result.properties["hs_timestamp"]

# Deal object (from batch_api.read):
deal_result.updated_at      # datetime

# Sibling contact (from batch_api.read):
sibling_result.updated_at   # datetime

# Owner (from owners_api.get_by_id):
owner.updated_at            # datetime (Owner record updated_at, not user activity)

# Serialize to ISO string for JSON:
obj.updated_at.isoformat() if obj.updated_at else None
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| HubSpot Engagements v1 API | CRM v3 objects/emails + meetings + CRM v4 Associations | 2022-2023 | v1 deprecated; use CRM search + v4 associations |
| Legacy `hs_all_owner_ids` patterns | `hubspot_owner_id` + owners API | 2022 | Single owner per contact; owners API returns firstName/lastName |
| HubSpot v1 Contact Lists sunset | HubSpot moved to date-versioned APIs (2026-03) | April 2026 | Use `/crm/v3/` or `/crm/objects/2026-03/`; v1 Lists sunset April 30, 2026 |
| Chorus `/v1/` transcript endpoint | `/api/v3/engagements/{id}` | ~2022 | v3 is current; v1 accessible but not recommended |

**Deprecated/outdated:**
- HubSpot Engagements v1 (`/engagements/v1/`): Deprecated; the Python SDK 12.x does not expose it under `crm`. Use CRM search for emails/meetings.
- `Bearer` prefix on Chorus token: Never correct for Chorus; raw token only.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `lxml==5.3.0` is current stable | Standard Stack | Wrong pin causes install failure on `pip install -r requirements.txt`; verify with `pip index versions lxml` |
| A2 | `result.to_object_id` from v4 associations is int (requires `str()` cast for batch read) | Pattern 5, Pattern 6 | Type error in batch read; trivial fix but will break at runtime if not addressed |
| A3 | CRM search with `associations.contact` EQ covers all logged email and meeting engagements | Pattern 3, Pattern 4 | Some engagement types may not appear in search; v4 associations supplement covers this for meetings (HUB-04); email-only contact records are fine |
| A4 | `chorus.ai/api/v3/engagements/{id}` is the correct and current transcript endpoint | Pattern 9 | Chorus/ZoomInfo may have changed endpoint post-acquisition; validate in smoke test against a known conversation ID (STATE.md blocker) |
| A5 | Deal `dealstage` values use internal names like `closedwon`/`closedlost` (portal-specific) | Pattern 6 | Phase 3 token computation must handle portal-specific stage values; pass all deals and let Phase 3 classify |
| A6 | `propertiesWithHistory` can be used on `get_by_id` (not batch read) for per-property timestamps | Pattern 2 | If SDK 12.x does not support this parameter on `basic_api.get_by_id`, fall back to storing only `contact.updated_at` for all properties |
| A7 | `client.crm.owners.owners_api.get_by_id(owner_id=int(...))` is the correct SDK call | Pattern 8 | If owner_id type is wrong or method name differs in SDK 12.x, owner fetch returns empty and pipeline continues (non-fatal) |

---

## Open Questions (RESOLVED)

1. **Chorus endpoint validity post-ZoomInfo acquisition** — RESOLVED (non-fatal by design)
   - Resolution: Plan 02-02 Task 2 includes a live smoke test. If the endpoint has changed, `_fetch_single_transcript` catches the error and writes the sentinel `{"transcript_available": false}` — CHO-02 guarantees this is non-fatal. Endpoint validity is confirmed or redirected at first run without blocking the pipeline.

2. **`hubspot.crm.objects.emails` and `.meetings` module paths in SDK 12.x** — RESOLVED (runtime verification)
   - Resolution: Plan 02-01 Task 1 Step 0 verifies the import path at execution time. If the primary path fails, the fallback is `from hubspot.crm.objects import PublicObjectSearchRequest` with `object_type="emails"` passed to the search call — identical behaviour either way.

3. **`associatedcompanyid` vs v4 associations for company lookup** — RESOLVED (property first, empty-list fallback)
   - Resolution: `fetch_related_contacts` reads `associatedcompanyid` from the already-fetched contact properties dict. If null, the function returns an empty list — a contact with no company association has no related contacts, which Phase 3 handles gracefully via the secondary contact selection fallback. No additional API call needed.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| hubspot-api-client | `fetch_hubspot.py` | ✓ (in requirements.txt) | ==12.0.0 | — |
| requests | `fetch_chorus.py` | ✓ (in requirements.txt) | ==2.34.2 | — |
| beautifulsoup4 | `fetch_hubspot.py` (HTML strip) | ✓ (in requirements.txt) | ==4.15.0 | — |
| lxml | `fetch_hubspot.py` (bs4 parser) | ✓ (in requirements.txt) | ==5.3.0 [ASSUMED] | Fall back to `html.parser` if lxml install fails |
| HubSpot Private App API key | All HubSpot calls | Must exist in repo secrets | — | Pipeline fails at HubSpot init |
| Chorus API token | `fetch_chorus.py` | Must exist in repo secrets | — | CHO-02: silent sentinel if token absent or invalid |
| Network access to api.hubapi.com | `fetch_hubspot.py` | ✓ (GitHub-hosted runner) | — | — |
| Network access to chorus.ai | `fetch_chorus.py` | ✓ (GitHub-hosted runner) | — | CHO-02 sentinel fallback |

**Missing dependencies with no fallback:**
- `HUBSPOT_API_KEY` secret — must exist and be valid before Phase 2 execution. Workflow will fail at `HubSpot(access_token=...)` if absent.

**Missing dependencies with fallback:**
- `CHORUS_API_TOKEN` — absent or invalid token produces 401 → sentinel written → pipeline continues.
- `lxml` — if install fails, `html.parser` is Python stdlib and works as bs4 backend (slower, slightly less tolerant of malformed HTML).

---

## Validation Architecture

> `nyquist_validation` is explicitly set to `false` in `.planning/config.json`. This section is omitted.

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | API keys via GitHub Secrets — not app-layer auth |
| V3 Session Management | No | Stateless per-run execution |
| V4 Access Control | No | GitHub repo + HubSpot Private App scopes control access |
| V5 Input Validation | Yes — `contact_id` used in API calls | Validate `CONTACT_ID` is numeric before use; non-numeric ID causes HubSpot 404, not injection |
| V6 Cryptography | No | TLS handled by requests + HubSpot SDK |

### Known Threat Patterns for This Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| API key in exception logs | Information Disclosure | Catch `ApiException` and log only `e.status`; never log `str(e)` or `repr(e)` which includes request headers in SDK exceptions |
| Chorus token in log output | Information Disclosure | Token is passed directly in Authorization header; log only status codes, never the token value |
| contact_email logged in print statements | Information Disclosure | Log only `contact_id` in all print statements; `contact_email` is not a secret but is PII |
| HubSpot 422 retried as if transient | Tampering/DoS | `hubspot_retry` in `api_client.py` only retries 429/5xx; 422 (property type error) fails immediately |

---

## Sources

### Primary (HIGH confidence)
- `/hubspot/hubspot-api-python` — Context7, verified 2026-06-12: associations v4 pattern, get_by_id, batch read, search API, deals, companies
- `C:\Users\irahfo\Outreach\Asotos\CLAUDE.md` — canonical spec for auth headers, retry policy, DLQ, Chorus auth
- `C:\Users\irahfo\Outreach\Asotos\.planning\research\STACK.md` — library versions, HubSpot auth pattern, Chorus endpoint, all confirmed
- `/websites/developers_hubspot` — Context7, verified 2026-06-12: owners API response shape, CRM search `associations.contact` filter syntax, email/meeting properties
- [developers.hubspot.com/docs/api-reference/search/guide](https://developers.hubspot.com/docs/api-reference/search/guide) — `associations.contact` pseudo-property EQ filter confirmed
- [developers.hubspot.com/docs/api-reference/latest/crm/owners/get-owner](https://developers.hubspot.com/docs/api-reference/latest/crm/owners/get-owner) — `firstName`, `lastName` on owner object confirmed

### Secondary (MEDIUM confidence)
- [docs.nexla.com — Chorus AI API auth](https://docs.nexla.com/user-guides/connectors/chorus_ai_api/chorus_ai_api_auth) — raw token, no Bearer prefix confirmed; validation endpoint `GET /api/v1/me`
- [community.hubspot.com — pagination out-of-order bug](https://community.hubspot.com/t5/APIs-Integrations/BUG-API-with-Pagination-return-out-of-order-and-duplicated-rows/) — exhaust-cursor approach validated
- [linuxbeast.com — HubSpot v4 associations Python](https://linuxbeast.com/blog/how-to-use-hubspot-v4-associations-api-in-python/) — labels endpoint for dynamic association type discovery

### Tertiary (LOW confidence)
- `chorus.ai/api/v3/engagements/{id}` transcript endpoint — from STACK.md + community references; not verified against accessible official Chorus docs (api-docs.chorus.ai was inaccessible). Flagged as A4 in Assumptions Log; validate in smoke test.
- Deal stage internal values (`closedwon`, `closedlost`) — standard HubSpot portal defaults; actual portal may use custom pipeline stages

---

## Metadata

**Confidence breakdown:**
- HubSpot SDK patterns: HIGH — Context7 + official docs for all major patterns
- HubSpot search + associations: HIGH — official docs confirmed `associations.contact` filter syntax
- Owner API: HIGH — official docs confirmed response shape with firstName/lastName
- Chorus auth format: MEDIUM — confirmed by Nexla connector docs + STACK.md; not verified against live Chorus API in this session
- Chorus transcript endpoint: LOW — STACK.md carries this forward from prior research; must validate in smoke test (STATE.md pre-Phase-2 blocker)
- Output JSON schema: MEDIUM — designed to satisfy Phase 3 token computation requirements; actual field names need cross-checking against Phase 3 planning

**Research date:** 2026-06-12
**Valid until:** 2026-09-12 (stable stack; re-verify lxml version; re-check Chorus endpoint if ZoomInfo migration continues)
