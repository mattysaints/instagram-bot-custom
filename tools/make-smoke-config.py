"""Genera accounts/<account>/config-smoke.yml: una sessione di prova corta.

PERCHE'
    Con il config di produzione un giro completo dura ore e un errore in un
    passaggio (lista follower, storie, griglia dei post, commento) si vede
    solo dopo decine di minuti, in mezzo a centinaia di righe. Qui invece:
    UNA sorgente per job, 3 profili, like e commento al 100%, sticker al
    100%: in 10-15 minuti il bot tocca ogni passaggio del flusso e il log
    dice esattamente dove si inceppa.

    Il file viene DERIVATO dal config.yml di produzione: filtri, hint dei
    commenti, impostazioni dello Space, device restano identici. Si cambiano
    solo sorgenti, quantita' e percentuali. Cosi' quello che si prova e' il
    comportamento vero, non una configurazione di comodo.

USO
    python tools/make-smoke-config.py            # rigenera per entrambi
    python run-dynamic.py --config accounts/rb.coach/config-smoke.yml --sessions 1 --avd rbcoach

    Da rilanciare dopo ogni modifica al config.yml, altrimenti lo smoke
    resta indietro rispetto alla produzione.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Sorgenti scelte per essere GRANDI e verificate (22/08/2026): una lista
# follower lunga non finisce dopo 10 profili, e un big ha sempre post freschi
# da commentare.
SORGENTI = {
    "rb.coach": {
        "blogger-followers": ["mcfit_it"],              # catena palestre, 74K, mai usata
        "blogger": ["informaconfede"],                  # coach generalista, 133K
        "hashtag-likers-recent": ["trasformazionefisica"],
    },
    "roberto_buonomo_ifbbpro": {
        "blogger-followers": ["andrea_mammoli"],          # IFBB pro, 338K, mai usata
        "blogger": ["andrea.presti_ifbbpro"],             # 606K (visibile solo dal personale)
        "hashtag-likers-recent": ["bodybuildingitalia"],
    },
}

# chiave -> valore da imporre (None = riga rimossa)
OVERRIDE = {
    "debug": "true",
    "shuffle-jobs": "false",
    "truncate-sources": "1",
    "scroll-skip-start": "5-10",
    "interactions-count": "3-3",
    "likes-count": "1",
    "likes-percentage": "100",
    "carousel-percentage": "100",
    "comment-percentage": "100",
    "interact-percentage": "100",
    "follow-percentage": "50",
    "sticker-check-percentage": "100",
    "stories-count": "0",
    "stories-percentage": "0",
    "action-throttle-follow-min": "20-30",
    "action-throttle-like-min": "5-10",
    "action-throttle-comment-min": "60-90",
    # I 3 profili del job blogger-followers possono esaurire da soli il
    # budget commenti: il job blogger (il big) arrivava sempre a limite
    # raggiunto e il suo commento non si provava mai. Margine per tutti e
    # tre i job: 3 + 1 (big) + 1 (hashtag).
    "total-likes-limit": "8-8",
    "total-follows-limit": "2-2",
    "total-comments-limit": "5-5",
    "total-successful-interactions-limit": "8-8",
    "total-interactions-limit": "999",
    "end-if-likes-limit-reached": "false",
    "end-if-follows-limit-reached": "false",
    "end-if-comments-limit-reached": "false",
    "total-sessions": "1",
    "time-delta": "0-0",
    "working-hours": None,          # la mette run-dynamic al lancio
    "hashtag-likers-top": None,
    "place-likers-recent": None,
}

INTESTAZIONE = """\
##############################################################################
# CONFIG DI PROVA (smoke) - generato da tools/make-smoke-config.py
#
# NON modificare a mano: deriva da config.yml e viene sovrascritto.
# Una sorgente per job, 3 profili, like/commento/sticker al 100%: serve a
# vedere in 10-15 minuti dove si inceppa il flusso, con il log in debug.
# Azioni reali sull'account: massimo 8 like, 2 follow, 5 commenti.
##############################################################################

"""


def sorgenti_fresche(account: str) -> dict:
    """Sceglie, fra le sorgenti del config di produzione, le piu' grandi MAI
    usate da questo account (lette dal log). Con can-reinteract-after a 168h
    uno smoke ripetuto sulle stesse sorgenti trova solo profili "already
    interacted" e non prova niente: e' successo due volte in un pomeriggio.
    """
    import json
    import re

    import yaml

    cfg = yaml.safe_load((REPO / "accounts" / account / "config.yml").read_text(encoding="utf-8"))
    log = REPO / "logs" / f"{account}.log"
    usate = set(re.findall(r"Handle #?(\S+)", log.read_text(encoding="utf-8", errors="replace"))) if log.exists() else set()
    follower = {}
    for f in (REPO / "dump").glob("*verifica*.json"):
        try:
            for r in json.load(open(f, encoding="utf-8")):
                if r.get("esito") == "esiste" and r.get("follower"):
                    follower[r["handle"]] = r["follower"]
        except Exception:
            pass
    for f in (REPO / "dump").glob("lista_utente*.json"):
        try:
            for r in json.load(open(f, encoding="utf-8")):
                if r.get("esito") == "esiste" and r.get("follower"):
                    follower[r["handle"]] = r["follower"]
        except Exception:
            pass

    def scegli(chiave, esclusi=()):
        cand = [h for h in cfg.get(chiave, []) if h not in usate and h not in esclusi]
        cand.sort(key=lambda h: -follower.get(h, 0))
        return cand[:1] or cfg.get(chiave, [])[:1]

    bf = scegli("blogger-followers")
    bl = scegli("blogger", esclusi=bf)
    ht = [h for h in cfg.get("hashtag-likers-recent", []) if h not in usate][:1] or cfg.get("hashtag-likers-recent", [])[:1]
    return {"blogger-followers": bf, "blogger": bl, "hashtag-likers-recent": ht}


def genera(account: str) -> Path:
    src = REPO / "accounts" / account / "config.yml"
    dst = REPO / "accounts" / account / "config-smoke.yml"
    righe = src.read_text(encoding="utf-8").splitlines()
    out = []
    override = dict(OVERRIDE)
    scelte = sorgenti_fresche(account)
    for k, v in scelte.items():
        override[k] = "[" + ", ".join(v) + "]"

    for r in righe:
        m = re.match(r"^([A-Za-z0-9_-]+)\s*:", r)
        if m and m.group(1) in override:
            val = override.pop(m.group(1))
            if val is None:
                continue
            out.append(f"{m.group(1)}: {val}")
        else:
            out.append(r)
    # chiavi imposte ma assenti nel config di partenza
    for k, v in override.items():
        if v is not None:
            out.append(f"{k}: {v}")
    dst.write_text(INTESTAZIONE + "\n".join(out) + "\n", encoding="utf-8")
    return dst


if __name__ == "__main__":
    import yaml

    for account in ("rb.coach", "roberto_buonomo_ifbbpro"):
        p = genera(account)
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        print(f"{p.relative_to(REPO)}: ok | device {d['device']} | "
              f"sorgenti {d['blogger-followers']} {d['blogger']} {d['hashtag-likers-recent']} | "
              f"commenti {d['comment-percentage']}% sticker {d['sticker-check-percentage']}% "
              f"limiti like/follow/commenti {d['total-likes-limit']}/{d['total-follows-limit']}/{d['total-comments-limit']}")
