#!/usr/bin/env python3
"""fetch_hubspot.py — Phase 2: fetch contact, engagements, deals, related contacts, and owner from HubSpot.

Output: hubspot_contact.json written to RUNNER_TEMP.
"""
import datetime
import os
import re
import sys

import hubspot
from hubspot.crm.contacts import ApiException
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from api_client import hubspot_retry
from dlq_writer import write_dlq
from file_io import write_json

CONTACT_ID = os.environ.get("CONTACT_ID", "")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")

TWELVE_MONTHS_AGO_MS = str(int(
    (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=365)).timestamp() * 1000
))

CONTACT_PROPERTIES = [
    "firstname", "lastname", "email", "jobtitle", "company",
    "industry", "numberofemployees", "city", "country",
    "website", "hubspot_owner_id",
    "name_of_target_role", "name_of_target_role_last_updated",
    "company_locations", "years_in_crm", "outreach_attempt_count",
    "related_contacts", "secondary_contact_name",
    "associatedcompanyid", "createdate",
]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def strip_html(html_body: str) -> str:
    if not html_body:
        return ""
    try:
        return BeautifulSoup(html_body, "lxml").get_text(separator=" ", strip=True)
    except Exception:
        try:
            return BeautifulSoup(html_body, "html.parser").get_text(separator=" ", strip=True)
        except Exception:
            return re.sub(r"<[^>]+>", " ", html_body).strip()


def _dt_to_iso(dt) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime.datetime):
        return dt.isoformat()
    return str(dt)


# ---------------------------------------------------------------------------
# HubSpot fetchers
# ---------------------------------------------------------------------------

@hubspot_retry
def fetch_contact_properties(client, contact_id: str):
    return client.crm.contacts.basic_api.get_by_id(
        contact_id=contact_id,
        properties=CONTACT_PROPERTIES,
    )


@hubspot_retry
def fetch_contact_history(client, contact_id: str):
    return client.crm.contacts.basic_api.get_by_id(
        contact_id=contact_id,
        properties_with_history=["name_of_target_role", "jobtitle", "name_of_target_role_last_updated"],
    )


@hubspot_retry
def _do_search(client, object_type: str, search_req):
    from hubspot.crm.objects import PublicObjectSearchRequest
    return client.crm.objects.search_api.do_search(
        object_type=object_type,
        public_object_search_request=search_req,
    )


def fetch_email_engagements(client, contact_id: str) -> list:
    from hubspot.crm.objects import PublicObjectSearchRequest

    properties_list = [
        "hs_timestamp", "hs_email_subject", "hs_email_text",
        "hs_email_html", "hs_email_direction", "hs_email_status",
    ]
    search_req = PublicObjectSearchRequest(
        filter_groups=[{
            "filters": [
                {"propertyName": "associations.contact", "operator": "EQ", "value": str(contact_id)},
                {"propertyName": "hs_timestamp", "operator": "GTE", "value": TWELVE_MONTHS_AGO_MS},
            ]
        }],
        properties=properties_list,
        limit=100,
        after="0",
    )
    results = []
    page_count = 0
    while True:
        resp = _do_search(client, "emails", search_req)
        results.extend(resp.results or [])
        page_count += 1
        if page_count >= 50:
            print(f"[fetch_hubspot] WARNING: email pagination hit 50-page cap", flush=True)
            break
        if not resp.paging or not resp.paging.next:
            break
        search_req.after = resp.paging.next.after
    return results


def fetch_meeting_engagements(client, contact_id: str) -> list:
    from hubspot.crm.objects import PublicObjectSearchRequest

    properties_list = [
        "hs_timestamp", "hs_meeting_title", "hs_meeting_body",
        "hs_internal_meeting_notes", "hs_meeting_start_time",
        "hs_meeting_end_time", "hs_meeting_outcome",
    ]
    search_req = PublicObjectSearchRequest(
        filter_groups=[{
            "filters": [
                {"propertyName": "associations.contact", "operator": "EQ", "value": str(contact_id)},
                {"propertyName": "hs_timestamp", "operator": "GTE", "value": TWELVE_MONTHS_AGO_MS},
            ]
        }],
        properties=properties_list,
        limit=100,
        after="0",
    )
    results = []
    page_count = 0
    while True:
        resp = _do_search(client, "meetings", search_req)
        results.extend(resp.results or [])
        page_count += 1
        if page_count >= 50:
            print(f"[fetch_hubspot] WARNING: meeting pagination hit 50-page cap", flush=True)
            break
        if not resp.paging or not resp.paging.next:
            break
        search_req.after = resp.paging.next.after
    return results


@hubspot_retry
def fetch_meeting_ids_v4(client, contact_id: str) -> list:
    results = []
    after = None
    while True:
        resp = client.crm.associations.v4.basic_api.get_all(
            from_object_type="contacts",
            from_object_id=contact_id,
            to_object_type="meetings",
            after=after,
        )
        for result in (resp.results or []):
            results.append(str(result.to_object_id))
        if not resp.paging or not resp.paging.next:
            break
        after = resp.paging.next.after
    return results


@hubspot_retry
def batch_read_meetings(client, meeting_ids: list, properties: list) -> list:
    if not meeting_ids:
        return []
    from hubspot.crm.objects.meetings.models import (
        BatchReadInputSimplePublicObjectId,
        SimplePublicObjectId,
    )
    inputs = [SimplePublicObjectId(id=mid) for mid in meeting_ids]
    batch_input = BatchReadInputSimplePublicObjectId(inputs=inputs, properties=properties)
    resp = client.crm.objects.meetings.batch_api.read(
        batch_read_input_simple_public_object_id=batch_input
    )
    return resp.results or []


def merge_meetings(client, search_results: list, v4_ids: list) -> list:
    """Deduplicate meetings from search + v4 associations. Batch-read any new IDs."""
    seen_ids = {str(r.id) for r in search_results}
    new_ids = [mid for mid in v4_ids if mid not in seen_ids]
    if not new_ids:
        return list(search_results)
    properties = [
        "hs_timestamp", "hs_meeting_title", "hs_meeting_body",
        "hs_internal_meeting_notes", "hs_meeting_start_time",
        "hs_meeting_end_time", "hs_meeting_outcome",
    ]
    new_meetings = batch_read_meetings(client, new_ids, properties)
    return list(search_results) + new_meetings


@hubspot_retry
def _fetch_deal_ids_v4(client, contact_id: str) -> list:
    results = []
    after = None
    while True:
        resp = client.crm.associations.v4.basic_api.get_all(
            from_object_type="contacts",
            from_object_id=contact_id,
            to_object_type="deals",
            after=after,
        )
        for result in (resp.results or []):
            results.append(str(result.to_object_id))
        if not resp.paging or not resp.paging.next:
            break
        after = resp.paging.next.after
    return results


@hubspot_retry
def _batch_read_deals(client, deal_ids: list) -> list:
    if not deal_ids:
        return []
    from hubspot.crm.deals.models import (
        BatchReadInputSimplePublicObjectId as DealBatchInput,
        SimplePublicObjectId as DealObjId,
    )
    properties = ["dealname", "dealstage", "amount", "closedate", "pipeline"]
    inputs = [DealObjId(id=did) for did in deal_ids]
    batch_input = DealBatchInput(inputs=inputs, properties=properties)
    resp = client.crm.deals.batch_api.read(
        batch_read_input_simple_public_object_id=batch_input
    )
    return resp.results or []


def fetch_deals(client, contact_id: str) -> list:
    deal_ids = _fetch_deal_ids_v4(client, contact_id)
    if not deal_ids:
        return []
    return _batch_read_deals(client, deal_ids)


@hubspot_retry
def _fetch_company_contact_ids_v4(client, company_id: str) -> list:
    results = []
    after = None
    while True:
        resp = client.crm.associations.v4.basic_api.get_all(
            from_object_type="companies",
            from_object_id=company_id,
            to_object_type="contacts",
            after=after,
        )
        for result in (resp.results or []):
            results.append(str(result.to_object_id))
        if not resp.paging or not resp.paging.next:
            break
        after = resp.paging.next.after
    return results


@hubspot_retry
def _batch_read_contacts(client, contact_ids: list) -> list:
    from hubspot.crm.contacts.models import (
        BatchReadInputSimplePublicObjectId,
        SimplePublicObjectId as ContactSimplePublicObjectId,
    )
    properties = ["firstname", "lastname", "jobtitle", "email", "hs_lead_status", "lastmodifieddate"]
    inputs = [ContactSimplePublicObjectId(id=cid) for cid in contact_ids[:100]]
    batch_input = BatchReadInputSimplePublicObjectId(inputs=inputs, properties=properties)
    resp = client.crm.contacts.batch_api.read(
        batch_read_input_simple_public_object_id=batch_input
    )
    return resp.results or []


def fetch_related_contacts(client, contact_id: str, company_id: str) -> list:
    if not company_id:
        return []
    all_ids = _fetch_company_contact_ids_v4(client, company_id)
    other_ids = [cid for cid in all_ids if str(cid) != str(contact_id)]
    if not other_ids:
        return []
    return _batch_read_contacts(client, other_ids)


@hubspot_retry
def fetch_owner(client, owner_id: str) -> dict | None:
    try:
        owner = client.crm.owners.owners_api.get_by_id(
            owner_id=int(owner_id), id_property="id"
        )
        return {
            "id": str(owner.id),
            "firstName": owner.first_name or "",
            "lastName": owner.last_name or "",
            "email": owner.email or "",
        }
    except Exception as e:
        print(f"[fetch_hubspot] WARNING: could not fetch owner {owner_id}: {e}", flush=True)
        return None


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _serialise_engagement_email(eng) -> dict:
    props = eng.properties or {}
    body_html = props.get("hs_email_html") or ""
    body_text = props.get("hs_email_text") or ""
    if body_html and not body_text:
        body_text = strip_html(body_html)
    return {
        "id": str(eng.id),
        "hs_timestamp": props.get("hs_timestamp"),
        "hs_email_subject": props.get("hs_email_subject") or "",
        "hs_email_text": body_text,
        "hs_email_direction": props.get("hs_email_direction") or "",
        "hs_email_status": props.get("hs_email_status") or "",
        "updated_at": _dt_to_iso(eng.updated_at),
    }


def _serialise_engagement_meeting(eng) -> dict:
    props = eng.properties or {}
    return {
        "id": str(eng.id),
        "hs_timestamp": props.get("hs_timestamp"),
        "hs_meeting_title": props.get("hs_meeting_title") or "",
        "hs_meeting_body": props.get("hs_meeting_body") or "",
        "hs_internal_meeting_notes": props.get("hs_internal_meeting_notes") or "",
        "hs_meeting_start_time": props.get("hs_meeting_start_time"),
        "hs_meeting_end_time": props.get("hs_meeting_end_time"),
        "hs_meeting_outcome": props.get("hs_meeting_outcome") or "",
        "updated_at": _dt_to_iso(eng.updated_at),
    }


def _serialise_deal(deal) -> dict:
    props = deal.properties or {}
    return {
        "id": str(deal.id),
        "dealname": props.get("dealname") or "",
        "dealstage": props.get("dealstage") or "",
        "amount": props.get("amount") or "",
        "closedate": props.get("closedate") or "",
        "updated_at": _dt_to_iso(deal.updated_at),
    }


def _serialise_related_contact(contact) -> dict:
    props = contact.properties or {}
    return {
        "id": str(contact.id),
        "firstname": props.get("firstname") or "",
        "lastname": props.get("lastname") or "",
        "jobtitle": props.get("jobtitle") or "",
        "email": props.get("email") or "",
        "hs_lead_status": props.get("hs_lead_status") or "",
        "updated_at": _dt_to_iso(contact.updated_at),
    }


def assemble_output(
    contact,
    contact_history,
    emails: list,
    meetings: list,
    deals: list,
    related: list,
    owner: dict | None,
) -> dict:
    props = contact.properties or {}

    # Extract properties_updated_at from propertiesWithHistory
    props_updated_at = {}
    if hasattr(contact_history, "properties_with_history") and contact_history.properties_with_history:
        pwh = contact_history.properties_with_history
        for key in ["name_of_target_role", "jobtitle", "name_of_target_role_last_updated"]:
            history_list = pwh.get(key, []) if isinstance(pwh, dict) else []
            if history_list:
                first = history_list[0]
                props_updated_at[key] = (
                    first.get("timestamp") if isinstance(first, dict)
                    else getattr(first, "timestamp", None)
                )
            else:
                props_updated_at[key] = None
    else:
        props_updated_at = {
            "name_of_target_role": None,
            "jobtitle": None,
            "name_of_target_role_last_updated": None,
        }

    return {
        "contact_id": str(CONTACT_ID),
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "properties": {
            "firstname": props.get("firstname") or "",
            "lastname": props.get("lastname") or "",
            "email": props.get("email") or "",
            "jobtitle": props.get("jobtitle") or "",
            "company": props.get("company") or "",
            "industry": props.get("industry") or "",
            "numberofemployees": props.get("numberofemployees") or "",
            "city": props.get("city") or "",
            "country": props.get("country") or "",
            "website": props.get("website") or "",
            "hubspot_owner_id": props.get("hubspot_owner_id") or "",
            "name_of_target_role": props.get("name_of_target_role") or "",
            "name_of_target_role_last_updated": props.get("name_of_target_role_last_updated") or "",
            "company_locations": props.get("company_locations") or "",
            "years_in_crm": props.get("years_in_crm") or "",
            "outreach_attempt_count": props.get("outreach_attempt_count") or "",
            "related_contacts": props.get("related_contacts") or "",
            "secondary_contact_name": props.get("secondary_contact_name") or "",
        },
        "properties_updated_at": props_updated_at,
        "contact_updated_at": _dt_to_iso(contact.updated_at),
        "contact_created_at": _dt_to_iso(contact.created_at),
        "email_engagements": [_serialise_engagement_email(e) for e in emails],
        "meeting_engagements": [_serialise_engagement_meeting(m) for m in meetings],
        "deals": [_serialise_deal(d) for d in deals],
        "related_contacts_detail": [_serialise_related_contact(c) for c in related],
        "owner": owner or {"id": "", "firstName": "", "lastName": "", "email": ""},
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"[fetch_hubspot] processing contact_id={CONTACT_ID}", flush=True)
    if not CONTACT_ID:
        raise ValueError("CONTACT_ID environment variable is required")

    client = hubspot.Client.create(access_token=os.environ["HUBSPOT_API_KEY"])

    # 1. Contact properties
    contact = fetch_contact_properties(client, CONTACT_ID)
    print(
        f"[fetch_hubspot] contact fetched: "
        f"{contact.properties.get('firstname')} {contact.properties.get('lastname')} "
        f"@ {contact.properties.get('company')}",
        flush=True,
    )

    # 2. Properties with history (for freshness tiers)
    contact_history = fetch_contact_history(client, CONTACT_ID)

    # 3. Email engagements (CRM search, last 12 months)
    emails = fetch_email_engagements(client, CONTACT_ID)
    print(f"[fetch_hubspot] email engagements: {len(emails)}", flush=True)

    # 4. Meeting engagements (CRM search + v4 associations merge)
    meetings_search = fetch_meeting_engagements(client, CONTACT_ID)
    try:
        v4_meeting_ids = fetch_meeting_ids_v4(client, CONTACT_ID)
    except Exception as e:
        print(f"[fetch_hubspot] WARNING: v4 meeting associations failed: {e}", flush=True)
        v4_meeting_ids = []
    meetings = merge_meetings(client, meetings_search, v4_meeting_ids)
    print(f"[fetch_hubspot] meeting engagements: {len(meetings)}", flush=True)

    # 5. Deals
    try:
        deals = fetch_deals(client, CONTACT_ID)
    except Exception as e:
        print(f"[fetch_hubspot] WARNING: deals fetch failed: {e}", flush=True)
        deals = []
    print(f"[fetch_hubspot] deals: {len(deals)}", flush=True)

    # 6. Related contacts (via company association)
    company_id = (contact.properties or {}).get("associatedcompanyid") or ""
    try:
        related = fetch_related_contacts(client, CONTACT_ID, company_id)
    except Exception as e:
        print(f"[fetch_hubspot] WARNING: related contacts fetch failed: {e}", flush=True)
        related = []
    print(f"[fetch_hubspot] related contacts: {len(related)}", flush=True)

    # 7. Owner
    owner_id = (contact.properties or {}).get("hubspot_owner_id") or ""
    owner = None
    if owner_id:
        owner = fetch_owner(client, owner_id)

    # 8. Assemble and write
    output = assemble_output(contact, contact_history, emails, meetings, deals, related, owner)
    write_json("hubspot_contact.json", output)
    print(
        f"[fetch_hubspot] hubspot_contact.json written — "
        f"{len(emails)} emails, {len(meetings)} meetings, {len(deals)} deals, {len(related)} related contacts",
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        write_dlq(CONTACT_ID, CONTACT_EMAIL, "fetch_hubspot", str(e))
        raise SystemExit(1)
