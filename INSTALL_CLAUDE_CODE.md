# Installing the Multi-Source List Builder in Claude Code

This repository contains **no API keys or credentials**. You supply your own.

## Option A: personal install
```bash
git clone https://github.com/karikattirajat/multi-source-list-builder.git
mkdir -p ~/.claude/skills
cp -R multi-source-list-builder/multi-source-list-builder ~/.claude/skills/
pip install -r ~/.claude/skills/multi-source-list-builder/requirements.txt
```
Add your keys:
```bash
cd ~/.claude/skills/multi-source-list-builder
cp .env.example .env     # then fill in APOLLO_API_KEY and PROSPEO_API_KEY
```
Restart Claude Code. The skill triggers when you ask it to build or pull a prospect list.

## Option B: project install (shared with a team)
Copy the skill folder into `<your-project>/.claude/skills/`. Each teammate creates their own
`.env` locally. Keep `.env` out of version control.

## Requirements per machine
- Python 3.9+ and `pip install -r requirements.txt` (requests, pyyaml).
- Apollo (master API key) and Prospeo keys in `.env`. These legs are script-based, no MCP needed.
- Optional: the Clay MCP server connected in Claude Code for the Clay discovery leg. Without it,
  runs still work with Prospeo + Apollo and the Clay discovery step is skipped.

## What to expect in a run
One intake, credit-free discovery, **one approval gate showing the credit estimate**, then
enrichment and push, and a report of the actual credits used. Each run writes only to its own
`runs/<run-name>/` folder.

See `README.md` for the full step-by-step flow and `multi-source-list-builder/SKILL.md` for the
detailed workflow.
