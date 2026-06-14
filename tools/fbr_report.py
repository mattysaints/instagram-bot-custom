#!/usr/bin/env python3
"""Per-source follow-back-rate (FBR) report + blogger-list pruning helper.

Reads ``accounts/<username>/source_stats.json`` (filled by the in-app
auto-FBR refresh or by ``tools/recompute_fbr.py``) and prints a ranked
report of every source for a given job, classifying each as GOOD / OK /
DEAD / LOW-SIGNAL.

If ``--config`` is given, for the ``blogger-followers`` job it also reads the
current source list from the YAML and emits:
  - a PRUNED inline list (dead sources removed) ready to paste back, and
  - the list of DROPPED sources with the reason.

A source is only ever dropped when it has ENOUGH signal (>= --min-signal
follows) AND a low FBR (<= --low-fbr). Sources with little data are kept:
we don't have the evidence to judge them yet.

Usage:
    python tools/fbr_report.py [username]
    python tools/fbr_report.py marramattia_fmgpro --job blogger-followers \\
        --config accounts/marramattia_fmgpro/config.yml --emit-pruned
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_stats(account_path: Path) -> dict:
    p = account_path / "source_stats.json"
    if not p.exists():
        sys.exit(f"source_stats.json not found in {account_path}")
    return json.loads(p.read_text(encoding="utf-8"))


def current_list_from_config(config_path: Path, job: str) -> list[str]:
    """Best-effort parse of the inline ``job: [a, b, c]`` line from the YAML,
    reading only the FIRST uncommented occurrence."""
    if not config_path.exists():
        return []
    for line in config_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        m = re.match(rf"^{re.escape(job)}\s*:\s*\[(.*)\]\s*(#.*)?$", s)
        if m:
            inner = m.group(1)
            return [x.strip() for x in inner.split(",") if x.strip()]
    return []


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("username", nargs="?", default="marramattia_fmgpro")
    ap.add_argument("--job", default="blogger-followers")
    ap.add_argument("--min-signal", type=int, default=10,
                    help="min follows on a source before its FBR is trusted (default 10)")
    ap.add_argument("--low-fbr", type=float, default=0.05,
                    help="FBR at/below which a well-sampled source is DEAD (default 0.05)")
    ap.add_argument("--high-fbr", type=float, default=0.15,
                    help="FBR at/above which a source is GOOD (default 0.15)")
    ap.add_argument("--config", default=None,
                    help="path to config.yml to read the current source list and emit a pruned one")
    ap.add_argument("--emit-pruned", action="store_true",
                    help="print the pruned inline list ready to paste")
    args = ap.parse_args()

    account_path = ROOT / "accounts" / args.username
    data = load_stats(account_path)
    sources = data.get("sources", {})

    # Collect rows for the requested job.
    prefix = f"{args.job}|"
    rows = []
    for key, v in sources.items():
        if not key.startswith(prefix):
            continue
        name = key[len(prefix):]
        done = int(v.get("follows_done", 0))
        back = int(v.get("follows_back", 0))
        rate = v.get("follow_back_rate")
        rows.append([name, done, back, rate])

    if not rows:
        print(f"No data for job '{args.job}' in {account_path/'source_stats.json'}.")
        print("Available jobs:", sorted({k.split('|',1)[0] for k in sources}))
        return

    def classify(done, rate):
        if rate is None or done < args.min_signal:
            return "LOW-SIGNAL"
        if rate <= args.low_fbr:
            return "DEAD"
        if rate >= args.high_fbr:
            return "GOOD"
        return "OK"

    for r in rows:
        r.append(classify(r[1], r[3]))

    # Sort: rate desc (None last), then done desc.
    rows.sort(key=lambda r: (r[3] if r[3] is not None else -1.0, r[1]), reverse=True)

    last = data.get("last_auto_fbr_check")
    tot_done = sum(r[1] for r in rows)
    tot_back = sum(r[2] for r in rows)
    gfbr = 100 * tot_back / tot_done if tot_done else 0.0

    print("=" * 78)
    print(f"FBR REPORT — {args.username} — job '{args.job}'")
    print(f"last auto-FBR check: {last or 'never'}")
    print(f"sources: {len(rows)} | follows_done: {tot_done} | follows_back: {tot_back} "
          f"| GLOBAL FBR: {gfbr:.1f}%")
    print(f"thresholds: min-signal={args.min_signal}  DEAD<= {args.low_fbr*100:.0f}%  "
          f"GOOD>= {args.high_fbr*100:.0f}%")
    print("=" * 78)
    print(f"{'#':>3}  {'source':<34} {'done':>5} {'back':>5} {'FBR':>7}  class")
    print("-" * 78)
    for i, (name, done, back, rate, cls) in enumerate(rows, 1):
        rate_s = f"{rate*100:5.1f}%" if rate is not None else "  n/a"
        print(f"{i:>3}  {name:<34} {done:>5} {back:>5} {rate_s:>7}  {cls}")

    counts = {}
    for r in rows:
        counts[r[4]] = counts.get(r[4], 0) + 1
    print("-" * 78)
    print("summary:", ", ".join(f"{k}={counts[k]}" for k in ("GOOD", "OK", "DEAD", "LOW-SIGNAL") if k in counts))

    # --- Pruning suggestion -------------------------------------------------
    if args.config:
        cfg = Path(args.config)
        current = current_list_from_config(cfg, args.job)
        if not current:
            print(f"\n[prune] Could not read inline '{args.job}: [...]' from {cfg}.")
            return
        dead = {r[0] for r in rows if r[4] == "DEAD"}
        # Dedup while preserving order.
        seen = set()
        kept, dropped = [], []
        for s in current:
            if s in seen:
                dropped.append((s, "duplicate"))
                continue
            seen.add(s)
            if s in dead:
                dropped.append((s, "DEAD (low FBR, enough signal)"))
            else:
                kept.append(s)

        print("\n" + "=" * 78)
        print(f"[prune] current list: {len(current)} | kept: {len(kept)} | dropped: {len(dropped)}")
        print("=" * 78)
        if dropped:
            print("DROPPED:")
            for s, why in dropped:
                print(f"  - {s:<34} {why}")
        if args.emit_pruned:
            print(f"\n{args.job}: [{', '.join(kept)}]")


if __name__ == "__main__":
    main()
