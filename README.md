# Multi-Source List Builder

A [Claude Code](https://claude.com/claude-code) **skill** that builds B2B prospect lists from
**Prospeo, Apollo and Clay** in one run. You describe who you want in plain language. The skill
translates it into each platform's filters, pulls the people, merges them into one clean schema,
removes duplicates, and pushes the result to a **Clay table** via webhook.

> *"Build a list of VPs and Heads of Sales at US software companies, 51-200 employees,
> cap 1,000 per source, push to my main Clay table."*

**No credentials are included in this repository.** You bring your own API keys; they live in a
local `.env` file that is git-ignored (see [Setup](#setup)).

---

## What problem it solves

Each data provider covers a different slice of the market, and each uses different field names,
enums and filter rules. Doing this by hand means re-typing the same ICP three times, spending
credits on people you already have, and cleaning duplicates afterwards. This skill:

- takes **one** ICP description and translates it for every platform,
- never pays twice for the same person (exclusions are applied *before* any credit is spent),
- asks for approval **once**, with a credit estimate, before spending anything,
- leaves an audit trail of every run.

## How it works, step by step

| # | Step | What happens | Cost |
|---|------|--------------|------|
| 1 | **Intake** | Your plain-language brief becomes one ICP spec (titles, seniority, location, industry, company size, revenue, exclusions, cap). Only genuinely ambiguous items are asked, in a single recap. | Free |
| 2 | **Translate filters** | The spec is converted into Apollo and Prospeo filter payloads using the lookup tables in `mappings/` (industry, seniority, revenue). | Free |
| 3a | **Prospeo pull** | Full profiles (name, LinkedIn, company, domain, location). Emails stay masked. | Small search cost |
| 3b | **Extract exclusions** | LinkedIn URLs and company domains from the Prospeo results are saved so those people are not enriched again. | Free |
| 3c | **Apollo search only** | LinkedIn URLs (from Prospeo and any do-not-contact list you supply) are resolved to Apollo IDs and subtracted *before* enrichment. Apollo search returns IDs only. | Free |
| 3e | **Clay discovery** | Optional. Over the company domains found so far, the Clay MCP finds additional contacts. Discovery only, no paid data points. | Free |
| 4 | **Credit estimate + approval gate** | You see how many records will be enriched, the estimated credits and your Apollo balance. **Nothing is spent until you approve.** | - |
| 5 | **Spend and push** | Apollo enriches *only its own* candidates (verified work email). Everything is normalized, merged, de-duplicated and POSTed to your Clay table webhook. | Apollo credits |
| 6 | **Report** | Final pushed count, actual credits used per provider, dedupe key in use. | - |

**Enrichment policy:** each tool enriches only its own records. Prospeo records are taken as-is,
Clay is discovery-only, and final email enrichment is left to your own waterfall inside Clay.
That keeps spend predictable and attributable.

### Output fields

First Name, Last Name, Full Name, Company Name, Person Location, Company HQ, Industry, Email,
LinkedIn URL, Website, Employee Size, Revenue (plus title, seniority, email status and
de-duplication keys).

### De-duplication

Records are de-duplicated locally on `LinkedIn URL -> email -> name + company domain`, then
Clay's **auto-dedupe** on `linkedin_url_canonical` does the authoritative pass.

## Repository layout

```
multi-source-list-builder/
  SKILL.md              The skill itself: full workflow Claude follows
  README.md             Short setup notes
  .env.example          Names of the variables you must set (no values)
  requirements.txt      requests, pyyaml
  config/
    icp.example.yaml          Example ICP spec
    clay_tables.example.json  Example registry of Clay table -> webhook URL
  mappings/             Industry, seniority and revenue translation tables
  references/api_notes.md   Apollo / Prospeo / Clay parameter and credit reference
  scripts/              pull_prospeo, pull_apollo, resolve_linkedin_ids, extract_exclusions,
                        normalize_and_merge, push_to_clay, credit_report
  runs/                 Created per run (git-ignored): filters, raw pulls, merged output
INSTALL_CLAUDE_CODE.md  Install options for Claude Code
```

## Setup

You need accounts with **Apollo** (a *master* API key), **Prospeo**, and **Clay** (a table with a
webhook source). Optionally connect the **Clay MCP** server in Claude Code for the discovery leg.

1. Copy the skill into Claude Code's skills folder:
   ```bash
   mkdir -p ~/.claude/skills
   cp -R multi-source-list-builder ~/.claude/skills/
   pip install -r ~/.claude/skills/multi-source-list-builder/requirements.txt
   ```
2. Create your own credentials file (it is git-ignored and never leaves your machine):
   ```bash
   cd ~/.claude/skills/multi-source-list-builder
   cp .env.example .env
   # edit .env and fill in APOLLO_API_KEY and PROSPEO_API_KEY
   ```
3. In your destination Clay table: **+ Add source -> Webhook**, copy the URL, and turn on
   **Auto-dedupe** keyed on `linkedin_url_canonical`. Register the URL in
   `config/clay_tables.json` (copy `clay_tables.example.json`; the real file is git-ignored).
4. Restart Claude Code and describe the list you want.

## Security notes

- `.env`, `config/clay_tables.json` and everything under `runs/` are in `.gitignore`.
  **Never commit them.** Run output contains personal contact data.
- Never paste API keys into chat. Put them in `.env`.
- If a key is ever exposed, rotate it in the provider's dashboard.

## Requirements

Claude Code, Python 3.9+, and your own Apollo, Prospeo and Clay accounts. Usage consumes
credits on your own provider accounts.
