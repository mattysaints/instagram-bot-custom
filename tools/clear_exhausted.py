#!/usr/bin/env python3
"""Azzera il flag 'exhausted_at' delle sorgenti in explored_segments.json.

Utile dopo aver corretto il bug che marcava per errore come "esaurite" sorgenti
grandi (es. @leonardopratoo 9k): le rende di nuovo disponibili SENZA toccare gli
anchor (così il resume riprende da dove era arrivato).

Uso:
    python tools/clear_exhausted.py [username]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    username = sys.argv[1] if len(sys.argv) > 1 else "marramattia_fmgpro"
    path = ROOT / "accounts" / username / "explored_segments.json"
    if not path.exists():
        sys.exit(f"File non trovato: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    cleared = []
    for job, targets in data.get("sources", {}).items():
        for target, rec in targets.items():
            if rec.get("exhausted_at"):
                rec["exhausted_at"] = None
                cleared.append(f"{job}|{target}")

    if not cleared:
        print("Nessuna sorgente marcata 'esaurita'. Niente da fare.")
        return

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Azzerato 'exhausted_at' per {len(cleared)} sorgenti (anchor preservati):")
    for k in cleared:
        print(f"  - {k}")


if __name__ == "__main__":
    main()
