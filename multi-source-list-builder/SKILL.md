---
name: multi-source-list-builder
description: Build prospect/lead lists by pulling from Apollo, Prospeo, and Clay simultaneously, normalizing all records into one canonical schema, and pushing the consolidated list to a Clay table via webhook for deduplication. Use this skill whenever the user wants to "build a list", "pull leads", "create a prospect list", "run a list build", mentions ICP filters (titles, industries, headcount, geo, seniority), or asks to consolidate/push contacts from Apollo, Prospeo, or any data source into Clay. Also trigger when the user gives plain-language targeting criteria like "VPs of Sales at US SaaS companies, 51-200 employees" — even if they don't name the platforms. Always use this skill instead of calling the Apollo/Prospeo APIs ad-hoc.
---

# Multi-Source List Builder (Prospeo → Apollo → Clay MCP → Clay table)

Pulls people matching one canonical ICP spec in a fixed three-phase order:
1. **Prospeo first** — full profiles (name, LinkedIn, domain, location, company). Emails
   are masked and left unrevealed; Prospeo's LinkedIn URLs also feed the Apollo exclusion.
2. **Apollo second** — excludes the Prospeo people, then enriches its OWN candidates via
   `people/bulk_match` (emails YES, phone NEVER). Apollo is the only enrichment leg.
3. **Clay MCP third** — discovery-only: loops `find-and-enrich-contacts-at-company` over
   the company domains. No paid data points.

Each tool enriches only its own records (see **Enrichment policy** below). Final email
enrichment is done by the user in Clay.

All three sources are normalized into one canonical schema, pre-deduped locally, and
POSTed to a Clay table webhook. Final authoritative dedup happens in Clay via auto-dedupe.

## Prerequisites (check before first run)

API keys live in a **`.env` file at the skill root** (next to this SKILL.md). The
scripts auto-load it on import (`scripts/_env.py`), so the keys work whether they're
in `.env` OR exported in the shell. **Before ever telling the user a key is missing,
read the skill-root `.env`** — do NOT rely on `printenv`/`os.environ` alone, because
keys in `.env` won't show up there until a script loads them. (This bit us once: a run
wrongly skipped the whole Prospeo leg after only checking the shell environment.)

Required keys:
- `APOLLO_API_KEY` — must be a **master** API key (required by Apollo's search endpoint)
- `PROSPEO_API_KEY`
- `CLAY_WEBHOOK_AUTH_TOKEN` — optional, only if the user enabled auth on webhooks

If a key is genuinely absent from both `.env` and the shell, ask the user to add it to
`.env` — NEVER ask them to paste a key into chat.

Destination tables live in `config/clay_tables.json` (table ID → webhook URL registry;
created/extended automatically during Step 1 intake — see `clay_tables.example.json`).

Clay-side setup the user must do once (remind them):
1. In the destination Clay table: **+ Add source → Webhook → Monitor webhook**, copy the URL.
2. Enable **Auto-dedupe** on the table, keyed on the `linkedin_url_canonical` column
   (Clay retains the oldest row and deletes duplicates).
3. If they want different ICP segments in different tables, create one webhook per table
   and pass the right URL per run.

Python: `requests` and `pyyaml` (`pip install requests pyyaml` if missing).

## Enrichment policy — NO cross-tool enrichment (each tool enriches only its own)

This is a hard rule. Credits from one provider must never be spent enriching another
provider's records, because it makes spend unpredictable and double-charges for the same
person. Concretely:

- **Apollo enriches ONLY its own search candidates.** Apollo's search returns obfuscated
  previews (no name/LinkedIn/email), so `people/bulk_match` on the Apollo-sourced IDs is
  the *only* enrichment this skill performs, and it is capped by the per-tool record cap.
- **Prospeo is taken as-is.** Its emails come back MASKED; we do NOT reveal them here (no
  Apollo match, no Prospeo reveal call). Prospeo's only cross-tool role is to supply
  LinkedIn URLs that **exclude** those people from the Apollo leg, so the same person is
  never enriched twice. Masked emails are nulled by the normalizer (status
  `masked_unrevealed`) so they never look deliverable.
- **Clay is discovery-only** — no paid `dataPoints`.
- **Final enrichment is the user's job, in Clay.** The list ships with verified emails for
  Apollo-sourced people and LinkedIn URLs for everyone; the user runs their own email
  waterfall on the Clay table. Do not offer to "fill" emails by routing other tools'
  records through Apollo.

So the ONLY credit-spending action in a run is: Apollo `bulk_match` on Apollo's own IDs
(≤ the per-tool cap), plus the small Prospeo search-page cost. That keeps each provider's
spend attributable to its own tool and bounded by the cap.

## Permissions philosophy — ask the minimum, in the fewest interruptions

This skill is meant to scale across large lists, so it must not nag. There are only
**two moments** where you stop for the user:

1. **One intake** at the start — to lock the ICP spec and the destination, asking ONLY
   about things you genuinely can't infer.
2. **One approval gate** right before anything that costs credits or pushes data — to
   show the credit estimate and get a single go-ahead.

Everything in between (credit-free search, normalization, dedup, domain extraction,
writing files) runs **without prompting**. Read the user's intent generously and fill
sensible defaults instead of asking — e.g. "titles use CONTAINS", "no exclusions unless
stated", "cap = what they said, else 1000". Surface every inferred default in the spec
recap so the user can correct it in one place, rather than answering a checklist.

## Workflow

### Step 1 — Single intake: lock the spec + destination

Parse the user's brief into one ICP spec and present it back as a compact recap
(pre-filled, defaults shown). Capture:

- **Titles** (CONTAINS keywords) + any title exclusions
- **Seniority** levels
- **Location(s)** — person and/or company
- **Industry**
- **Company size** (headcount ranges) — if mentioned
- **Company revenue** floor/ceiling — if mentioned (supported on Apollo AND Prospeo, see Step 2)
- **Other exclusions** — domains, locations, LinkedIn URLs, email domains. If the user
  supplies a **suppression/do-not-contact list of LinkedIn URLs** (already-contacted
  people, an existing customer list, etc.), save it as-is to
  `runs/<run-name>/user_exclusions.txt` (one URL per line) — it gets resolved to Apollo
  IDs and excluded BEFORE any reveal credits are spent (see Step 3c). This works at any
  size (thousands of URLs) since resolution is free.
- **Max leads cap**
- **Default output fields** the user gets in the final list (no need to ask unless they
  want to change them): **First Name, Last Name, Full Name, Company Name, Person Location,
  Company HQ, Industry, Email, LinkedIn URL, Website, Employee Size, Revenue**. The
  normalizer always produces these; mention it so expectations are set.

Only genuinely ask the user about:
- Anything **ambiguous or missing** that changes results (e.g. "banking" = licensed banks
  only, or include card issuers/NBFCs?).
- The **Clay destination table** — which table to push to. Look up the table ID in
  `config/clay_tables.json`; if unknown, ask for the webhook URL once (Clay: table →
  + Add source → Webhook → Monitor webhook → copy URL) and save it to the registry.

Bundle these into a **single** `AskUserQuestion` (or a short recap they can confirm/edit).
Don't ask item-by-item. Then write `runs/<run-name>/icp.yaml` (see `config/icp.example.yaml`)
and proceed — no further confirmation needed until the approval gate in Step 4.5.

### Step 2 — Translate filters per platform

The same concept has different names and different accepted values on each platform.
Read `references/api_notes.md` for the full parameter reference, and use
`mappings/industry_map.json` + `mappings/seniority_map.json` + `mappings/revenue_map.json`
to translate values.

Key rules:
- **Titles always use CONTAINS** — never SIMILAR, never EXACT (unless the user
  explicitly asks for exact matching). The `pull_prospeo.py` script enforces this
  automatically, but the filter JSON must also specify `"match_mode": "CONTAINS"`.
- **Apollo** takes flat arrays: `person_titles[]`, `person_seniorities[]` (enum:
  owner, founder, c_suite, partner, vp, head, director, manager, senior, entry, intern),
  `person_locations[]` ("United States" style), `organization_locations[]`,
  `organization_num_employees_ranges[]` ("51,200" string ranges),
  `q_organization_domains_list[]`. Revenue → `revenue_range` object with **integer**
  min/max, e.g. `{"min": 500000}` for ≥ $0.5M.
- **Prospeo** takes a nested `filters` object. VERIFIED key names (use exactly these
  or you get a 400 `INVALID_FILTERS`):
  - `person_job_title` → `{"include": [...], "exclude": [...], "match_mode": "CONTAINS"}`
  - `person_seniority` → `{"include": [...]}` — enum values from `seniority_map.json`
    (e.g. `"Vice President"`, `"C-Suite"`, `"Founder/Owner"` — NOT "VP"/"C-Level"/"Owner")
  - `person_location_search` (NOT `person_location`) → values from the Search Suggestions
    API only; arbitrary strings are rejected
  - `company_headcount_range` → plain array of strings, no include/exclude wrapper
    (e.g. `["51-100", "101-200"]`)
  - `company_industry` → `{"include": [...]}` — enum values from `industry_map.json`
  - `company_revenue` → ✅ **Prospeo SUPPORTS revenue** (older skill text was wrong).
    Object `{"min": "500K", "max": "...", "include_unknown_revenue": false}` with
    **string** band values (NOT integers — `500000` is rejected). Bands and the
    number→band mapping are in `mappings/revenue_map.json`. For "min $0.5M" use `"500K"`.
- Resolve `person_location_search` / `company_location_search` / `company_industry`
  strings through the Prospeo Search Suggestions API first (free) — arbitrary strings 400.
- If a mapping is missing, add it to the mapping file so future runs benefit.

Write the two translated filter payloads to `runs/<run-name>/apollo_filters.json`
and `runs/<run-name>/prospeo_filters.json`, and show the user a short summary of the
translation (not the raw JSON unless asked).

### Step 3 — Pull from each source (fixed order: Prospeo → Apollo → Clay)

**3a — Prospeo (pull first)**

```bash
python scripts/pull_prospeo.py \
    --filters runs/<run>/prospeo_filters.json \
    --out runs/<run>/raw_prospeo.jsonl \
    --max-records 1000
```

Prospeo pagginates 25/page, retries 429s, enforces CONTAINS match mode automatically.
Report record count. If 0, re-check filter key names and enum values (most common cause
of 0 results: wrong seniority string or `person_location` instead of `person_location_search`).

**3b — Extract exclusions from Prospeo output**

```bash
python scripts/extract_exclusions.py \
    --in runs/<run>/raw_prospeo.jsonl \
    --linkedin-out runs/<run>/prospeo_exclusions.txt \
    --domains-out  runs/<run>/prospeo_domains.txt
```

This writes:
- `prospeo_exclusions.txt` — one canonical LinkedIn URL per line → resolved to Apollo
  IDs and excluded pre-enrich in step 3c (saves enrichment credits).
- `prospeo_domains.txt` — unique company domains → used in step 3c to drive Clay MCP pulls.

**3c — Resolve exclusions to Apollo IDs, then Apollo SEARCH ONLY (both credit-free)**

Resolve every LinkedIn-URL exclusion source to Apollo person IDs BEFORE the search —
this is a free `mixed_people/api_search` lookup, not an enrich call, so it never costs
a credit no matter how many URLs are in the list (works fine at thousands of rows):

```bash
python scripts/resolve_linkedin_ids.py \
    --in  runs/<run>/prospeo_exclusions.txt \
    --out runs/<run>/prospeo_exclusion_ids.json

# only if the user supplied a suppression/do-not-contact list in Step 1:
python scripts/resolve_linkedin_ids.py \
    --in  runs/<run>/user_exclusions.txt \
    --out runs/<run>/user_exclusion_ids.json
```

Then run only Phase-1 search, passing every resolved exclusion file so excluded people
are dropped from the candidate list before you even know the enrichment count — the
credit estimate in Step 4 will already reflect the true (smaller) number:

```bash
python scripts/pull_apollo.py \
    --filters runs/<run>/apollo_filters.json \
    --out runs/<run>/raw_apollo.jsonl \
    --max-records 1000 \
    --exclude-ids-file runs/<run>/prospeo_exclusion_ids.json \
    --exclude-ids-file runs/<run>/user_exclusion_ids.json \
    --search-only --ids-out runs/<run>/apollo_ids.json
```

(Omit an `--exclude-ids-file` flag if that exclusion source doesn't apply to the run.)
This writes the ALREADY-EXCLUDED candidate IDs and prints how many were dropped and why
(0 credits used either way). Enrichment (Phase 2) happens AFTER the approval gate in
Step 4. If using the **Apollo MCP** instead of the script, run `mixed_people_api_search`
for both the exclusion-resolution and the main search (credit-free) — don't call
`people_bulk_match` yet, and subtract the resolved exclusion IDs from the candidate IDs
yourself before sizing the estimate.

**3d — Company domains to drive the Clay leg**

Apollo isn't enriched yet (IDs only), so the domain list for Clay comes from Prospeo
(`prospeo_domains.txt` from Step 3b). That's the primary discovery source, so it's a
good account list. If you want broader coverage you can add more domains to Clay in an
optional second pass after Apollo enrichment in Step 5.

**3e — Clay MCP (pull third, per company domain)**

Clay has no global filter-based people search — it requires a company identifier.
Loop `find-and-enrich-contacts-at-company` over each domain in `prospeo_domains.txt`,
applying the same title/location filters from the ICP spec. **Do NOT request paid
`dataPoints` (Email, etc.) yet** — discovery first; email enrichment, if wanted, is a
post-gate decision so it lands inside the single approval.

Clay has **no seniority filter**, so it returns all seniorities — post-filter the
results to the ICP seniority by title (keep VP/Director/Head/Chief/President-tier,
drop Manager/Specialist/Analyst). Also dedupe against the Prospeo+Apollo set; note that
Apollo and Clay often return **different LinkedIn URL forms for the same person** (vanity
vs numbered slug), so dedupe on **name + company-domain**, not just the URL —
`normalize_and_merge.py` now does this automatically in its second pass.

Write contacts to `runs/<run>/raw_clay.jsonl` (one JSON object per line). The normalizer
reads Clay's native field names (`name`, `url`, `latest_experience_title`,
`latest_experience_company`, `domain`, `location_name`, `profile_id`) directly — no manual
renaming needed.

If there are more than ~50 domains, prioritise accounts that appeared in BOTH Prospeo and
Apollo (higher signal) and cap Clay at 50 domains/run. To keep large per-company outputs
out of context, run the Clay loop in a subagent that returns only compact filtered records.

After each pull leg, report the record count. If a source returns 0, re-check filters.

### Step 4 — Credit estimate + SINGLE approval gate (the only gate before spending/pushing)

Now you know the volumes (Prospeo records, Apollo candidate IDs, Clay discovery hits).
Per the enrichment policy, the ONLY credit-spending action is Apollo enriching its own
candidate IDs — there is no cross-tool reveal to decide. Check the Apollo balance
(Profile endpoint, `include_credit_usage=true`), then render the estimate:

```bash
python scripts/credit_report.py --mode estimate \
    --item "Apollo|bulk_match enrich (own candidates)|<#apollo_ids>|1" \
    --item "Prospeo|search pages already run|<#pages>|1"
```

See `references/api_notes.md` § Credit costs for the per-action rates. Present this with
the Apollo balance and the projected final list size (after dedup), capped at the user's
max. Ask **once**: proceed or trim scope? Nothing below runs until the user approves.

**Re-gate on material scope change.** If, after approval, the user changes scope (e.g.
"broaden the industries", "raise the cap") in a way that moves any cost line by more than
~25%, re-render this estimate and re-confirm BEFORE spending. The number the user approves
must match the number you spend — never let a later choice silently multiply the bill.

### Step 5 — Spend + push (runs straight through after approval)

1. **Apollo enrich its OWN candidate IDs** (the only spend — Apollo search is obfuscated,
   so this materializes name/LinkedIn/email for Apollo-sourced people):
   ```bash
   python scripts/pull_apollo.py --filters runs/<run>/apollo_filters.json \
       --out runs/<run>/raw_apollo.jsonl \
       --enrich-ids-file runs/<run>/apollo_ids.json \
       --exclude-ids-file runs/<run>/prospeo_exclusion_ids.json \
       --exclude-ids-file runs/<run>/user_exclusion_ids.json
   ```
   `apollo_ids.json` is already pre-filtered from step 3c, so this is a no-op in the
   normal case — the `--exclude-ids-file` flags here are just a free belt-and-suspenders
   re-check, not a second exclusion pass.
   (Or Apollo MCP `people_bulk_match` in batches of 10 — emails YES, phone NEVER.)
   Do NOT enrich Prospeo or Clay people through Apollo (see Enrichment policy). Prospeo
   rows stay as pulled (masked email → nulled by the normalizer); Clay rows stay
   discovery-only. The user reveals those emails later in Clay.
2. **Final normalize + merge + dedupe** (all three sources → the 12 default fields):
   ```bash
   python scripts/normalize_and_merge.py \
       --apollo runs/<run>/raw_apollo.jsonl --prospeo runs/<run>/raw_prospeo.jsonl \
       --clay runs/<run>/raw_clay.jsonl \
       --out runs/<run>/merged.jsonl --report runs/<run>/merge_report.json \
       --priority prospeo,apollo,clay
   ```
   Dedupes on `linkedin_url_canonical` → `email` → `name+domain` (the name+domain pass
   catches the same person across sources even with different LinkedIn URL forms). Priority
   `prospeo,apollo,clay`: Prospeo carries the fullest profile, Apollo contributes verified
   work email for its own people, Clay fills remaining gaps. Because Prospeo people are
   excluded from the Apollo leg, cross-source person overlap is minimal by design. Trim to
   the user's max-leads cap. Show the merge report.
3. **Push to Clay** (webhook resolved from the table ID; ingestion is free):
   ```bash
   python scripts/push_to_clay.py --in runs/<run>/merged.jsonl \
       --table-id "<table-id-from-intake>" --batch-delay 0.1
   ```
   One POST/record; retries failures into `runs/<run>/push_failures.jsonl`.

### Step 6 — Report ACTUAL credits used + wrap up

Re-check the Apollo balance, then show what was really consumed:

```bash
python scripts/credit_report.py --mode actual \
    --item "Apollo|bulk_match enrich (own candidates)|<#matched>|1" \
    --item "Prospeo|search pages|<#pages>|1" \
    --apollo-balance-before <before> --apollo-balance-after <after>
```

Then tell the user: final pushed count (ok/failed), actual credits per provider, the
dedup key in use, and a reminder that Clay's auto-dedupe on `linkedin_url_canonical` is
the authoritative dedup (records without a LinkedIn URL carry `fallback_dedupe_key` for a
manual second pass). Keep the `runs/<run>/` folder — it's the audit trail.

## Failure handling

- "Key missing" → CHECK THE SKILL-ROOT `.env` FIRST (scripts auto-load it; `printenv`
  won't show it). Only ask the user if it's truly absent from `.env` too.
- 401/403 from Apollo → key isn't a master key, or plan lacks API search access.
- Prospeo `INVALID_FILTERS` with a `filter_error` → enum/format mismatch; consult
  `references/api_notes.md` + the `mappings/*.json`. Common ones: seniority must be the
  long enum ("Vice President" not "VP"); **`company_revenue` values are STRING bands
  ("500K") not integers (500000)**; locations/industries must come from Search Suggestions.
- Clay webhook non-2xx → URL stale (regenerates if the source is recreated) or auth
  token mismatch.
- Never print API keys in output. Never push raw un-normalized records to Clay.
