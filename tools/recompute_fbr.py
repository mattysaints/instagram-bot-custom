#!/usr/bin/env python3
"""Recompute follow-back rate (FBR) per source from interacted_users.json.

Usage:
    python tools/recompute_fbr.py <username> [--followers-file path]

You need a TXT/JSON list of usernames currently following the account.
Easiest way to obtain it: export from the IG web (or use an Instaloader/
similar tool). The file can be either:
  - a plain text file with one username per line
  - a JSON list of strings
  - a JSON object whose keys are usernames

After running, ``accounts/<username>/source_stats.json`` will have
``follows_back`` and ``follow_back_rate`` populated for every (job, target)
where the bot has performed at least one follow.

Example:
    python tools/recompute_fbr.py marramattia_fmgpro \\
        --followers-file my_followers.txt
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running the script without installing the package.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from GramAddict.core.source_stats import SourceStats  # noqa: E402


def load_followers(path: str):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text[0] in "[{":
        data = json.loads(text)
        if isinstance(data, dict):
            return list(data.keys())
        if isinstance(data, list):
            return [str(x) for x in data]
        raise ValueError("Unsupported JSON shape; use list or dict.")
    # plain text: one username per line, '@' optional
    return [line.strip().lstrip("@") for line in text.splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username", help="Bot account username (folder under accounts/)")
    parser.add_argument(
        "--followers-file",
        required=True,
        help="Path to a file containing the usernames currently following you.",
    )
    args = parser.parse_args()

    account_path = ROOT / "accounts" / args.username
    if not account_path.exists():
        sys.exit(f"Account folder not found: {account_path}")

    interacted_path = account_path / "interacted_users.json"
    if not interacted_path.exists():
        sys.exit(f"interacted_users.json not found in {account_path}")

    interacted = json.loads(interacted_path.read_text(encoding="utf-8"))
    followers = load_followers(args.followers_file)
    print(f"Loaded {len(followers)} followers from {args.followers_file}.")
    print(f"Loaded {len(interacted)} interacted users from {interacted_path}.")

    stats = SourceStats(str(account_path))
    per_source = stats.recompute_fbr_from_followers_set(interacted, followers)

    if not per_source:
        print("No (job, target) pairs found. Nothing to update.")
        return
    print()
    print("Per-source FBR (job|source -> done / back / rate):")
    rows = sorted(per_source.items(), key=lambda kv: -(kv[1][1] / max(kv[1][0], 1)))
    for key, (done, back) in rows:
        rate = (back / done) if done else 0.0
        print(f"  {key:<60}  {done:>4} / {back:>4}  ({rate*100:5.1f} %)")
    print()
    print(f"Saved updates to {stats.path}")


if __name__ == "__main__":
    main()


