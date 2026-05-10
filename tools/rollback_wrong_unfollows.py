"""
One-shot rollback script: undo the bogus `following_status: unfollowed` entries
that were written when the search-based unfollow flow could not actually type
the username into the in-list search bar (and so wrongly classified candidates
as 'not in your Following list anymore').

Reads the log file to recover the affected usernames and reverts their
status to `followed`.
"""
import json
import re
import sys
from pathlib import Path

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else "marramattia_fmgpro"
ROOT = Path(__file__).resolve().parent.parent
log_path = ROOT / "logs" / f"{ACCOUNT}.log"
json_path = ROOT / "accounts" / ACCOUNT / "interacted_users.json"

victims = set()
with log_path.open(encoding="utf-8") as f:
    for line in f:
        if "not found in your Following list anymore" in line:
            m = re.search(r"@([a-zA-Z0-9_.]+)", line)
            if m:
                victims.add(m.group(1))

print(f"Victims to rollback: {len(victims)}")

with json_path.open(encoding="utf-8") as f:
    data = json.load(f)

changed = 0
for u in sorted(victims):
    if u in data:
        cur = data[u].get("following_status")
        if cur == "unfollowed":
            data[u]["following_status"] = "followed"
            changed += 1
            print(f"  rollback @{u}: unfollowed -> followed")
        else:
            print(f"  skip @{u}: already '{cur}'")
    else:
        print(f"  skip @{u}: not in interacted_users.json")

with json_path.open("w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f"\nDone. Reverted {changed} entries.")

