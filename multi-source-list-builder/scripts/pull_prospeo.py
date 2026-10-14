#!/usr/bin/env python3
"""Pull people from Prospeo's Search Person API (/search-person).

- Requires PROSPEO_API_KEY env var.
- 25 results/page, up to 1000 pages via the `page` parameter.
- Filters use Prospeo's nested include/exclude format and enum values
  (industries, seniorities, employee ranges) — see references/api_notes.md.
- At least one positive (include) filter is required.

Usage:
  python pull_prospeo.py --filters prospeo_filters.json --out raw_prospeo.jsonl --max-records 1000
"""
import argparse, json, os, sys, time
import requests
import _env  # auto-loads skill-root .env into os.environ on import

API_URL = "https://api.prospeo.io/search-person"
PER_PAGE = 25


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--filters", required=True, help='JSON file: {"filters": {...}}')
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-records", type=int, default=1000)
    args = ap.parse_args()

    api_key = os.environ.get("PROSPEO_API_KEY")
    if not api_key:
        sys.exit("ERROR: PROSPEO_API_KEY not found in shell env or skill-root .env. "
                 "Add it to the .env file next to SKILL.md (PROSPEO_API_KEY=...).")

    with open(args.filters) as f:
        spec = json.load(f)
    filters = spec.get("filters", spec)  # accept either wrapped or bare filters

    # Enforce CONTAINS match mode on job title filter — NEVER use SIMILAR or EXACT
    # unless the caller explicitly set something other than SIMILAR.
    if "person_job_title" in filters and isinstance(filters["person_job_title"], dict):
        jt = filters["person_job_title"]
        current_mode = jt.get("match_mode", "").upper()
        if current_mode not in ("CONTAINS", "EXACT"):
            # Default to CONTAINS; only allow EXACT if the caller explicitly chose it
            jt["match_mode"] = "CONTAINS"
        if current_mode == "SIMILAR":
            # Silently upgrade SIMILAR → CONTAINS per workflow rules
            jt["match_mode"] = "CONTAINS"
            print("NOTE: match_mode upgraded from SIMILAR to CONTAINS per workflow rules.")

    headers = {"Content-Type": "application/json", "X-KEY": api_key}

    total_written = 0
    page = 1
    max_pages = min(1000, -(-args.max_records // PER_PAGE))

    with open(args.out, "w") as out:
        while page <= max_pages and total_written < args.max_records:
            payload = {"page": page, "filters": filters}

            resp = None
            for attempt in range(5):
                resp = requests.post(API_URL, headers=headers, json=payload, timeout=60)
                if resp.status_code == 429:
                    wait = 2 ** attempt
                    print(f"  429 rate-limited, retrying in {wait}s...", file=sys.stderr)
                    time.sleep(wait)
                    continue
                break

            if resp is None or resp.status_code != 200:
                code = resp.status_code if resp is not None else "n/a"
                body = resp.text[:500] if resp is not None else ""
                sys.exit(f"ERROR: Prospeo returned {code} on page {page}: {body}")

            data = resp.json()
            if data.get("error"):
                sys.exit(f"ERROR: Prospeo error on page {page}: "
                         f"{json.dumps(data)[:500]}")

            results = data.get("results", []) or []
            if not results:
                break

            for r in results:
                if total_written >= args.max_records:
                    break
                out.write(json.dumps(r, ensure_ascii=False) + "\n")
                total_written += 1

            pagination = data.get("pagination", {}) or {}
            total_pages = pagination.get("total_page") or pagination.get("total_pages")
            print(f"page {page}/{total_pages or '?'}: +{len(results)} (total {total_written})")
            if total_pages and page >= int(total_pages):
                break
            page += 1
            time.sleep(0.2)

    print(f"DONE prospeo: {total_written} records -> {args.out}")


if __name__ == "__main__":
    main()
