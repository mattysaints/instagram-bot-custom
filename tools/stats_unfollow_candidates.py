"""Verifica eta' al momento dell'unfollow: confronta last_interaction (ora
dell'unfollow) con il follow originale tracciato altrove se possibile.

Ma il problema: storage.add_interacted_user OVERWRITE last_interaction ad
ogni interazione. Quindi una volta unfollowato, il last_interaction NON
e' piu' la data del follow ma la data dell'unfollow stesso.

Per sapere quanti giorni fa li avevi seguiti dobbiamo guardare il job_name
e il session_id originali, ma anche quelli vengono preservati (vedi
storage.py:201-204 'if not user.get(...)'), quindi rimangono quelli del
follow iniziale. Pero' il TIMESTAMP del follow originale e' perso.

Soluzione approssimata: per gli utenti unfollowati OGGI, possiamo dedurre
l'eta' del follow guardando il loro session_id originale -> log file.
Pero' e' complesso.

Approccio piu' semplice: stampiamo il can_be_unfollowed check che il bot
fa, sui candidati ancora pendenti (followed/requested), per mostrare quanti
hanno >= unfollow-delay giorni e quanti meno.
"""
import json
import sys
from datetime import datetime
from collections import Counter
from pathlib import Path


def main(account: str = "simonebestagno", unfollow_delay_days: int = 2) -> int:
    p = Path(__file__).resolve().parent.parent / "accounts" / account / "interacted_users.json"
    if not p.is_file():
        print(f"File non trovato: {p}")
        return 1

    with open(p, encoding="utf-8") as f:
        data = json.load(f)

    now = datetime.now()
    candidates = []  # (username, last_interaction, days_ago, status)
    for username, u in data.items():
        fs = u.get("following_status", "")
        if fs not in ("followed", "requested"):
            continue
        try:
            last = datetime.strptime(u["last_interaction"], "%Y-%m-%d %H:%M:%S.%f")
        except (KeyError, ValueError):
            continue
        days_ago = (now - last).days
        candidates.append((username, last, days_ago, fs))

    candidates.sort(key=lambda x: x[1])

    print(f"Candidati totali (status=followed/requested): {len(candidates)}")
    print(f"Configurato unfollow-delay: {unfollow_delay_days} giorni")
    print()

    eligible = [c for c in candidates if c[2] >= unfollow_delay_days]
    too_recent = [c for c in candidates if c[2] < unfollow_delay_days]

    print(f"  ELIGIBILI per unfollow (>= {unfollow_delay_days}gg fa): {len(eligible)}")
    print(f"  Troppo recenti (< {unfollow_delay_days}gg, SKIP):       {len(too_recent)}")
    print()

    print("=== Distribuzione per giorni fa ===")
    buckets = Counter()
    for _, _, d_ago, _ in candidates:
        if d_ago == 0:
            buckets["oggi (0gg)"] += 1
        elif d_ago == 1:
            buckets["ieri (1gg)"] += 1
        elif d_ago <= 7:
            buckets["2-7 gg"] += 1
        elif d_ago <= 30:
            buckets["8-30 gg"] += 1
        else:
            buckets[">30 gg"] += 1
    for k in ["oggi (0gg)", "ieri (1gg)", "2-7 gg", "8-30 gg", ">30 gg"]:
        if k in buckets:
            print(f"  {k:14s} {buckets[k]:5d}")

    print()
    print("=== I 10 candidati piu' VECCHI (saranno unfolloati per primi) ===")
    for username, last, d_ago, fs in candidates[:10]:
        marker = "OK" if d_ago >= unfollow_delay_days else "skip"
        print(f"  {d_ago:4d}gg  {last.strftime('%Y-%m-%d %H:%M')}  @{username:30s} [{fs}] {marker}")

    if too_recent:
        print()
        print(f"=== I 5 'troppo recenti' (< {unfollow_delay_days}gg) ===")
        for username, last, d_ago, fs in too_recent[:5]:
            print(f"  {d_ago:4d}gg  {last.strftime('%Y-%m-%d %H:%M')}  @{username:30s} [{fs}]")

    return 0


if __name__ == "__main__":
    delay = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "simonebestagno", delay))

