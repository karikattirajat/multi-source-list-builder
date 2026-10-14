#!/usr/bin/env python3
"""Resolve a list of LinkedIn URLs to Apollo person IDs -- credit-free.

Use this to turn ANY LinkedIn-URL list (a suppression list, an already-
contacted list, the Prospeo-overlap list, etc.) into Apollo person IDs
BEFORE enrichment, so those people are never revealed and never cost a
credit. Feed the output straight into pull_apollo.py's --exclude-ids-file.

Mechanism: POSTs mixed_people/api_search with person_linkedin_urls in
batches of <=100. This is a SEARCH call, not an enrich call -- Apollo
returns obfuscated records (no name/email) but the `id` field IS present
and free. URLs Apollo doesn't have in its DB simply don't resolve, which
is fine: they can't cost credits either way.

Usage:
  python resolve_linkedin_ids.py \\
      --in  runs/<run>/user_exclusions.txt \\
      --out runs/<run>/user_exclusion_ids.json
"""
import argparse, json, os, re, sys, time
import requests
import _env  # auto-loads skill-root .env into os.environ on import

SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
BATCH_SIZE = 100

# NOTE: the slug charclass must exclude \s -- a bare [^/?#]+ swallows a
# trailing newline from a text file into the slug and the match silently
# fails, which once shipped 207 excluded people into a "clean" merged list.
LI_RE = re.compile(r"linkedin\.com/(in|pub)/([^/?#\s]+)", re.IGNORECASE)


def canonical_slug(url):
    if not url or not isinstance(url, str):
        return None
    m = LI_RE.search(url.strip())
    return m.group(2).lower().rstrip("/") if m else None


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", required=True,
                    help="Text file, one LinkedIn URL per line")
    ap.add_argument("--out", required=True,
                    help="Output JSON: list of resolved Apollo person IDs")
    ap.add_argument("--unresolved-out", default=None,
                    help="Optional: write URLs that didn't resolve to an Apollo ID")
    args = ap.parse_args()

    api_key = os.environ.get("APOLLO_API_KEY")
    if not api_key:
        sys.exit("ERROR: APOLLO_API_KEY not found in shell env or skill-root .env. "
                 "Add it to the .env file next to SKILL.md (APOLLO_API_KEY=...).")

    slugs = []
    seen = set()
    with open(args.infile) as f:
        for line in f:
            slug = canonical_slug(line)
            if slug and slug not in seen:
                seen.add(slug)
                slugs.append(slug)

    if not slugs:
        sys.exit(f"ERROR: no valid LinkedIn URLs parsed from {args.infile}")

    print(f"Loaded {len(slugs)} unique LinkedIn URLs from {args.infile}.")

    urls = [f"https://www.linkedin.com/in/{s}" for s in slugs]
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": api_key,
    }

    resolved_ids = []
    resolved_slugs = set()
    saw_linkedin_url_field = False
    total_batches = -(-len(urls) // BATCH_SIZE)

    print(f"Resolving via mixed_people/api_search (credit-free) in {total_batches} batches...")
    for i in range(0, len(urls), BATCH_SIZE):
        batch = urls[i:i + BATCH_SIZE]
        payload = {"person_linkedin_urls": batch, "per_page": BATCH_SIZE, "page": 1}
        data = _post_with_retry(SEARCH_URL, headers, payload, "Apollo search").json()
        people = (data.get("people") or []) + (data.get("contacts") or [])
        for p in people:
            pid = p.get("id")
            if not pid:
                continue
            resolved_ids.append(pid)
            li = p.get("linkedin_url")
            if li:
                saw_linkedin_url_field = True
                slug = canonical_slug(li)
                if slug:
                    resolved_slugs.add(slug)
        batch_num = i // BATCH_SIZE + 1
        print(f"  batch {batch_num}/{total_batches}: +{len(people)} matched  "
              f"running_total={len(resolved_ids)}")
        time.sleep(0.3)

    with open(args.out, "w") as f:
        json.dump(resolved_ids, f)

    print(f"DONE: {len(resolved_ids)}/{len(slugs)} LinkedIn URLs resolved to Apollo IDs "
          f"-> {args.out} (0 credits used).")
    print(f"  {len(slugs) - len(resolved_ids)} not found in Apollo's DB "
          f"(expected -- they can't cost credits either way).")

    if args.unresolved_out:
        if not saw_linkedin_url_field:
            print("  NOTE: Apollo did not echo linkedin_url in the response, so the "
                  "unresolved list can't be computed reliably -- skipping "
                  "--unresolved-out.", file=sys.stderr)
        else:
            unresolved = [s for s in slugs if s not in resolved_slugs]
            with open(args.unresolved_out, "w") as f:
                for s in unresolved:
                    f.write(f"https://www.linkedin.com/in/{s}\n")
            print(f"  Unresolved URLs written -> {args.unresolved_out}")


if __name__ == "__main__":
    main()
