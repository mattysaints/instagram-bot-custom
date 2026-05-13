"""Stats su interacted_users.json."""
import json
from collections import Counter

PATH = "accounts/simonebestagno/interacted_users.json"

with open(PATH, encoding="utf-8") as f:
    data = json.load(f)

total = len(data)
status_count = Counter(u.get("following_status", "unknown") for u in data.values())
followed = [k for k, v in data.items() if v.get("followed") and not v.get("unfollowed")]
requested = [k for k, v in data.items() if v.get("following_status") == "requested"]
unfollowed = [k for k, v in data.items() if v.get("unfollowed")]

print(f"Totale utenti tracciati: {total}")
print()
print("=== Distribuzione per following_status ===")
for s, n in status_count.most_common():
    print(f"  {s:<12s} {n}")
print()
print(f"Follow attivi (followed=true, unfollowed=false): {len(followed)}")
print(f"Follow request pendenti (privati):                {len(requested)}")
print(f"Sganciati:                                        {len(unfollowed)}")
print()
print("=== Ultimi 5 follow/request ===")
sorted_users = sorted(
    ((k, v) for k, v in data.items() if v.get("followed")),
    key=lambda x: x[1].get("last_interaction", ""),
    reverse=True,
)[:5]
for k, v in sorted_users:
    ts = v.get("last_interaction", "?")[:19]
    st = v.get("following_status", "?")
    tgt = v.get("target", "-") or "-"
    print(f"  {ts} | {st:<10s} | from {tgt:<25s} | @{k}")

