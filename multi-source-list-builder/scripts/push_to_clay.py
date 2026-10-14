#!/usr/bin/env python3
"""Push merged canonical records to a Clay table webhook (one JSON POST per record).

Usage:
  python push_to_clay.py --in merged.jsonl --webhook "$CLAY_WEBHOOK_URL" \
      [--auth-token "$CLAY_WEBHOOK_AUTH_TOKEN"] [--batch-delay 0.1] [--dry-run]
"""
import argparse, json, os, sys, time
import requests
import _env  # auto-loads skill-root .env into os.environ BEFORE argparse reads defaults


def resolve_webhook(table_id):
    """Look up a table ID in config/clay_tables.json (relative to skill root)."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    registry_path = os.path.join(here, "config", "clay_tables.json")
    if not os.path.exists(registry_path):
        sys.exit(f"ERROR: table registry not found at {registry_path}. "
                 "Create it (see clay_tables.example.json) or pass --webhook.")
    with open(registry_path) as f:
        registry = json.load(f)
    entry = registry.get(table_id)
    if not entry:
        sys.exit(f"ERROR: table id '{table_id}' not in {registry_path}. "
                 f"Known ids: {', '.join(k for k in registry if not k.startswith('_'))}")
    return entry["webhook_url"] if isinstance(entry, dict) else entry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", required=True)
    ap.add_argument("--table-id", help="Clay table id (resolved via config/clay_tables.json)")
    ap.add_argument("--webhook", default=os.environ.get("CLAY_WEBHOOK_URL"))
    ap.add_argument("--auth-token", default=os.environ.get("CLAY_WEBHOOK_AUTH_TOKEN"))
    ap.add_argument("--batch-delay", type=float, default=0.1)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip", type=int, default=0, help="skip the first N records (resume a partial push)")
    args = ap.parse_args()

    if args.table_id:
        args.webhook = resolve_webhook(args.table_id)
    if not args.webhook:
        sys.exit("ERROR: no destination (pass --table-id or --webhook, "
                 "or set CLAY_WEBHOOK_URL).")

    headers = {"Content-Type": "application/json"}
    if args.auth_token:
        headers["x-clay-webhook-auth"] = args.auth_token

    ok, failed = 0, 0
    failures_path = os.path.join(os.path.dirname(os.path.abspath(args.infile)),
                                 "push_failures.jsonl")
    fails = open(failures_path, "w")

    with open(args.infile) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            if i <= args.skip:
                continue
            rec = json.loads(line)

            if args.dry_run:
                ok += 1
                continue

            success = False
            for attempt in range(4):
                try:
                    resp = requests.post(args.webhook, headers=headers,
                                         json=rec, timeout=30)
                except requests.RequestException as e:
                    time.sleep(2 ** attempt)
                    continue
                if resp.status_code == 429 or resp.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                success = 200 <= resp.status_code < 300
                break

            if success:
                ok += 1
            else:
                failed += 1
                fails.write(json.dumps(rec, ensure_ascii=False) + "\n")

            if i % 50 == 0:
                print(f"  pushed {i} (ok={ok}, failed={failed})", flush=True)
            time.sleep(args.batch_delay)

    fails.close()
    if failed == 0 and os.path.getsize(failures_path) == 0:
        os.remove(failures_path)

    mode = "DRY-RUN " if args.dry_run else ""
    print(f"DONE {mode}push: ok={ok}, failed={failed}"
          + (f" (failures saved to {failures_path})" if failed else ""))


if __name__ == "__main__":
    main()
