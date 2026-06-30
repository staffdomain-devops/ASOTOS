path = r"C:\Users\irahfo\Outreach\Asotos\.planning\research\SUMMARY.md"

sections = []

sections.append("""# Research Summary: ASOTOS GitHub Actions AI Email Campaign Pipeline

**Project:** ASOTOS -- 8-email re-engagement campaign for Staff Domain (Australian B2B offshore staffing)
**Synthesized:** 2026-06-12
**Research confidence:** HIGH across stack and architecture; MEDIUM on Chorus AI specifics

---

## Executive Summary

ASOTOS is a linear, single-job GitHub Actions pipeline triggered on-demand by Make.com via . It fetches a HubSpot contact and Chorus AI call transcripts, computes personalisation tokens, calls Claude to generate all 8 re-engagement emails in a single API call, and writes the output back to HubSpot as CRM notes and contact properties. The pipeline processes one contact per run, making it simple to reason about, debug, and scale incrementally.

The research strongly validates the chosen stack. Every library version is current and pinned. The architecture pattern -- five Python scripts sharing state through  JSON files -- is the correct choice for a sequential single-contact pipeline with no parallelism requirements. There is no case for async, microservices, queues, or LLM orchestration frameworks at this scale. The design constraints imposed by Claude prompt caching (static system prompt, dynamic user message) align naturally with the personalisation model.

The highest-risk areas are data integrity, not technology. Stale HubSpot data passed as current context, silent Jinja2 token substitution, and unvalidated LLM output written directly to the CRM are all plausible failure modes that produce professionally damaging emails. These must be designed out from the first end-to-end run, not patched in later.

---""")

with open(path, "w", encoding="utf-8") as f:
    f.write("".join(sections))
print("done")