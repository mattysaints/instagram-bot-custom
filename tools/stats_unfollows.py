"""Quick stats: chi e' stato unfollowato dal bot, e quanti giorni fa."""
import json
import sys
from datetime import datetime
from collections import Counter
from pathlib import Path


def main(account: str = "simonebestagno") -> int:
    p = Path(__file__).resolve().parent.parent / "accounts" / account / "interacted_users.json"
    if not p.is_file():
        print(f"File non trovato: {p}")
        return 1

    with open(p, encoding="utf-8") as f:
        data = json.load(f)

    now = datetime.now()
    unfollowed = []
    for username, u in data.items():
        if u.get("following_status") == "unfollowed" or u.get("unfollowed") is True:
            try:
                last = datetime.strptime(u["last_interaction"], "%Y-%m-%d %H:%M:%S.%f")
            except (KeyError, ValueError):
                continue
            days_ago = (now - last).days
            unfollowed.append((username, last, days_ago, u.get("job_name", "?")))

    print(f"Totale unfollow registrati: {len(unfollowed)}")
    print()
    if not unfollowed:
        print("Nessun unfollow ancora registrato.")
        return 0

    unfollowed.sort(key=lambda x: x[1], reverse=True)

    print("=== Distribuzione per giorni fa ===")
    buckets = Counter()
    for _, _, d_ago, _ in unfollowed:
        if d_ago == 0:
            buckets["oggi"] += 1
        elif d_ago == 1:
            buckets["ieri"] += 1
        elif d_ago <= 7:
            buckets["2-7 gg"] += 1
        elif d_ago <= 30:
            buckets["8-30 gg"] += 1
        else:
            buckets[">30 gg"] += 1
    order = ["oggi", "ieri", "2-7 gg", "8-30 gg", ">30 gg"]
    for k in order:
        if k in buckets:
            print(f"  {k:10s} {buckets[k]:5d}")

    print()
    print("=== Per job ===")
    for k, v in Counter(j for _, _, _, j in unfollowed).most_common():
        print(f"  {k:30s} {v}")

    print()
    print("=== Ultimi 20 unfollow (piu recenti) ===")
    fmt = "  {ago:>3d}gg fa  {when}  @{user:30s}  [{job}]"
    for username, last, d_ago, job in unfollowed[:20]:
        print(fmt.format(ago=d_ago, when=last.strftime("%Y-%m-%d %H:%M"),
                         user=username, job=job))

    if len(unfollowed) > 20:
        print()
        print("=== Primi 5 (piu vecchi) ===")
        for username, last, d_ago, job in unfollowed[-5:]:
            print(fmt.format(ago=d_ago, when=last.strftime("%Y-%m-%d %H:%M"),
                             user=username, job=job))

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "simonebestagno"))

