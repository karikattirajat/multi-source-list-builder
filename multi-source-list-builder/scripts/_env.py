"""Shared .env loader for the multi-source-list-builder scripts.

Why this exists: the skill keeps API keys in a `.env` file at the SKILL ROOT
(next to SKILL.md), not exported into the shell. A previous run wrongly reported
PROSPEO_API_KEY "missing" because it only checked the shell environment. Calling
load_env() at import time makes every script read that `.env` automatically, so
keys are found whether they're exported OR sitting in the file.

Existing real environment variables always win (we never overwrite an export),
so a user CAN still override the file by exporting a var for one run.
"""
import os


def load_env(filename=".env"):
    # skill root = parent of this scripts/ directory
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, filename)
    if not os.path.exists(path):
        return path  # nothing to load; caller still validates required keys
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:   # don't clobber real exports
                os.environ[key] = val
    return path


# Load immediately on import so argparse defaults that read os.environ
# (e.g. push_to_clay.py's --webhook default) see the values.
load_env()
