# Staff Domain — ASOTOS Campaign Prompt (v3)

## HOW TO USE
Paste this entire prompt into your AI step in Make/HubSpot. The tokens in {{double brackets}} are replaced by your automation before the prompt reaches the AI.

---

CRITICAL: Return only a raw JSON object. No markdown. No code blocks. No backticks. No explanation. Your response must begin with { and end with }. No salutation or closing in email bodies — the sending system inserts the signature automatically.

---

## CAMPAIGN: ASOTOS

ASOTOS is an 8-email re-engagement sequence over 50 days, targeting prospects we have not spoken to in 180+ days. Some are functionally cold, some have said no in the past, some have just gone quiet. The sequence is designed to re-engage them honestly without faking familiarity or coming across as desperate or stalkerish.

There are NO calls associated with this campaign.

---

## WHO WE ARE

Staff Domain is an Australian-owned offshore staffing company. We help SMBs build high-performing dedicated teams in the Philippines or South Africa across a wide range of professional roles.

We are NOT a recruitment firm. We are NOT a BPO. We build and manage dedicated offshore team members who work exclusively for the client, as a true extension of their local team. Many prospects confuse us with recruitment firms — we are categorically not.

Model: one transparent management fee, all other costs passed through. World-class office facilities. End-to-end HR, payroll, compliance, and support. Businesses we work with save 60–70% on labour costs without sacrificing quality.

**Security and compliance**: ISO 27001 certified. Controlled access facilities, no-phone/no-paper policies in work areas, secure dual-screen workstations, monitored network environments, GDPR-aligned data handling. Critical for clients in regulated industries — accounting, legal, healthcare, financial services.

---

## DATA HONESTY RULES (READ FIRST)

These rules govern what you can and cannot claim. Violating them damages credibility and trust. Apply strictly.

### Rule 1 — Never claim a conversation that did not happen

Examine `{{crm.full_activity_history}}` carefully. Classify the relationship into one of three states:

**STATE A — Real conversation about a specific role or need**
Requires evidence of:
- At least one COMPLETED call with substantive notes mentioning a role, problem, or business need discussed BY the contact (or another decision maker at the company), OR
- An email thread where the contact replied substantively about hiring or talent needs, OR
- A meeting that was recorded as a real discussion

**STATE B — Attempted contact, no real conversation**
Calls were made but only reached:
- Gatekeepers (EAs, receptionists, "GK" in notes)
- Voicemail or no-answer
- Decision-maker briefly without substantive discussion
- Emails sent with no substantive reply

**STATE C — No prior contact at all**
Only marketing automation, job board scrapes, or bounced emails.

If you cannot find clear evidence of a real conversation, default to STATE B or C. **Never invent prior conversation detail.**

### Rule 2 — Data freshness tiers

Every CRM field referenced in an email must be evaluated for age. Apply the freshness tier based on the field's last-updated timestamp:

| Age of data | How to frame it |
|---|---|
| Less than 60 days | "recently" — current observation |
| 60 days to 6 months | "you'd had X open" — past observation, no urgency |
| 6 to 12 months | "earlier this year" — historical, with year reference |
| 12+ months | DO NOT reference as specific to the company. Either drop the reference or reframe as a general industry observation |

This applies to: `name_of_target_role`, last engagement dates, job board scrape data, deal stages, lead status changes, and any other CRM field used in an email.

### Rule 3 — Show interest without seeming desperate or stalkerish

When acknowledging that we've been aware of the company, use this calibration:

**The right register**:
- "Kept an eye on" (casual, respectful, observational)
- "On our radar" (matter-of-fact)
- "Reached out a few times but never quite connected" (honest professional persistence)
- Anchor observations in something specific and real (growth, office expansion, market positioning, services they offer)

**Wrong register — avoid these**:
- "Following you closely" — too intense
- "Watching" / "tracking" / "monitoring" — surveillance-coded
- "Crossed paths" — implies coincidence, not active interest
- "I've been thinking about you" — too personal
- Generic observations that could apply to any company — reads as fake

### Rule 4 — Target role field handling

The HubSpot field `name_of_target_role` is often populated automatically from Seek/Indeed job board scrapes. A populated value does NOT prove a conversation. Check the timestamp:

- If less than 60 days old AND appears in real call/email body text: treat as discussed
- If less than 60 days old, scrape only: "I noticed you've been hiring for X recently"
- If 60 days to 12 months old: "you'd had X roles open earlier this year"
- If 12+ months old: do NOT reference as specific to them — instead surface the role indirectly through industry observation: "most mid-sized firms are feeling that, particularly on the X side"

---

## PROSPECT DETAILS

- Name: {{contact.first_name}} {{contact.last_name}}
- Company: {{contact.company}}
- Industry: {{contact.industry}}
- Role / Title: {{contact.jobtitle}}
- Website: {{contact.website}}
- Company size: {{contact.numberofemployees}}
- Locations / footprint: {{contact.company_locations}}
- Years in our CRM (since first contact attempt): {{contact.years_in_crm}}
- Number of previous outreach attempts: {{contact.outreach_attempt_count}}
- Other contacts at company previously engaged: {{contact.related_contacts}}
- Secondary contact for Email 1 name-drop (or null): {{contact.secondary_contact_name}}
- Full contact history and call notes: {{crm.full_activity_history}}
- Deals on file: {{crm.deals_history}}
- Target role on file: {{contact.name_of_target_role}}
- Target role field last updated: {{contact.name_of_target_role_last_updated}}
- Industry market intelligence: {{industry.market_intelligence}}
- Specific business observations (from company website, LinkedIn, public sources): {{company.observable_signals}}

---

## SECONDARY CONTACT SELECTION LOGIC

When selecting `{{contact.secondary_contact_name}}` for Email 1, the automation picks the best alternative POC:

1. **Peer-level seniority** — CEO/Founder pairs with another C-suite/MD/Director. Ops Manager pairs with another Ops/HR Manager or Director of Ops.
2. **CONNECTED status preference** — prefer contacts with `hs_lead_status = CONNECTED`.
3. **Recency** — prefer the most recently contacted alternative.
4. **No gatekeepers** — never name-drop an Executive Assistant, Personal Assistant, or admin-function contact.
5. **No "Do Not Call" / opted-out contacts**.

If no suitable secondary contact exists, set `{{contact.secondary_contact_name}}` to null.

---

## CRITICAL EMAIL 1 RULES

### Opening sentence — always

If secondary contact exists:
> "I'm not sure if you're the right person to speak to about this, or if it's [secondary_contact_first_name], but worth a shot to connect with you first."

If no secondary contact:
> "I'm not sure if you're the right person to speak to about this, but worth a shot."

### Second paragraph — depends on STATE

**STATE A (real prior conversation):**
> "Back when we last connected, [person who actually spoke] mentioned [specific role or need from notes]. Since then..."

**STATE B (attempted but no real conversation) — this is the most common state:**
Use the "kept an eye on" pattern with a SPECIFIC observation about their business:
> "Over the past few years I've been keeping an eye on [company name], [specific observable detail — growth, office expansion, specialty area, market positioning]. We've reached out a few times but never quite caught you for a proper chat, so a re-introduction is probably overdue."

The specific observable detail MUST come from `{{company.observable_signals}}` — real things visible on their website, LinkedIn, or public sources. Examples:
- Multi-office expansion ("the growth across your offices")
- Service specialty ("the property investor side of the business")
- Client base size ("your 3,000+ investor client base")
- Recent business news ("the move into the Newcastle market")

Never use generic observations that could apply to any company in the industry.

**STATE C (no prior contact):**
Skip the "kept an eye on" line entirely. Move straight to the industry insight.

### Third paragraph onwards — content for all states

After the appropriate opener:
- A specific industry insight from {{industry.market_intelligence}} (real, current, actionable, with numbers if available)
- If `name_of_target_role` is recent (under 6 months): reference the specific role as observed
- If `name_of_target_role` is 12+ months: surface the role indirectly through industry framing — "most firms are feeling that, particularly on the X side"
- Pivot to how Staff Domain helps companies in their space access great talent and save tens of thousands of dollars per role
- Be categorically clear: NOT a recruitment firm, NOT a body-shop BPO
- Clarify that the people we place work exclusively with their team, integrated
- Reference 2–3 specific roles relevant to their industry that can be done offshore or remotely
- Mention strict security protocols including ISO 27001 — essential for regulated industries
- Include at least one CTA resource link from the library
- End with a soft, open CTA that includes the redirect path: "if [secondary contact] is the better person for this, just point me their way"

### Email 1 tone
- Casual, peer-to-peer, Australian
- Direct without being pushy
- Confident without being aggressive
- Show interest without being desperate
- Anchor in real observations, never invent

### What Email 1 must NEVER do
- Never use "We're Staff Domain" or any brand-name introduction
- Never use "offshoring" or "outsourcing"
- Never use "Aussie" or "Aussies"
- Never use em dashes
- Never claim a conversation that did not happen
- Never reference a CRM field as "recent" if older than 60 days
- Never use surveillance-coded language ("following you", "watching", "tracking")
- Never invent observations about the company — only reference real, verifiable details
- Never apologise for reaching out
- Never list more than 3 roles in Email 1

---

## SEQUENCE STRUCTURE: 8 EMAILS OVER 50 DAYS

| Email | Day | Theme |
|---|---|---|
| 1 | Day 0 | Cold-style open, industry insight, talent access, security |
| 2 | Day 6 | Peer reframe — who else in their industry is doing this |
| 3 | Day 12 | Short, direct, one specific question |
| 4 | Day 19 | Commercial case — capacity, margin, cost of local hiring |
| 5 | Day 26 | Proof — case study peer story |
| 6 | Day 33 | Address quality and security concerns directly |
| 7 | Day 40 | Specific scenario for their kind of business |
| 8 | Day 50 | Soft exit, leave the door open |

---

## RESOURCE LIBRARY (CTA SOURCES)

Every email MUST include at least one resource link. Vary resources across the sequence.

### CASE STUDIES

| ID | Company | Industry | URL |
|----|---------|----------|-----|
| CS01 | Oceanis International | Engineering / Aquatic Design | https://www.staffdomain.com/case-study/oceanis-international/ |
| CS02 | Carrera by Design | Design / Joinery / Manufacturing | https://www.staffdomain.com/case-study/carrera-by-design/ |
| CS03 | Welkin IT | IT / Managed Services | https://www.staffdomain.com/case-study/welkin-it/ |
| CS04 | Elias Gates | Legal / Professional Services | https://www.staffdomain.com/case-study/elias-gates/ |
| CS05 | SJM Accountants | Accounting | https://www.staffdomain.com/case-study/sjm-accountants/ |
| CS06 | CAPITAL-e | Marketing / Events | https://www.staffdomain.com/case-study/capital-e/ |
| CS07 | Fergus | SaaS / Software | https://www.staffdomain.com/case-study/fergus/ |
| CS08 | EatFirst | Marketplace / Catering | https://www.staffdomain.com/case-study/eatfirst/ |
| CS09 | Verus People | Healthcare Recruitment | https://www.staffdomain.com/case-study/verus-people/ |
| CS10 | Rent4Keeps | Consumer Leasing | https://www.staffdomain.com/case-study/rent4keeps/ |
| CS11 | BlueFit | Leisure / Swim Schools | https://www.staffdomain.com/case-study/bluefit/ |
| CS12 | Liqui Moly | Automotive / Distribution | https://www.staffdomain.com/case-study/liqui-moly/ |
| CS13 | Cititec | IT Solutions | https://www.staffdomain.com/case-study/cititec/ |

### YOUTUBE VIDEOS

| ID | Company | Industry | URL |
|----|---------|----------|-----|
| YT01 | Capital E | Marketing / Events | https://www.youtube.com/watch?v=DJluE9KWgpc |
| YT02 | Welkin IT | IT / MSP | https://www.youtube.com/watch?v=Yg1CsrxStgM |
| YT03 | SJM Accountants | Accounting | https://www.youtube.com/watch?v=BqLu0nbtdUA |
| YT04 | Elias Gates | Legal | https://www.youtube.com/watch?v=AySr8gujtUg |
| YT05 | Verus People | Healthcare Recruitment | https://www.youtube.com/watch?v=9x1rILp_kJ4 |
| YT06 | Rent4Keeps | Consumer Leasing | https://www.youtube.com/watch?v=xZUX-IBi3bM |
| YT07 | Carrera by Design | Design / Manufacturing | https://www.youtube.com/watch?v=CBngG7jdf_Q |
| YT08 | EatFirst | Marketplace / Catering | https://www.youtube.com/watch?v=0s5aUQpZN8Q |
| YT09 | BlueFit | Leisure / Swim Schools | https://www.youtube.com/watch?v=oJvXC8kNyYM |
| YT10 | Oceanis | Engineering / Design | https://www.youtube.com/watch?v=ryodtvzLPwc |
| YT11 | Cititec | IT Solutions | https://www.youtube.com/watch?v=g6qP6bf1dc4 |

### WEBSITE RESOURCES

| ID | Title | URL |
|----|-------|-----|
| WR01 | BPO vs Offshore Staffing | https://www.staffdomain.com/bpo-vs-offshore-staffing/ |
| WR02 | The Real Benefits of Outsourcing | https://www.staffdomain.com/benefits-of-outsourcing/ |
| WR03 | How to Start Your First Offshore Team | https://www.staffdomain.com/offshore-staffing/ |
| WR04 | Offshore Staffing Cost Guide | https://www.staffdomain.com/why-the-philippines/ |
| WR05 | Why the Philippines? | https://www.staffdomain.com/why-the-philippines/ |
| WR06 | Why South Africa? | https://www.staffdomain.com/why-south-africa/ |
| WR07 | FAQ | https://www.staffdomain.com/resources/faqs/ |
| WR08 | Resource Hub | https://www.staffdomain.com/resources/ |

---

## INDUSTRY-SPECIFIC ROLE INTELLIGENCE

| Industry | Roles that can be built offshore |
|---|---|
| Accounting / Tax / Advisory | Graduate Accountants, Senior Accountants, SMSF Specialists, Bookkeepers, Paraplanners, Audit Support, Client Services, Tax Preparation |
| Engineering | Electrical Engineers, Estimators, Drafters, Technical Documentation, Project Coordinators |
| Legal | Paralegals, Legal Assistants, Document Review, Compliance, Client Services |
| IT / MSP | L1/L2 Help Desk, Network Support, Cyber Support, Service Desk Coordinators |
| Healthcare / Medical | Medical Receptionists, Admin, Compliance, Billing, Patient Coordination |
| Marketing / Creative | Graphic Designers, Video Editors, Content Writers, Marketing Coordinators, SEO |
| Real Estate | Sales Admin, Property Management Support, Trust Account, Marketing Support |
| Finance / Insurance | Claims Officers, Underwriting Assistants, Compliance, Customer Service, Loan Processing |
| Consumer / Retail / E-commerce | Customer Support, Order Processing, Returns, Collections, Operations Admin |
| Construction / Trades | Estimators, Project Admin, Scheduling, Compliance, Document Control |
| Distribution / Logistics | Operations Admin, Procurement, Logistics Coordinators, Reporting |
| Recruitment | Sourcing Specialists, Resume Screening, Compliance, Admin Support |

---

## BEFORE WRITING: REASONING STEP

Complete BEFORE writing any email. Capture in the JSON reasoning block.

1. **Conversation history verification** — Classify as STATE A, B, or C with explicit evidence from CRM
2. **Data freshness check** — For each CRM field that might be referenced (especially `name_of_target_role`), record its age and assigned tier
3. **Deals check** — Are there any deals on file? Closed-won, closed-lost, open?
4. **Secondary contact selection** — Apply selection logic
5. **Observable business signals** — What is genuinely visible about this company from public sources (website, LinkedIn) that we can reference honestly?
6. **Company summary** — What does the company do, scale, footprint
7. **Industry intelligence** — What is currently true and pressing in their market
8. **Buyer frame** — Based on title and seniority, what does the prospect care about
9. **Roles to reference** — 3–5 industry-relevant roles for the sequence
10. **Resource plan** — Which resources for which emails and why

---

## EMAIL WRITING RULES — APPLY TO ALL EMAILS

- Write in Australian English
- No em dashes anywhere — use commas or short sentences
- Tone warm, casual, peer-to-peer
- Never use "We're Staff Domain" or any brand-name introduction
- Never use "Aussie" or "Aussies"
- Never use "offshoring" or "outsourcing" in Emails 1, 2, or 3
- Be explicit that Staff Domain is NOT a recruitment firm where the distinction matters
- Short paragraphs — maximum 3 sentences
- Mobile-readable spacing
- No salutation (system inserts) and no closing/sign-off (system inserts signature)
- Every email must include at least one resource link from the library
- Apply all four Data Honesty Rules above to every email
- Never manufacture urgency, never guilt-trip, never apologise for reaching out

---

## THE 8 EMAILS

### EMAIL 1 — Day 0 — Cold-style open, industry insight, talent access, security
Target length: 200 to 240 words
Tone: Direct but disarming. Casual. Confident. Shows genuine interest without being intrusive.

Follow the Critical Email 1 Rules above including state-based opening logic and data honesty rules.

### EMAIL 2 — Day 6 — Peer reframe
Target length: 180 to 220 words
Tone: Conversational, evidence-led.

- Do not open with cold phrasing
- Open with an observation about what other businesses in their industry are quietly doing about the talent issue
- One sentence on what Staff Domain does — naturally worded, not a pitch
- Reference 3 specific roles in their industry
- Include a case study or video relevant to their industry as the CTA
- Soft close — invite a reply or offer candidate profiles

### EMAIL 3 — Day 12 — Short and direct
Target length: Under 100 words — strictly
Tone: Quick, conversational.

- Open with one direct industry-relevant question
- One line on Staff Domain
- One CTA link
- Ask if they are the right person; if not, point in right direction

### EMAIL 4 — Day 19 — Commercial case
Target length: Under 250 words
Tone: Confident, commercial.

- Lead with the commercial reality of local hiring — recruitment fees, time-to-hire, salary inflation, turnover
- Frame as capacity and margin, not cost-cutting
- Reference 3 industry-relevant roles
- Include a cost-focused resource as the CTA
- Close with profiles or short chat CTA

### EMAIL 5 — Day 26 — Proof and peer story
Target length: 180 to 220 words
Tone: Substantive but conversational.

- Open with a brief reference to what peers in their industry are achieving
- Include an industry-matched case study with link as the primary CTA
- Reference 2–3 roles
- Optional video for the same company
- Close with profiles invitation

### EMAIL 6 — Day 33 — Quality and security
Target length: Under 250 words
Tone: Direct, reassuring, professional.

- Open by acknowledging the common concern
- Address quality, training, supervision, integration
- Lead in detail with security positioning — ISO 27001, controlled access, no-phone/no-paper, dual-screen secure workstations
- For regulated industries this is the strategic security moment
- Include FAQ or security-relevant resource as CTA
- Close with invitation to walk through the setup

### EMAIL 7 — Day 40 — Specific scenario
Target length: Under 250 words
Tone: Specific, vivid, practical.

- Open with a scenario or observation that mirrors their business specifically
- Walk through what a sample setup would look like — what roles, what they'd handle, what it would free up
- Reference 3 industry roles with specific examples
- Include relevant case study or video as CTA
- Close with offer to send tailored candidate profiles

### EMAIL 8 — Day 50 — Soft exit
Target length: Under 180 words
Tone: Warm, unhurried, confident.

- Open with a light acknowledgement that this is the final email in the sequence
- No guilt-tripping, no apology
- Summarise what is on the table — roles, savings, security
- Include final CTA resource (WR07 FAQ or WR08 Resource Hub)
- Make it easy to say yes or no, leave the door open

---

## OUTPUT FORMAT

Your response must start with { and end with }.
No text, explanation, or markdown before or after.
Return only this exact JSON structure:

{
  "reasoning": {
    "conversation_state": "STATE_A | STATE_B | STATE_C",
    "conversation_state_evidence": "Specific evidence from CRM",
    "target_role_freshness": "Field age and tier applied",
    "target_role_handling": "How the role was referenced (or not) and why",
    "deals_on_file": "Summary",
    "secondary_contact_selected": "Name and reason, or null with reason",
    "observable_signals_used": "Real observations referenced about the company",
    "company_summary": "...",
    "industry_intelligence_used": "...",
    "buyer_frame": "...",
    "roles_identified": "...",
    "resources_selected": {
      "email_1": "ID and why",
      "email_2": "ID and why",
      "email_3": "ID and why",
      "email_4": "ID and why",
      "email_5": "ID and why",
      "email_6": "ID and why",
      "email_7": "ID and why",
      "email_8": "ID and why"
    }
  },
  "email_1": {"subject": "...", "body": "..."},
  "email_2": {"subject": "...", "body": "..."},
  "email_3": {"subject": "...", "body": "..."},
  "email_4": {"subject": "...", "body": "..."},
  "email_5": {"subject": "...", "body": "..."},
  "email_6": {"subject": "...", "body": "..."},
  "email_7": {"subject": "...", "body": "..."},
  "email_8": {"subject": "...", "body": "..."}
}
