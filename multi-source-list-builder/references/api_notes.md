# API Notes — Apollo / Prospeo / Clay (verified June 2026)

Read the relevant section before translating filters or debugging a pull.

## Canonical Schema (what every record looks like after normalization)

The first 12 are the **default output fields** the user expects as columns in the
final list — they are always present in every record (null if a source lacks them).

| Field | Default? | Notes |
|---|---|---|
| first_name, last_name, full_name | ✅ | full_name reconstructed if missing; split into parts if only full_name given |
| company_name | ✅ | employer name |
| person_location | ✅ | "City, State, Country" joined from the person's location (Clay's combined string parsed) |
| company_hq | ✅ | "City, State, Country" of the company HQ (Apollo org / Prospeo company location) |
| industry | ✅ | as reported by source |
| email | ✅ | Apollo enrich returns real emails. Prospeo emails are MASKED and NOT revealed by this skill (no cross-tool enrichment) — the normalizer nulls masked values and sets email_status=`masked_unrevealed`. Clay-only people have no email. User reveals the rest in Clay. |
| linkedin_url | ✅ | as returned by source |
| website | ✅ | company website, canonicalized to a bare domain |
| employee_size | ✅ | headcount (integer) or Prospeo range label |
| revenue | ✅ | Apollo prints e.g. "$1.5M"; Prospeo gives a band like "10B+"; null if unknown |
| title | | raw job title string |
| seniority | | source-reported seniority if available |
| email_status | | verified / unavailable / extrapolated (Apollo); VERIFIED/UNAVAILABLE (Prospeo) |
| linkedin_url_canonical | | `linkedin.com/in/<slug>` lowercase, no protocol/www/trailing slash/params — **primary dedup key** |
| company_domain, company_linkedin_url | | domain canonicalized (no protocol/www/path) |
| city, state, country | | raw person-location components (person_location is the joined form) |
| source | | platform of the record's first occurrence |
| sources | | comma list of all platforms that returned this person |
| source_id | | platform-native id |
| fallback_dedupe_key | | `first\|last\|domain` lowercase — secondary dedup key; ALSO collapses the same person when two sources return different LinkedIn URL forms (vanity vs numbered slug) |
| pulled_at | | UTC ISO timestamp |

## Apollo — People API Search

- `POST https://api.apollo.io/api/v1/mixed_people/api_search`
- Auth: header `X-Api-Key` — must be a **master** API key.
- Credit-free; does NOT return emails/phones (use `people/match` enrichment later if needed).
- 100 records/page (`per_page`), max 500 pages (50k display cap). Narrow filters for bigger universes and pull in segments.
- Common body params (arrays):
  - `person_titles[]` — free-text titles, ~OR semantics; include common variants ("VP Sales", "Vice President of Sales").
  - `person_seniorities[]` — enum: owner, founder, c_suite, partner, vp, head, director, manager, senior, entry, intern.
  - `person_locations[]` — e.g. "California, US", "United States".
  - `organization_locations[]` — company HQ.
  - `organization_num_employees_ranges[]` — strings like "51,200".
  - `q_organization_domains_list[]` — named-account targeting.
  - `q_keywords` — keyword search.
- Response: `{"people":[...], "contacts":[...], "pagination":{"page","per_page","total_entries","total_pages"}}`.
- Person object highlights: `id, first_name, last_name, name, title, seniority, linkedin_url, city, state, country, organization{name, primary_domain, website_url, linkedin_url, industry, estimated_num_employees}`.
- ⚠️ VERIFIED June 2026: on standard/free API plans `api_search` returns **obfuscated preview records** — `first_name` + `last_name_obfuscated`, no `name`, no `linkedin_url`, no city/state/country, and `organization` carries only `has_*` boolean flags (no `primary_domain`/`industry`/`estimated_num_employees`). You get `has_email`/`has_city`/etc. flags, not the values. To materialize full fields (name, LinkedIn, domain, location, email) you must enrich the returned `id`s via `people/match` / `people/bulk_match` (consumes credits), or use a plan whose search returns full data. Treat Apollo `api_search` alone as a candidate-ID discovery leg, not a finished-contact source.
- For AI agents: `https://docs.apollo.io/llms.txt` indexes all docs in Markdown.

## Prospeo — Search Person API

- `POST https://api.prospeo.io/search-person`
- Auth: header `X-KEY`.
- 25 results/page, max 1000 pages; body: `{"page": N, "filters": {...}}`.
- Filters are nested objects, most with `include`/`exclude` arrays. VERIFIED filter KEY NAMES (June 2026 — these differ from older docs, get them exact or you get a 400 `INVALID_FILTERS`):
  ```json
  {
    "person_job_title": {"include": ["VP Sales"], "exclude": ["Assistant"], "match_mode": "CONTAINS"},
    "person_seniority": {"include": ["Vice President"]},
    "person_location_search": {"include": ["United States"]},
    "company_industry": {"include": ["Software Development"]},
    "company_headcount_range": ["51-100", "101-200"],
    "company_location_search": {"include": ["United States"]},
    "company_revenue": {"min": "500K", "include_unknown_revenue": false}
  }
  ```
  - `person_job_title`: `match_mode` is one of `CONTAINS` (default), `EXACT`, `SIMILAR`; or use a `boolean_search` string. Max 100 items.
  - `person_seniority`: enum, EXACT values are `C-Suite, Director, Entry, Founder/Owner, Head, Intern, Manager, Partner, Senior, Vice President`. NOT `VP`/`C-Level`/`Owner`/`Founder` (those 400). See `mappings/seniority_map.json`.
  - `person_location_search` / `company_location_search`: NOT `person_location`/`company_location`. Values must be EXACT strings pulled from the Search Suggestions API (below) — arbitrary strings like "nyc" are rejected.
  - `company_headcount_range`: a **plain array of strings** (no include/exclude wrapper), from the fixed set `1-10, 11-20, 21-50, 51-100, 101-200, 201-500, 501-1000, 1001-2000, 2001-5000, 5001-10000, 10000+`. NOT `company_employee_range`.
  - `company_revenue`: ✅ **Prospeo DOES support revenue filtering** (verified live June 2026 — earlier notes wrongly said it didn't). Object `{"min": "<band>", "max": "<band>", "include_unknown_revenue": false}`. `min`/`max` are STRINGS from the enum `<100K, 100K, 500K, 1M, 5M, 10M, 25M, 50M, 100M, 250M, 500M, 1B, 5B, 10B+` — passing an integer like `500000` returns a 400 "Invalid value". Requires Starter plan+. See `mappings/revenue_map.json`. Example: min $0.5M → `{"company_revenue": {"min": "500K", "include_unknown_revenue": false}}`.
  - `company_industry`: enum (256 values), include/exclude arrays.
- **Search Suggestions API** (free, ~15 req/s): `POST https://api.prospeo.io/search-suggestions`, headers `X-KEY` + `Content-Type: application/json`. Body `{"location_search": "united states"}` → `{"error": false, "location_suggestions": [{"name": "United States", "type": "COUNTRY"}, {"name": "California, United States", "type": "STATE"}, ...]}` (types: COUNTRY/STATE/CITY/ZONE). Use the returned `name` verbatim in `person_location_search`. Also supports job-title/industry suggestion queries. ALWAYS resolve location strings through this endpoint before searching.
- Industries, seniorities, headcount ranges, funding stages etc. are **enums** — values must match Prospeo's published lists (api-docs > Enum Values). An off-list value returns a 400 `INVALID_FILTERS` with a `filter_error` naming the bad filter.
- At least one positive (include) filter is mandatory — exclude-only searches are rejected.
- Unavailable via API: company_intent, person_socials, company_oids/list_oids filters.
- 1 credit per search request returning ≥1 result (max 25 results per credit).
- Response: `{"error": false, "results": [{...person/company objects...}], "pagination": {"current_page","per_page","total_page"}}`.
- NOTE: legacy endpoints (email-finder, mobile-finder, email-verifier, etc.) were deprecated in favor of Enrich Person / Search Person — don't use them.
- On the FIRST run, dump one raw result and verify `PROSPEO_PATHS` in normalize_and_merge.py matches the actual nesting; fix once if needed.

## Credit costs (drive the pre-push estimate + post-push actuals)

Each provider bills in its own currency — keep them separate, don't sum into one number.

| Provider | Action | Cost | How to count |
|---|---|---|---|
| Apollo | `mixed_people/api_search` | **0** (credit-free) | discovery only |
| Apollo | `people/bulk_match` enrich | **1 credit per matched person**, 0 if unmatched | response returns `credits_consumed`; estimate = # people to enrich |
| Prospeo | `search-person` page | **1 credit per page that returns ≥1 result** (25 rec/page) | estimate = ceil(expected_records / 25) |
| Prospeo | `search-suggestions` | **0** (free, ~15 req/s) | resolving location/industry |
| Prospeo | email/mobile **reveal** | **credits per revealed contact** | NOT used by this skill — Prospeo emails are left masked; the user reveals them in Clay. Never reveal Prospeo emails by routing them through Apollo. |
| Clay | `find-and-enrich-contacts-at-company` discovery | minimal/none for the search itself | the search returns contacts |
| Clay | each enrichment **data point** (Email, etc.) | **Clay credits per data point per contact** | estimate = # contacts × # data points requested |
| Clay | webhook ingestion (push) | **0** | pushing rows costs nothing |

Before spending: check Apollo balance via the Profile endpoint with `include_credit_usage=true`.
Use `scripts/credit_report.py` to render the estimate (mode=estimate) and the actuals
(mode=actual, with Apollo balance before/after) in a consistent table.

## Clay — webhook ingestion + dedup

- Each table can expose a webhook source: **+ Add source → Webhook → Monitor webhook** → URL like `https://api.clay.com/v3/sources/webhook/pull-in-data-from-a-webhook-<uuid>`.
- Send one flat JSON object per POST, `Content-Type: application/json`. Keys become columns (map automatically on first receipt; keep key names stable across runs).
- Optional auth token can be configured on the webhook; if the user enabled it, send it (script uses `x-clay-webhook-auth` header — confirm the header name shown in their webhook config and adjust if different).
- **Auto-dedupe** (table settings, bottom-right icon): monitors one column, keeps the oldest row, deletes newer duplicates. Set it on `linkedin_url_canonical`. Caveats: blank cells and >200-char values are skipped — which is why records without LinkedIn URLs carry `fallback_dedupe_key` for a manual second-pass dedupe (right-click column → Dedupe → Delete Duplicate Row).
- With auto-dedupe enabled, an incoming webhook record matching an existing key UPDATES that row instead of creating a duplicate — this is what makes Clay the authoritative consolidation layer.

## Clay as a SOURCE (third pull leg)

Clay has no public filter-based people-search REST endpoint. Options:
1. **Clay MCP** (Claude Code connector, `https://api.clay.com/v3/mcp`): `find-and-enrich-contacts-at-company` searches people at one company with title/seniority/location filters — loop it over a named-account/domain list. Returns a taskId; fetch values via `get-task-context`.
2. **Clay-native source on the destination table**: add a "Find People" source with the same ICP directly in the table; auto-dedupe consolidates it with pushed records. Zero code, but lives outside this pipeline.
