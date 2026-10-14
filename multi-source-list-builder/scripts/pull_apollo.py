#!/usr/bin/env python3
"""Pull people from Apollo — two-phase: search IDs then bulk_match enrich.

Phase 1 — Search  (credit-free):
  POST /api/v1/mixed_people/api_search  → collects person IDs only.
  The search response is intentionally obfuscated (no name, no LinkedIn URL,
  no domain) — IDs are the only usable output.

Phase 2 — Enrich  (uses credits, emails YES, phone NEVER):
  POST /api/v1/people/bulk_match in batches of 10 → full records:
  first_name, last_name, name, title, seniority, linkedin_url, email,
  email_status, city, state, country, organization (name, primary_domain,
  industry, estimated_num_employees, linkedin_url).

If --exclude-ids-file is supplied (JSON list of Apollo person IDs, produced
by resolve_linkedin_ids.py from any LinkedIn-URL exclusion list -- Prospeo
overlap, a user-supplied suppression list, etc.), those IDs are dropped from
the candidate list BEFORE enrichment. This is the credit-safe path: excluded
people are never revealed, so they never cost a credit. Repeatable -- pass
it once per exclusion source.

--exclude-linkedin-file (legacy) is still supported as a POST-enrich safety
net (one canonical URL per line, produced by extract_exclusions.py). Prefer
--exclude-ids-file: this one pays a credit for every excluded person before
discarding them, so only use it for a final belt-and-suspenders pass.

Usage:
  python pull_apollo.py \\
      --filters   runs/<run>/apollo_filters.json \\
      --out       runs/<run>/raw_apollo.jsonl \\
      [--max-records 1000] \\
      [--exclude-ids-file runs/<run>/prospeo_exclusion_ids.json] \\
      [--exclude-ids-file runs/<run>/user_exclusion_ids.json]
"""
import argparse, json, os, re, sys, time
import requests
import _env  # auto-loads skill-root .env into os.environ on import

SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
ENRICH_URL = "https://api.apollo.io/api/v1/people/bulk_match"
PER_PAGE   = 100
BATCH_SIZE = 10   # bulk_match hard limit per request

LI_RE = re.compile(r"linkedin\.com/(in|pub)/([^/?#]+)", re.IGNORECASE)


def canonical_li(url):
    if not url or not isinstance(url, str):
        return None
    m = LI_RE.search(url.strip())
    return f"linkedin.com/in/{m.group(2).lower().rstrip('/')}" if m else None


def _post_with_retry(url, headers, payload, label="Apollo"):
    resp = None
    for attempt in range(5):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
        except requests.RequestException as exc:
            print(f"  {label} request error (attempt {attempt+1}): {exc}", file=sys.stderr)
            time.sleep(2 ** attempt)
            continue
        if resp.status_code == 429:
            wait = 2 ** attempt
            print(f"  429 rate-limited ({label}), retrying in {wait}s...", file=sys.stderr)
            time.sleep(wait)
            continue
        break
    if resp is None or resp.status_code != 200:
        code = resp.status_code if resp is not None else "n/a"
        body = resp.text[:600] if resp is not None else ""
        sys.exit(f"ERROR: {label} returned {code}: {body}")
    return resp


def search_phase(api_key, base_payload, max_records):
    """Phase 1: paginate mixed_people/api_search to collect person IDs."""
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": api_key,
    }
    ids = []
    page = 1
    max_pages = min(500, -(-max_records // PER_PAGE))

    print("Phase 1 — searching Apollo (credit-free ID discovery)...")
    while page <= max_pages and len(ids) < max_records:
        payload = dict(base_payload, page=page, per_page=PER_PAGE)
        data = _post_with_retry(SEARCH_URL, headers, payload, "Apollo search").json()
        people = (data.get("people") or []) + (data.get("contacts") or [])
        if not people:
            break
        for p in people:
            if len(ids) >= max_records:
                break
            if p.get("id"):
                ids.append(p["id"])
        pagination = data.get("pagination") or {}
        total_pages = pagination.get("total_pages")
        print(f"  page {page}/{total_pages or '?'}: +{len(people)} ids  (total {len(ids)})")
        if total_pages and page >= int(total_pages):
            break
        page += 1
        time.sleep(0.3)

    print(f"Phase 1 done: {len(ids)} IDs collected.")
    return ids


def enrich_phase(api_key, ids):
    """Phase 2: bulk_match in batches of 10 → full records, emails on, phone off."""
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": api_key,
    }
    enriched = []
    total_batches = -(-len(ids) // BATCH_SIZE)

    print(f"Phase 2 — enriching {len(ids)} IDs via people/bulk_match "
          f"(emails: YES, phone: NO) in {total_batches} batches...")

    for i in range(0, len(ids), BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]
        payload = {
            "reveal_personal_emails": True,
            "reveal_phone_number": False,
            "details": [{"id": pid} for pid in batch],
        }
        data = _post_with_retry(ENRICH_URL, headers, payload, "Apollo enrich").json()
        matches = data.get("matches") or []
        credits_used = data.get("credits_consumed", "?")
        enriched.extend(m for m in matches if m)   # filter None/null entries
        batch_num = i // BATCH_SIZE + 1
        print(f"  batch {batch_num}/{total_batches}: +{len(matches)} records  "
              f"credits_consumed={credits_used}  running_total={len(enriched)}")
        time.sleep(0.5)   # respect rate limits

    print(f"Phase 2 done: {len(enriched)} enriched records.")
    return enriched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--filters", required=True,
                    help="JSON file with Apollo search filter payload")
    ap.add_argument("--out", required=True,
                    help="Output JSONL path")
    ap.add_argument("--max-records", type=int, default=1000,
                    help="Max records to pull (default 1000)")
    ap.add_argument("--exclude-linkedin-file", default=None,
                    help="LEGACY, POST-enrich only: text file with one LinkedIn URL "
                         "per line to exclude (produced by extract_exclusions.py after "
                         "Prospeo pull). Costs a credit per excluded person -- prefer "
                         "--exclude-ids-file.")
    ap.add_argument("--exclude-ids-file", action="append", default=None,
                    help="JSON file with a list of Apollo person IDs to exclude BEFORE "
                         "enrichment (credit-free). Repeatable -- pass once per exclusion "
                         "source. Produced by resolve_linkedin_ids.py from any LinkedIn-URL "
                         "list (Prospeo overlap, a user-supplied suppression list, etc.).")
    ap.add_argument("--search-only", action="store_true",
                    help="Run ONLY the credit-free Phase-1 search; write candidate IDs "
                         "to --ids-out and stop. Use this to size the enrichment spend "
                         "BEFORE the credit-approval gate. No credits consumed.")
    ap.add_argument("--ids-out", default=None,
                    help="Where to write candidate IDs (JSON list) in --search-only mode. "
                         "Defaults to <out>.ids.json")
    ap.add_argument("--enrich-ids-file", default=None,
                    help="Skip search; enrich the IDs in this JSON-list file "
                         "(produced earlier by --search-only). Use AFTER approval.")
    args = ap.parse_args()

    api_key = os.environ.get("APOLLO_API_KEY")
    if not api_key:
        sys.exit("ERROR: APOLLO_API_KEY not found in shell env or skill-root .env. "
                 "Add it to the .env file next to SKILL.md (APOLLO_API_KEY=...). "
                 "Must be a MASTER key for the search endpoint.")

    with open(args.filters) as f:
        base_payload = json.load(f)

    # Load Prospeo exclusion set (canonical linkedin.com/in/<slug> strings)
    exclusion_set = set()
    if args.exclude_linkedin_file:
        if not os.path.exists(args.exclude_linkedin_file):
            print(f"WARNING: exclusion file not found: {args.exclude_linkedin_file}",
                  file=sys.stderr)
        else:
            with open(args.exclude_linkedin_file) as f:
                for line in f:
                    url = line.strip()
                    if url:
                        canon = canonical_li(url) or url.lower()
                        exclusion_set.add(canon)
            print(f"Loaded {len(exclusion_set)} LinkedIn URLs for post-enrich exclusion.")

    # --- Phase 1: ID discovery (credit-free) ---
    if args.enrich_ids_file:
        with open(args.enrich_ids_file) as f:
            ids = json.load(f)
        print(f"Loaded {len(ids)} candidate IDs from {args.enrich_ids_file} (skipping search).")
    else:
        ids = search_phase(api_key, base_payload, args.max_records)

    # --- Pre-enrich exclusion (credit-free): drop excluded IDs before Phase 2 ---
    if args.exclude_ids_file:
        exclude_ids = set()
        for path in args.exclude_ids_file:
            if not os.path.exists(path):
                print(f"WARNING: exclude-ids file not found: {path}", file=sys.stderr)
                continue
            with open(path) as f:
                exclude_ids.update(json.load(f))
        if exclude_ids:
            before = len(ids)
            ids = [i for i in ids if i not in exclude_ids]
            dropped = before - len(ids)
            print(f"Pre-enrich exclusion: dropped {dropped}/{before} candidate IDs "
                  f"({len(exclude_ids)} exclusion IDs loaded) -- saves {dropped} "
                  f"enrichment credits.")

    if not ids:
        print("No IDs returned by search — check your filters.")
        open(args.out, "w").close()
        return

    # In search-only mode, stop here so the run can size the enrichment spend
    # and get credit approval BEFORE Phase 2 consumes any credits.
    if args.search_only:
        ids_out = args.ids_out or (args.out + ".ids.json")
        with open(ids_out, "w") as f:
            json.dump(ids, f)
        print(f"SEARCH-ONLY: {len(ids)} candidate IDs -> {ids_out} "
              f"(0 credits used). Enriching all would cost up to {len(ids)} credits.")
        return

    # --- Phase 2: full-data enrichment (1 credit per matched person) ---
    enriched = enrich_phase(api_key, ids)

    # --- Post-filter: drop records already in Prospeo ---
    if exclusion_set and enriched:
        before = len(enriched)
        enriched = [
            r for r in enriched
            if canonical_li(r.get("linkedin_url") or "") not in exclusion_set
        ]
        dropped = before - len(enriched)
        if dropped:
            print(f"Post-filter: dropped {dropped} records already present in Prospeo.")

    # --- Write output ---
    with open(args.out, "w") as f:
        for rec in enriched:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"DONE apollo: {len(enriched)} records -> {args.out}")


if __name__ == "__main__":
    main()
