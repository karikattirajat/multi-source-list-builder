#!/usr/bin/env python3
"""Format a credit ledger for the list-build run — used twice:

1. BEFORE the run commits credits / pushes (mode=estimate): show the user the
   approximate credits each leg will consume so they can approve with eyes open.
2. AFTER the push (mode=actual): show what was really consumed, optionally with
   the Apollo balance before/after as a sanity check.

Credits live in different currencies per provider, so we keep each line item in
its own provider column rather than pretending there's one global "credit".

Each --item is "Provider|Label|units|credits_per_unit", e.g.
  --item "Apollo|bulk_match enrich|120|1"
  --item "Prospeo|search pages (25 rec/pg)|8|1"
  --item "Prospeo|email reveal|0|1"
  --item "Clay|Email data point|0|1"

Usage:
  python credit_report.py --mode estimate --item "Apollo|bulk_match|120|1" ...
  python credit_report.py --mode actual   --item "Apollo|bulk_match|118|1" \
      --apollo-balance-before 167899 --apollo-balance-after 167781
"""
import argparse


def parse_item(s):
    parts = s.split("|")
    if len(parts) != 4:
        raise ValueError(f"bad --item {s!r}; expected 'Provider|Label|units|credits_per_unit'")
    provider, label, units, cpu = parts
    return provider.strip(), label.strip(), float(units), float(cpu)


def fmt(n):
    return str(int(n)) if float(n).is_integer() else f"{n:.1f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["estimate", "actual"], required=True)
    ap.add_argument("--item", action="append", default=[],
                    help="Provider|Label|units|credits_per_unit (repeatable)")
    ap.add_argument("--apollo-balance-before", type=int)
    ap.add_argument("--apollo-balance-after", type=int)
    ap.add_argument("--note", action="append", default=[])
    args = ap.parse_args()

    items = [parse_item(s) for s in args.item]
    word = "ESTIMATED" if args.mode == "estimate" else "ACTUAL"
    print(f"\n### {word} credit usage\n")
    print("| Provider | Line item | Units | Credits/unit | Credits |")
    print("|---|---|---:|---:|---:|")

    per_provider = {}
    for provider, label, units, cpu in items:
        credits = units * cpu
        per_provider[provider] = per_provider.get(provider, 0) + credits
        verb = "up to " if args.mode == "estimate" else ""
        print(f"| {provider} | {label} | {fmt(units)} | {fmt(cpu)} | {verb}{fmt(credits)} |")

    print("\n**Per-provider total:**")
    for provider, total in per_provider.items():
        prefix = "≈ " if args.mode == "estimate" else ""
        print(f"- {provider}: {prefix}{fmt(total)} credits")

    if args.apollo_balance_before is not None:
        print(f"\nApollo balance before: {args.apollo_balance_before:,}")
        if args.apollo_balance_after is not None:
            delta = args.apollo_balance_before - args.apollo_balance_after
            print(f"Apollo balance after:  {args.apollo_balance_after:,}  "
                  f"(consumed {delta:,})")

    if args.mode == "estimate":
        print("\n_Estimates are upper bounds — unmatched enrichments and "
              "already-revealed records cost nothing. Confirm to proceed._")
    for n in args.note:
        print(f"- {n}")
    print()


if __name__ == "__main__":
    main()
