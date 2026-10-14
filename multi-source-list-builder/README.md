# Multi-Source List Builder — Setup

A Claude Code skill that builds prospect lists from **Apollo + Prospeo (+ Clay MCP)**,
normalizes them into one schema, and pushes the consolidated list to a **Clay table
webhook** where auto-dedupe consolidates everything.

## Install

1. In Claude Code, install the `.skill` file (or drop the `multi-source-list-builder/`
   folder into `~/.claude/skills/`).
2. Set credentials (shell profile or a `.env` you source before running):
   ```bash
   export APOLLO_API_KEY="..."        # must be a MASTER api key
   export PROSPEO_API_KEY="..."
   # Clay destinations are per-table: registered in config/clay_tables.json (table ID -> webhook URL)
   # export CLAY_WEBHOOK_AUTH_TOKEN="..."   # only if you enabled auth on the webhook
   ```
3. One-time Clay table setup:
   - Add a **Webhook** source to your destination table and copy its URL.
   - Enable **Auto-dedupe** keyed on the `linkedin_url_canonical` column
     (the column appears after the first push).
4. Optional: connect the **Clay MCP** server in Claude Code
   (`https://api.clay.com/v3/mcp`) to enable the Clay pull leg for named-account lists.
5. `pip install requests pyyaml`

## Use

In Claude Code, just describe the list in plain language:

> Build a list of VPs and Heads of Sales at US software companies, 51–200 employees,
> cap 1000 per source, push to my main Clay table.

Claude will: write the canonical ICP config → translate filters per platform
(using `mappings/`) → run the pulls → normalize/merge/pre-dedupe → push to Clay →
report counts. Each run leaves an audit trail in `runs/<run-name>/`.

## Pushing to different Clay tables per segment

Create one webhook per destination table and either export them under different env
vars (e.g. `CLAY_WEBHOOK_URL_ENTERPRISE`) or pass `--webhook <url>` — then tell Claude
which table the run targets.

## Notes

- Apollo's search endpoint is credit-free but never returns emails; Prospeo often does.
  Email gaps can be filled later inside Clay (waterfall enrichment).
- Prospeo charges 1 credit per search request returning results (25 records/request).
- First Prospeo run: verify the field paths in `scripts/normalize_and_merge.py`
  (`PROSPEO_PATHS`) against one raw record — adjust once if your plan's response
  shape differs.
