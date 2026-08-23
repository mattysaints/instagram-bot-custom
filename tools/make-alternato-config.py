#!/usr/bin/env python3
"""Genera accounts/<account>/config-alternato.yml: la giornata sfalsata.

PERCHE' ESISTE
    I config.yml di produzione fanno lavorare i due account NELLE STESSE
    finestre: i due emulatori girano insieme. Su un mini PC a 4 core questo
    significa due AVD da 2 core che si contendono la CPU, quindi sessioni piu'
    lente (con entrambi attivi Instagram ci ha messo 90 s ad aprire la barra
    delle schede).

    Questi config sono l'alternativa: le finestre dei due account non si
    toccano mai, nemmeno tenendo conto dello slittamento casuale, cosi' quando
    lavora uno l'altro dorme. Si lancia la coppia "alternata" oppure quella
    normale: la scelta e' di chi avvia i bot, non del codice.

    Il file viene DERIVATO dal config.yml di produzione: sorgenti, filtri,
    limiti, prompt dei commenti e device restano identici, cambia solo il
    blocco delle working-hours. Se tocchi il config.yml, rilancia questo
    script per riportare la modifica anche qui.

USO
    python tools/make-alternato-config.py
    python run-dynamic.py --config accounts/rb.coach/config-alternato.yml --fixed-hours --avd rbcoach
"""
import re
import sys
from pathlib import Path

# Finestre da 75 minuti dentro la fascia consentita 08:00-23:00, alternate fra
# i due account con 25 minuti di stacco. Con time-delta 0-10 lo slittamento
# massimo e' 10 minuti per parte, cioe' 20 < 25: due finestre consecutive non
# possono sovrapporsi nemmeno nel caso peggiore.
FINESTRE = {
    "rb.coach": ["08.10-09.25", "11.30-12.45", "14.50-16.05", "18.10-19.25", "21.30-22.45"],
    "roberto_buonomo_ifbbpro": ["09.50-11.05", "13.10-14.25", "16.30-17.45", "19.50-21.05"],
}
TIME_DELTA = "0-10"

INTESTAZIONE = """# ===========================================================================
# GIORNATA ALTERNATA - generata da tools/make-alternato-config.py
# ===========================================================================
# Copia di config.yml con le sole working-hours cambiate: qui i due account
# NON lavorano mai insieme (in config.yml invece si', in simultanea).
# Si lancia questa coppia quando si vuole un emulatore alla volta:
#     python run-dynamic.py --config accounts/{acct}/config-alternato.yml --fixed-hours --avd {avd}
# Ogni modifica va fatta su config.yml e riportata qui rilanciando lo script.
# ===========================================================================

"""

AVD = {"rb.coach": "rbcoach", "roberto_buonomo_ifbbpro": "robertobuo"}


def sostituisci(testo: str, chiave: str, valore: str, commento: str) -> str:
    """Sostituisce la riga 'chiave: ...' mantenendo il resto del file."""
    pattern = re.compile(rf"^\s*#?\s*{re.escape(chiave)}\s*:.*$", re.MULTILINE)
    if not pattern.search(testo):
        raise SystemExit(f"Nel config non c'e' la riga '{chiave}:': non so dove scrivere.")
    return pattern.sub(f"{chiave}: {valore}   # {commento}", testo, count=1)


def main() -> None:
    for acct, finestre in FINESTRE.items():
        sorgente = Path("accounts") / acct / "config.yml"
        if not sorgente.exists():
            raise SystemExit(f"Non trovo {sorgente}")
        testo = sorgente.read_text(encoding="utf-8")
        testo = sostituisci(
            testo,
            "working-hours",
            "[" + ", ".join(finestre) + "]",
            "giornata alternata: mai in contemporanea con l'altro account",
        )
        testo = sostituisci(
            testo,
            "time-delta",
            TIME_DELTA,
            "slittamento +/- 10 min: resta dentro i 25 min di stacco fra i due account",
        )
        destinazione = sorgente.with_name("config-alternato.yml")
        destinazione.write_text(
            INTESTAZIONE.format(acct=acct, avd=AVD[acct]) + testo, encoding="utf-8"
        )
        print(f"{destinazione}: {len(finestre)} finestre {finestre[0]} ... {finestre[-1]}")


if __name__ == "__main__":
    sys.exit(main())
