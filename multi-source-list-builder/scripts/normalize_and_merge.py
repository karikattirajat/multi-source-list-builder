#!/usr/bin/env python3
"""Normalize Apollo/Prospeo/Clay raw records into one canonical schema,
canonicalize LinkedIn URLs + domains, merge and pre-dedupe.

Dedup key order: linkedin_url_canonical -> email -> first+last+company_domain.
Field-level merge: first non-empty value wins, in --priority order.

DEFAULT OUTPUT FIELDS (always present in every record, even if null):
  first_name, last_name, full_name, company_name, person_location, company_hq,
  industry, email, linkedin_url, website, employee_size, revenue
These are the columns the user expects in the final list. Everything else
(title, seniority, email_status, domains, dedupe keys, provenance) is carried
alongside them so nothing is lost.

Usage:
  python normalize_and_merge.py --apollo raw_apollo.jsonl --prospeo raw_prospeo.jsonl \
      [--clay raw_clay.jsonl] --out merged.jsonl --report merge_report.json \
      [--priority prospeo,apollo,clay]
"""
import argparse, json, re, sys
from datetime import datetime, timezone

# Order matters: the 12 user-facing defaults come first so the JSONL/columns
# read naturally, then supporting fields, then provenance/dedupe plumbing.
CANONICAL_FIELDS = [
    # --- 12 default output fields ---
    "first_name", "last_name", "full_name",
    "company_name", "person_location", "company_hq", "industry",
    "email", "linkedin_url", "website", "employee_size", "revenue",
    # --- supporting fields ---
    "title", "seniority", "email_status",
    "linkedin_url_canonical", "company_domain", "company_linkedin_url",
    "city", "state", "country",
    # --- provenance / dedupe ---
    "source", "sources", "source_id", "fallback_dedupe_key", "pulled_at",
]

# ---- direct per-source field paths (dot = nested). Component fields used only
# ---- to DERIVE person_location / company_hq are prefixed with "_" and are NOT
# ---- emitted in the output record.
APOLLO_PATHS = {
    "first_name": ["first_name"],
    "last_name": ["last_name"],
    "full_name": ["name"],
    "title": ["title"],
    "seniority": ["seniority"],
    "linkedin_url": ["linkedin_url"],
    "email": ["email"],
    "email_status": ["email_status"],
    "company_name": ["organization.name", "account.name"],
    "company_domain": ["organization.primary_domain", "account.primary_domain"],
    "company_linkedin_url": ["organization.linkedin_url"],
    "website": ["organization.website_url", "organization.primary_domain"],
    "industry": ["organization.industry"],
    "employee_size": ["organization.estimated_num_employees"],
    "revenue": ["organization.annual_revenue_printed", "organization.annual_revenue"],
    "city": ["city"],
    "state": ["state"],
    "country": ["country"],
    "_company_city": ["organization.city"],
    "_company_state": ["organization.state"],
    "_company_country": ["organization.country"],
    "source_id": ["id"],
}

# Prospeo nests under person/company.
PROSPEO_PATHS = {
    "first_name": ["person.first_name", "person.firstName", "first_name"],
    "last_name": ["person.last_name", "person.lastName", "last_name"],
    "full_name": ["person.full_name", "person.fullName", "full_name"],
    "title": ["person.current_job_title", "person.job_title", "person.jobTitle",
              "job_title"],
    "seniority": ["person.seniority", "seniority"],
    "linkedin_url": ["person.linkedin_url", "person.linkedin", "linkedin_url",
                     "person.social.linkedin"],
    # Prospeo returns a MASKED email unless revealed; status still tells you it exists.
    "email": ["person.email.email", "person.email.address", "email"],
    "email_status": ["person.email.status", "email_status"],
    "company_name": ["company.name", "company_name"],
    "company_domain": ["company.domain", "company.website", "company_domain"],
    "company_linkedin_url": ["company.linkedin_url", "company.linkedin"],
    "website": ["company.website", "company.domain"],
    "industry": ["company.industry"],
    "employee_size": ["company.employee_count", "company.employee_range",
                      "company.employees", "company.size"],
    "revenue": ["company.revenue_range_printed"],
    "city": ["person.location.city", "person.city"],
    "state": ["person.location.state", "person.state"],
    "country": ["person.location.country", "person.country"],
    "_company_city": ["company.location.city"],
    "_company_state": ["company.location.state"],
    "_company_country": ["company.location.country"],
    "source_id": ["person.person_id", "person.oid", "person.id", "oid", "id"],
}

# Clay MCP / Clay-exported records. Contact-level objects rarely carry company
# HQ / website / revenue, so those stay null for Clay-only people and get filled
# by Apollo/Prospeo on merge when the same person appears in another source.
CLAY_PATHS = {
    "first_name": ["first_name", "firstName", "First Name"],
    "last_name": ["last_name", "lastName", "Last Name"],
    "full_name": ["full_name", "name", "Full Name"],
    "title": ["title", "latest_experience_title", "job_title", "Job Title"],
    "seniority": ["seniority"],
    "linkedin_url": ["linkedin_url", "url", "linkedin", "LinkedIn Profile",
                     "linkedin_profile"],
    "email": ["email", "work_email", "Work Email"],
    "email_status": ["email_status"],
    "company_name": ["company_name", "latest_experience_company", "company", "Company"],
    "company_domain": ["company_domain", "domain", "Domain", "website"],
    "company_linkedin_url": ["company_linkedin_url"],
    "website": ["website", "company_domain", "domain"],
    "industry": ["industry", "Industry"],
    "employee_size": ["employee_count", "employee_size", "headcount"],
    "revenue": ["revenue", "annual_revenue"],
    "city": ["city", "City"],
    "state": ["state", "State"],
    "country": ["country", "Country"],
    "_location_name": ["location_name"],   # Clay gives one combined string
    "source_id": ["source_id", "profile_id", "entityId", "entity_id", "id"],
}

SOURCE_PATHS = {"apollo": APOLLO_PATHS, "prospeo": PROSPEO_PATHS, "clay": CLAY_PATHS}


def dig(record, dotted):
    cur = record
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def first_value(record, paths):
    for p in paths:
        v = dig(record, p)
        if v not in (None, "", [], {}):
            return v
    return None


LI_RE = re.compile(r"linkedin\.com/(in|pub)/([^/?#]+)", re.IGNORECASE)


def canonical_linkedin(url):
    if not url or not isinstance(url, str):
        return None
    m = LI_RE.search(url.strip())
    if not m:
        return None
    return f"linkedin.com/in/{m.group(2).lower().rstrip('/')}"


def canonical_domain(value):
    if not value or not isinstance(value, str):
        return None
    v = value.strip().lower()
    v = re.sub(r"^https?://", "", v)
    v = re.sub(r"^www\.", "", v)
    v = v.split("/")[0].split("?")[0]
    return v or None


def join_location(*parts):
    seen, out = set(), []
    for p in parts:
        if p and str(p).strip() and str(p).strip().lower() not in seen:
            seen.add(str(p).strip().lower())
            out.append(str(p).strip())
    return ", ".join(out) or None


def normalize(record, source):
    paths = SOURCE_PATHS[source]
    out = {f: None for f in CANONICAL_FIELDS}
    # only assign real canonical fields; "_"-prefixed component paths handled below
    for field, candidates in paths.items():
        if field.startswith("_"):
            continue
        out[field] = first_value(record, candidates)

    # Prospeo stores title/seniority in the current job_history entry, not flat.
    if source == "prospeo":
        jobs = (record.get("person") or {}).get("job_history") or []
        current = next((j for j in jobs if j.get("current")), jobs[0] if jobs else None)
        if current:
            if not out["title"]:
                out["title"] = current.get("title")
            if not out["seniority"]:
                out["seniority"] = current.get("seniority")

    # Names: backfill full_name from parts, or split full_name into parts.
    if not out["full_name"] and (out["first_name"] or out["last_name"]):
        out["full_name"] = " ".join(filter(None, [out["first_name"], out["last_name"]]))
    if out["full_name"] and not out["first_name"]:
        parts = str(out["full_name"]).split()
        if parts:
            out["first_name"] = parts[0]
            out["last_name"] = " ".join(parts[1:]) or None

    # person_location — prefer explicit city/state/country; Clay gives one string.
    if source == "clay" and not (out["city"] or out["state"] or out["country"]):
        loc = first_value(record, paths.get("_location_name", []))
        out["person_location"] = loc
        # also seed country/state/city heuristically from the combined string
        if loc:
            bits = [b.strip() for b in str(loc).split(",")]
            out["city"] = out["city"] or (bits[0] if bits else None)
            if len(bits) >= 3:
                out["state"] = out["state"] or bits[1]
                out["country"] = out["country"] or bits[-1]
            elif len(bits) == 2:
                out["country"] = out["country"] or bits[-1]
    if not out["person_location"]:
        out["person_location"] = join_location(out["city"], out["state"], out["country"])

    # company_hq — from company location components when available.
    out["company_hq"] = join_location(
        first_value(record, paths.get("_company_city", [])),
        first_value(record, paths.get("_company_state", [])),
        first_value(record, paths.get("_company_country", [])),
    )

    # website canonicalized to a bare domain for consistency.
    out["website"] = canonical_domain(out["website"])
    out["linkedin_url_canonical"] = canonical_linkedin(out["linkedin_url"])
    out["company_domain"] = canonical_domain(out["company_domain"])
    if out["email"]:
        out["email"] = str(out["email"]).strip().lower()
        # Prospeo returns MASKED emails (e.g. "a******@x.com") unless revealed.
        # A masked value is not a usable email — treat it as empty so it never
        # masquerades as a deliverable address. We keep the fact that an email
        # EXISTS in email_status so the user knows it's revealable downstream.
        if "*" in out["email"]:
            orig = out["email_status"]
            out["email"] = None
            # keep the provider's verdict so the user knows a VERIFIED email is
            # revealable downstream vs UNAVAILABLE (none to find): "masked:VERIFIED"
            out["email_status"] = f"masked:{orig}" if orig else "masked_unrevealed"
    out["source"] = source
    out["sources"] = source
    out["pulled_at"] = datetime.now(timezone.utc).isoformat()

    fn = (out["first_name"] or "").strip().lower()
    ln = (out["last_name"] or "").strip().lower()
    dom = out["company_domain"] or ""
    out["fallback_dedupe_key"] = f"{fn}|{ln}|{dom}" if (fn and dom) else None
    return out


def dedupe_key(rec):
    if rec["linkedin_url_canonical"]:
        return ("li", rec["linkedin_url_canonical"])
    if rec["email"]:
        return ("em", rec["email"])
    if rec["fallback_dedupe_key"]:
        return ("nm", rec["fallback_dedupe_key"])
    return ("uid", f"{rec['source']}:{rec.get('source_id') or id(rec)}")


def merge(primary, secondary):
    """Fill empty fields of primary with secondary's values; track sources."""
    for f in CANONICAL_FIELDS:
        if f in ("source", "sources"):
            continue
        if primary.get(f) in (None, "", []) and secondary.get(f) not in (None, "", []):
            primary[f] = secondary[f]
    srcs = set(primary["sources"].split(",")) | set(secondary["sources"].split(","))
    primary["sources"] = ",".join(sorted(srcs))
    return primary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apollo")
    ap.add_argument("--prospeo")
    ap.add_argument("--clay")
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", default=None)
    ap.add_argument("--priority", default="prospeo,apollo,clay")
    args = ap.parse_args()

    priority = [s.strip() for s in args.priority.split(",") if s.strip()]
    inputs = {"apollo": args.apollo, "prospeo": args.prospeo, "clay": args.clay}

    counts = {}
    merged = {}
    order_seen = []

    for source in priority:
        path = inputs.get(source)
        if not path:
            continue
        n = 0
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = normalize(json.loads(line), source)
                    n += 1
                    key = dedupe_key(rec)
                    if key in merged:
                        merged[key] = merge(merged[key], rec)
                    else:
                        merged[key] = rec
                        order_seen.append(key)
        except FileNotFoundError:
            print(f"WARN: {path} not found, skipping {source}", file=sys.stderr)
            continue
        counts[source] = n

    # Second pass: a record keyed by email/fallback may share an email with a
    # LinkedIn-keyed record — collapse those. Also collapse records that share a
    # fallback name+domain key but have DIFFERENT LinkedIn URL forms (Apollo's
    # vanity slug vs Prospeo/Clay's numbered slug for the same person). This is a
    # real-world dedup gap: same human, two URL spellings -> two LinkedIn keys.
    by_email, by_name = {}, {}
    final_keys = []
    for key in order_seen:
        rec = merged.get(key)
        if rec is None:
            continue
        em = rec.get("email")
        nm = rec.get("fallback_dedupe_key")
        if em and em in by_email:
            merged[by_email[em]] = merge(merged[by_email[em]], rec)
            merged[key] = None
            continue
        if nm and nm in by_name:
            merged[by_name[nm]] = merge(merged[by_name[nm]], rec)
            merged[key] = None
            continue
        if em:
            by_email[em] = key
        if nm:
            by_name[nm] = key
        final_keys.append(key)

    final = [merged[k] for k in final_keys if merged.get(k)]
    with open(args.out, "w") as out:
        for rec in final:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")

    report = {
        "input_counts": counts,
        "total_raw": sum(counts.values()),
        "after_dedupe": len(final),
        "duplicates_collapsed": sum(counts.values()) - len(final),
        "missing_linkedin_url": sum(1 for r in final if not r["linkedin_url_canonical"]),
        "with_email": sum(1 for r in final if r["email"]),
        "with_revenue": sum(1 for r in final if r["revenue"]),
        "multi_source_records": sum(1 for r in final if "," in r["sources"]),
    }
    if args.report:
        with open(args.report, "w") as f:
            json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))
    print(f"DONE merge: {len(final)} records -> {args.out}")


if __name__ == "__main__":
    main()
