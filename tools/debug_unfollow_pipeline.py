"""Diagnosi dettagliata: perche' ci sono cosi' pochi candidati per l'unfollow.

Esegue gli STESSI filtri che applica iterate_via_deeplink in
action_unfollow_followers.py, mostrando passo-passo quanti utenti
vengono scartati ad ogni stage.
"""
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


def load_whitelist(account_path: Path) -> set:
    p = account_path / "whitelist.txt"
    if not p.is_file():
        return set()
    return {line.strip().lower().lstrip("@") for line in p.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")}


def load_engaged(account_path: Path) -> set:
    p = account_path / "engaged_users.json"
    if not p.is_file():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {k.strip().lower().lstrip("@") for k in data.keys() if isinstance(k, str)}
        if isinstance(data, list):
            return {str(k).strip().lower().lstrip("@") for k in data}
    except Exception:
        return set()
    return set()


def main(account: str = "simonebestagno", unfollow_delay_days: int = 2) -> int:
    base = Path(__file__).resolve().parent.parent
    account_path = base / "accounts" / account
    p = account_path / "interacted_users.json"
    if not p.is_file():
        print(f"Non trovato: {p}")
        return 1

    data = json.loads(p.read_text(encoding="utf-8"))
    total = len(data)
    print(f"=== STAGE 0: Totale utenti in interacted_users.json: {total}")

    # STAGE 1: status filter
    by_status = Counter(u.get("following_status", "<missing>") for u in data.values())
    print()
    print("=== STAGE 1: Distribuzione per following_status")
    for k, v in by_status.most_common():
        print(f"   {k:15s} {v}")

    eligible_status = {k: v for k, v in data.items()
                       if v.get("following_status") in ("followed", "requested")}
    print()
    print(f"   ==> Passano (followed/requested): {len(eligible_status)}")
    print(f"   ==> Scartati (unfollowed/none/scraped/altro): {total - len(eligible_status)}")

    # STAGE 2: can_be_unfollowed (last_interaction >= N giorni fa)
    now = datetime.now()
    parseable = 0
    age_ok = []
    age_too_recent = 0
    bad_dates = 0
    for username, u in eligible_status.items():
        try:
            last = datetime.strptime(u["last_interaction"], "%Y-%m-%d %H:%M:%S.%f")
        except (KeyError, ValueError):
            bad_dates += 1
            continue
        parseable += 1
        days_ago = (now - last).days
        if days_ago >= unfollow_delay_days:
            age_ok.append((username, last, days_ago))
        else:
            age_too_recent += 1

    print()
    print(f"=== STAGE 2: Filtro temporale (last_interaction >= {unfollow_delay_days}gg fa)")
    print(f"   Date parsate OK: {parseable}, date corrotte: {bad_dates}")
    print(f"   ==> Maturi (>= {unfollow_delay_days}gg): {len(age_ok)}")
    print(f"   ==> Troppo recenti (< {unfollow_delay_days}gg): {age_too_recent}")

    # STAGE 3: whitelist
    whitelist = load_whitelist(account_path)
    print()
    print(f"=== STAGE 3: Whitelist ({len(whitelist)} utenti in whitelist.txt)")
    after_wl = []
    in_wl = 0
    for username, last, days_ago in age_ok:
        if username.lower().lstrip("@") in whitelist:
            in_wl += 1
            print(f"   SKIP whitelist: @{username}")
        else:
            after_wl.append((username, last, days_ago))
    print(f"   ==> Passano: {len(after_wl)} (skippati per whitelist: {in_wl})")

    # STAGE 4: engaged_users
    engaged = load_engaged(account_path)
    print()
    print(f"=== STAGE 4: Engaged users ({len(engaged)} in engaged_users.json)")
    after_eng = []
    in_eng = 0
    for username, last, days_ago in after_wl:
        if username.lower().lstrip("@") in engaged:
            in_eng += 1
            print(f"   SKIP engaged: @{username}")
        else:
            after_eng.append((username, last, days_ago))
    print(f"   ==> Passano: {len(after_eng)} (skippati per engaged: {in_eng})")

    # STAGE 5: candidati finali
    print()
    print(f"=== RISULTATO FINALE: {len(after_eng)} candidati eligibili")
    if after_eng:
        after_eng.sort(key=lambda x: x[1])
        for username, last, days_ago in after_eng:
            print(f"   {days_ago}gg fa  {last.strftime('%Y-%m-%d %H:%M')}  @{username}")

    print()
    print("=== Per capire perche' SONO POCHI: distribuzione dell'eta' dei followed/requested")
    age_buckets = Counter()
    for username, u in eligible_status.items():
        try:
            last = datetime.strptime(u["last_interaction"], "%Y-%m-%d %H:%M:%S.%f")
        except (KeyError, ValueError):
            continue
        d_ago = (now - last).days
        if d_ago == 0: age_buckets["0gg (oggi)"] += 1
        elif d_ago == 1: age_buckets["1gg (ieri)"] += 1
        elif d_ago == 2: age_buckets["2gg"] += 1
        elif d_ago <= 7: age_buckets["3-7gg"] += 1
        elif d_ago <= 30: age_buckets["8-30gg"] += 1
        else: age_buckets[">30gg"] += 1
    for k in ["0gg (oggi)", "1gg (ieri)", "2gg", "3-7gg", "8-30gg", ">30gg"]:
        if k in age_buckets:
            print(f"   {k:12s} {age_buckets[k]}")

    return 0


if __name__ == "__main__":
    delay = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "simonebestagno", delay))

