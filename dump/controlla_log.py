#!/usr/bin/env python3
"""Controllo periodico dei due bot: cosa hanno fatto dall'ultimo giro.

Legge SOLO le righe nuove di ogni log (la posizione sta in dump/loop_pos.json,
aggiornata a ogni esecuzione) e stampa un riassunto compatto: stato sessione,
sorgente in corso, azioni, commenti AI, errori. Chiude segnalando i processi
vivi, uno per account: due bot sullo stesso emulatore sono il guaio peggiore.

Uso:
    python dump/controlla_log.py            # dalle posizioni salvate
    python dump/controlla_log.py --minuti 30  # ignora le posizioni, ultimi 30 min
"""
import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

# La console di Windows e' cp1252: senza questo, la prima emoji del log fa
# esplodere la stampa e il controllo non dice niente.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

ACCOUNT = ["rb.coach", "roberto_buonomo_ifbbpro"]
POSIZIONI = Path("dump/loop_pos.json")

# Righe che raccontano qualcosa. Il resto (DEBUG, elenco filtri, plugin) e'
# rumore: in dieci minuti di bot sono migliaia di righe.
INTERESSANTI = (
    "-------- START",
    "-------- FINISH",
    "Hello, @",
    "Crash saved",
    "App has crashed",
    "[ai-comment] generated:",
    "[ai-comment] fallback",
    "Next session will start",
    "block detected",
    "Total likes",
    "Total comments",
    "Total followed",
    "Working hours are over",
    "asking for the password",
    "Sorgente saltata",
    "quarantena",
)
RUMORE = (
    "Checking for block",
    "disable-block-detection",
    "Config used",
    "skip_",
)


def righe_nuove(log: Path, salvata: int, minuti: int | None):
    testo = log.read_text(encoding="utf-8", errors="replace").splitlines()
    if minuti is not None:
        limite = dt.datetime.now() - dt.timedelta(minutes=minuti)
        inizio = 0
        for i, r in enumerate(testo):
            m = re.match(r"\[(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2})\]", r)
            if not m:
                continue
            mese, giorno, h, mi, s = (int(x) for x in m.groups())
            quando = dt.datetime(limite.year, mese, giorno, h, mi, s)
            if quando >= limite:
                inizio = i
                break
        return testo[inizio:], len(testo)
    return testo[salvata:], len(testo)


def riassumi(nome: str, nuove: list) -> None:
    print(f"\n=== {nome}  ({len(nuove)} righe nuove)")
    conta = {
        "like": sum(1 for r in nuove if "Liked" in r or "Like succeed" in r),
        "follow": sum(1 for r in nuove if "Followed @" in r),
        "commenti": sum(1 for r in nuove if "Comment succeed" in r),
        "profili": sum(1 for r in nuove if ": interact" in r),
        "skip": sum(1 for r in nuove if " skip." in r.lower()),
        "crash": sum(1 for r in nuove if "Crash saved" in r),
        "ai generati": sum(1 for r in nuove if "[ai-comment] generated via" in r),
        "ai fallback": sum(1 for r in nuove if "[ai-comment]" in r and "fallback" in r),
    }
    print("   " + ", ".join(f"{k}={v}" for k, v in conta.items()))
    for r in nuove:
        if any(k in r for k in INTERESSANTI) and not any(k in r for k in RUMORE):
            if "DEBUG" in r:
                continue
            print("   " + r[:170])
    errori = [r for r in nuove if "ERROR" in r or ("WARNING" in r and "Checking" not in r)]
    if errori:
        print(f"   -- {len(errori)} righe ERROR/WARNING, ultime 6:")
        for r in errori[-6:]:
            print("      " + r[:170])


def processi():
    """Un solo bot per account: se ce ne sono due, e' il problema numero uno."""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
        "Where-Object { $_.CommandLine -like '*instagram-bot-custom*' -and "
        "$_.CommandLine -like '*run.py*' } | ForEach-Object { "
        "if ($_.CommandLine -match 'accounts[\\\\/]([^\\\\/]+)[\\\\/]') { $Matches[1] } else { 'sconosciuto' } }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=60,
        ).stdout
    except Exception as e:
        print(f"\n(processi non verificabili: {e})")
        return
    vivi = [r.strip() for r in out.splitlines() if r.strip()]
    print("\n=== processi bot vivi")
    for a in ACCOUNT:
        n = vivi.count(a)
        stato = "OK" if n == 1 else ("FERMO" if n == 0 else f"{n} ISTANZE (problema!)")
        print(f"   {a:24} {stato}")
    ignoti = [v for v in vivi if v not in ACCOUNT]
    if ignoti:
        print(f"   altri: {ignoti}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minuti", type=int, default=None,
                    help="Ignora le posizioni salvate e guarda gli ultimi N minuti.")
    args = ap.parse_args()

    pos = {}
    if POSIZIONI.exists():
        try:
            pos = json.loads(POSIZIONI.read_text(encoding="utf-8"))
        except Exception:
            pos = {}
    nuove_pos = {}
    print(f"controllo delle {dt.datetime.now():%H:%M:%S}")
    for a in ACCOUNT:
        log = Path(f"logs/{a}.log")
        if not log.exists():
            print(f"\n=== {a}: log assente")
            continue
        nuove, totale = righe_nuove(log, int(pos.get(a, 0)), args.minuti)
        nuove_pos[a] = totale
        riassumi(a, nuove)
    processi()
    POSIZIONI.parent.mkdir(parents=True, exist_ok=True)
    POSIZIONI.write_text(json.dumps(nuove_pos, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
