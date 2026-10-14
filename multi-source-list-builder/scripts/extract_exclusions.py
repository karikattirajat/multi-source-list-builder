#!/usr/bin/env python3
"""Extract LinkedIn URLs and company domains from a raw Prospeo JSONL file.

Run this AFTER pull_prospeo.py and BEFORE pull_apollo.py so that the Apollo
pull can skip people already sourced from Prospeo.

Also produces a company-domains file used in Step 3 to drive the Clay MCP
per-company pulls.

Usage:
  python extract_exclusions.py \\
      --in  runs/<run>/raw_prospeo.jsonl \\
      --linkedin-out  runs/<run>/prospeo_exclusions.txt \\
      --domains-out   runs/<run>/prospeo_domains.txt
"""
import argparse, json, re, sys

LI_RE = re.compile(r"linkedin\.com/(in|pub)/([^/?#]+)", re.IGNORECASE)


def canonical_li(url):
    if not url or not isinstance(url, str):
        return None
    m = LI_RE.search(url.strip())
    return f"linkedin.com/in/{m.group(2).lower().rstrip('/')}" if m else None


def canonical_domain(value):
    if not value or not isinstance(value, str):
        return None
    v = value.strip().lower()
    v = re.sub(r"^https?://", "", v)
    v = re.sub(r"^www\.", "", v)
    return v.split("/")[0].split("?")[0] or None


def dig(record, dotted_path):
    """Traverse dot-separated nested path, return value or None."""
    cur = record
    for part in dotted_path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", required=True,
                    help="Raw Prospeo JSONL (raw_prospeo.jsonl)")
    ap.add_argument("--linkedin-out", required=True,
                    help="Output: one canonical LinkedIn URL per line")
    ap.add_argument("--domains-out", required=True,
                    help="Output: one company domain per line (unique, sorted)")
    args = ap.parse_args()

    linkedin_urls = set()
    domains = set()
    total = 0

    with open(args.infile) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total += 1
            rec = json.loads(line)

            # LinkedIn URL — handles both:
            #   raw Prospeo JSONL:   person.linkedin_url
            #   normalized merged JSONL: linkedin_url_canonical or linkedin_url
            raw_li = (dig(rec, "person.linkedin_url")
                      or dig(rec, "linkedin_url_canonical")
                      or dig(rec, "linkedin_url"))
            canon_li = canonical_li(raw_li) or (raw_li if raw_li and "linkedin.com/in/" in raw_li else None)
            if canon_li:
                linkedin_urls.add(canon_li)

            # Company domain — handles both:
            #   raw Prospeo JSONL:   company.domain / company.website
            #   normalized merged JSONL: company_domain
            raw_domain = (dig(rec, "company.domain")
                          or dig(rec, "company.website")
                          or dig(rec, "company_domain"))
            canon_dom = canonical_domain(raw_domain)
            if canon_dom:
                domains.add(canon_dom)

    with open(args.linkedin_out, "w") as f:
        for url in sorted(linkedin_urls):
            f.write(url + "\n")

    with open(args.domains_out, "w") as f:
        for dom in sorted(domains):
            f.write(dom + "\n")

    print(f"Processed {total} Prospeo records.")
    print(f"  LinkedIn URLs written : {len(linkedin_urls)} -> {args.linkedin_out}")
    print(f"  Company domains written: {len(domains)} -> {args.domains_out}")


if __name__ == "__main__":
    main()
