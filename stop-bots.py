#!/usr/bin/env python
"""Ferma tutti i bot attivi (e, con --riavvia, li rilancia).

Perche' esiste
--------------
I bot si lanciano dalle configurazioni PyCharm, una per account, e ogni
lancio e' una catena di quattro processi: il launcher del venv, il vero
run-dynamic.py, il launcher di run.py e run.py. Fermarli a mano dal Task
Manager vuol dire trovarli tutti e otto, e sbagliare costa: il 24/08 un
`taskkill /T` sull'albero ha portato via anche i due emulatori, perche'
Windows li considera discendenti della catena nonostante run-dynamic li
avvii con DETACHED_PROCESS.

Qui i processi si terminano UNO A UNO per PID, senza /T e senza mai toccare
emulator/qemu/adb. Poi si ripuliscono i lucchetti di device rimasti orfani,
quelli che fanno dire al lancio successivo "Su emulator-XXXX sta gia'
girando un bot".

Uso
---
  python stop-bots.py              # ferma e basta
  python stop-bots.py --riavvia    # ferma, poi rilancia i due account
  python stop-bots.py --dry-run    # dice solo cosa farebbe
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

RADICE = Path(__file__).resolve().parent

# I due account gestiti, con gli stessi parametri delle configurazioni
# PyCharm "Bot - ...". Se cambiano li' vanno cambiati anche qui.
ACCOUNT = [
    {
        "nome": "rb.coach",
        "config": "accounts/rb.coach/config.yml",
        "avd": "rbcoach",
        "sessioni": "5",
    },
    {
        "nome": "roberto_buonomo_ifbbpro",
        "config": "accounts/roberto_buonomo_ifbbpro/config.yml",
        "avd": "robertobuo",
        "sessioni": "5",
    },
]

# Cosa si considera "un bot". run-dynamic.py e' il supervisore, run.py la
# singola sessione. Attenzione: "run-dynamic.py" NON contiene "run.py", per
# questo servono due espressioni distinte.
E_UN_BOT = re.compile(r"run-dynamic\.py|(?<![\w-])run\.py(?=\s|$)")

# Mai toccare questi, nemmeno per sbaglio: l'emulatore ci mette minuti a
# ripartire e il bot lo ritrova da solo.
DA_NON_TOCCARE = re.compile(r"(?i)emulator|qemu|adb\.exe")


def _processi_python() -> list[dict]:
    """Elenco dei processi python con la loro riga di comando, via PowerShell
    (psutil non e' tra le dipendenze del progetto)."""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or "
        "Name='pythonw.exe'\" | "
        "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()
    except Exception as e:
        print(f"Non riesco a elencare i processi: {e}", file=sys.stderr)
        return []
    if not out:
        return []
    try:
        dati = json.loads(out)
    except json.JSONDecodeError:
        return []
    # ConvertTo-Json restituisce un oggetto solo, non una lista, quando il
    # risultato e' uno solo.
    return dati if isinstance(dati, list) else [dati]


def trova_bot() -> list[dict]:
    io_stesso = os.getpid()
    trovati = []
    for p in _processi_python():
        riga = p.get("CommandLine") or ""
        pid = p.get("ProcessId")
        if pid is None or pid == io_stesso:
            continue
        if DA_NON_TOCCARE.search(riga):
            continue
        if not E_UN_BOT.search(riga):
            continue
        m = re.search(r"accounts[\\/]([^\\/]+)[\\/]", riga)
        trovati.append(
            {
                "pid": pid,
                "ppid": p.get("ParentProcessId"),
                "account": m.group(1) if m else "?",
                "script": "run-dynamic" if "run-dynamic.py" in riga else "run.py",
            }
        )
    # Prima le foglie, poi i padri: cosi' nessun supervisore vede morire il
    # proprio figlio e prova a reagire mentre lo stiamo fermando. La catena e'
    # profonda quattro (launcher venv -> run-dynamic -> launcher -> run.py),
    # quindi non basta "run.py prima di run-dynamic": si conta la profondita'
    # reale risalendo i padri che sono anch'essi nell'elenco.
    padre_di = {p["pid"]: p["ppid"] for p in trovati}

    def profondita(pid: int) -> int:
        n, visti = 0, set()
        while pid in padre_di and pid not in visti:
            visti.add(pid)
            pid = padre_di[pid]
            n += 1
        return n

    trovati.sort(key=lambda p: profondita(p["pid"]), reverse=True)
    return trovati


def _vivo(pid: int) -> bool:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"Get-Process -Id {pid} -ErrorAction SilentlyContinue"],
        capture_output=True,
        text=True,
    ).stdout
    return bool(out.strip())


def salva_stato() -> Path | None:
    """Copia i .json degli account prima di terminare: se un processo muore
    a meta' di una scrittura, il file di stato puo' restare troncato."""
    dove = RADICE / "logs" / "deploy" / f"backup-stato-{datetime.now():%Y%m%d-%H%M%S}"
    copiati = 0
    for acc in ACCOUNT:
        sorgente = RADICE / "accounts" / acc["nome"]
        if not sorgente.is_dir():
            continue
        destinazione = dove / acc["nome"]
        destinazione.mkdir(parents=True, exist_ok=True)
        for f in sorgente.glob("*.json"):
            shutil.copy2(f, destinazione / f.name)
            copiati += 1
    if copiati:
        print(f"   stato salvato ({copiati} file) in {dove.relative_to(RADICE)}")
        return dove
    return None


def termina(bot: list[dict], dry_run: bool) -> None:
    for b in bot:
        etichetta = f"{b['script']:11} {b['account']:24} pid {b['pid']}"
        if dry_run:
            print(f"   [dry-run] fermerei  {etichetta}")
            continue
        # Prima il tentativo gentile, cosi' GramAddict puo' chiudere i suoi
        # file; poi /F per quelli che non rispondono (i processi console
        # senza finestra di solito ignorano la richiesta gentile).
        subprocess.run(
            ["taskkill", "/PID", str(b["pid"])],
            capture_output=True,
            text=True,
        )
        print(f"   fermo    {etichetta}")

    if dry_run:
        return

    scadenza = time.time() + 10
    while time.time() < scadenza:
        if not any(_vivo(b["pid"]) for b in bot):
            return
        time.sleep(1)

    for b in bot:
        if _vivo(b["pid"]):
            subprocess.run(
                ["taskkill", "/PID", str(b["pid"]), "/F"],
                capture_output=True,
                text=True,
            )
            print(f"   forzo    {b['script']:11} {b['account']:24} pid {b['pid']}")


def pulisci_lucchetti(dry_run: bool) -> None:
    """Toglie i device_*.lock il cui processo non c'e' piu'. Senza questo, il
    lancio successivo si rifiuta di partire con "sta gia' girando un bot"."""
    cartella = RADICE / "logs" / "deploy"
    for percorso in sorted(cartella.glob("device_*.lock")):
        try:
            dati = json.loads(percorso.read_text(encoding="utf-8"))
        except Exception:
            dati = {}
        pid = dati.get("pid")
        if pid and _vivo(int(pid)):
            print(f"   lascio   {percorso.name} (pid {pid} ancora vivo)")
            continue
        if dry_run:
            print(f"   [dry-run] toglierei {percorso.name}")
            continue
        percorso.unlink(missing_ok=True)
        print(f"   tolgo    {percorso.name} (orfano)")


def rilancia(dry_run: bool) -> None:
    python = RADICE / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = Path(sys.executable)
    for acc in ACCOUNT:
        cmd = [
            str(python),
            str(RADICE / "run-dynamic.py"),
            "--config", acc["config"],
            "--sessions", acc["sessioni"],
            "--avd", acc["avd"],
        ]
        if dry_run:
            print(f"   [dry-run] rilancerei {acc['nome']}")
            continue
        flag = {}
        if os.name == "nt":
            # Indipendente da questo script: deve sopravvivere alla sua fine.
            flag["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        registro = RADICE / "logs" / "deploy" / f"{acc['nome']}.avvio.out"
        registro.parent.mkdir(parents=True, exist_ok=True)
        with open(registro, "w", encoding="utf-8") as f:
            subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=RADICE, **flag)
        print(f"   avvio    {acc['nome']:24} -> logs/deploy/{registro.name}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--riavvia", action="store_true", help="dopo aver fermato, rilancia i due account")
    ap.add_argument("--dry-run", action="store_true", help="mostra cosa farebbe, senza farlo")
    args = ap.parse_args()

    os.chdir(RADICE)

    bot = trova_bot()
    if not bot:
        print("Nessun bot attivo.")
    else:
        print(f"Bot attivi: {len(bot)}")
        for b in bot:
            print(f"   {b['script']:11} {b['account']:24} pid {b['pid']} (padre {b['ppid']})")
        print("\nFermo i processi (uno a uno, gli emulatori non si toccano):")
        if not args.dry_run:
            salva_stato()
        termina(bot, args.dry_run)

    print("\nLucchetti device:")
    pulisci_lucchetti(args.dry_run)

    if args.dry_run:
        print("\n(dry-run: non ho fermato nulla)")
        return 0

    rimasti = trova_bot()
    if rimasti:
        print(f"\nATTENZIONE: {len(rimasti)} processi ancora vivi:")
        for b in rimasti:
            print(f"   {b['script']} {b['account']} pid {b['pid']}")
        return 1

    if args.riavvia:
        print("\nRilancio:")
        rilancia(args.dry_run)
        print("\nFatto. I bot girano staccati da PyCharm: l'output e' nei log,")
        print("non in questa finestra (logs/rb.coach.log, logs/<account>.log).")
    else:
        print("\nTutti i bot sono fermi. Puoi lanciare le configurazioni 'Bot - ...'.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
