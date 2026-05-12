"""Engagement-based unfollow protection.

Idea (vedi punto #3 di IDEAS):
    Non basta valutare "ti ha ri-seguito si/no" prima di unfolloware: alcuni
    utenti non ti seguono ma sono comunque clienti potenziali ad alto valore
    perche' interagiscono con i tuoi contenuti (mettono like, commentano).
    Sganciarli col bot e' un autogol di engagement.

Funzionamento:
    - Manteniamo un file ``accounts/<user>/engaged_users.json`` aggiornato a
      mano O da uno script esterno (vedi tools/refresh_engaged.py futuro).
    - Schema: { "username": { "first_seen": "...", "source": "manual|likes|comments|dm",
                              "note": "..." }, ... }
    - Prima di unfolloware un utente, ``action_unfollow_followers.py`` chiama
      ``is_engaged(username, account_path)``: se True -> SKIP unfollow.

API minimalista, zero dipendenze. Cache in-memory per evitare di rileggere
il file ad ogni utente in lista (le liste unfollow possono avere 200+ utenti).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from threading import Lock
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)

_FILENAME = "engaged_users.json"

# Cache (account_path -> (mtime, set di username lowercase)). Ricarica solo
# se il file e' cambiato. Lock per thread-safety (qualcuno potrebbe lanciare
# piu' job in parallelo in futuro).
_cache: Dict[str, tuple[float, Set[str]]] = {}
_lock = Lock()


def _path(account_path: str) -> str:
    return os.path.join(account_path, _FILENAME)


def _load(account_path: str) -> Set[str]:
    """Carica/ricarica il file engaged_users.json se il mtime e' cambiato.

    Ritorna sempre un set di username NORMALIZZATI (lowercase, stripped).
    Errori soft: se il file non esiste o e' corrotto, ritorna set vuoto e
    logga a DEBUG (non vogliamo rumore: l'engagement-protect e' un
    enhancement opzionale, non un componente critico).
    """
    p = _path(account_path)
    try:
        if not os.path.isfile(p):
            return set()
        mtime = os.path.getmtime(p)
    except OSError:
        return set()

    with _lock:
        cached = _cache.get(account_path)
        if cached is not None and cached[0] == mtime:
            return cached[1]

        users: Set[str] = set()
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in data.keys():
                    if isinstance(k, str) and k.strip():
                        users.add(k.strip().lstrip("@").lower())
            elif isinstance(data, list):
                # fallback: lista flat di username
                for k in data:
                    if isinstance(k, str) and k.strip():
                        users.add(k.strip().lstrip("@").lower())
        except (OSError, json.JSONDecodeError) as e:
            logger.debug(f"[engagement-protect] cannot read {p}: {e}")
            return set()

        _cache[account_path] = (mtime, users)
        return users


def is_engaged(username: str, account_path: str) -> bool:
    """True se l'utente e' in lista engaged -> NON unfolloware."""
    if not username or not account_path:
        return False
    norm = username.strip().lstrip("@").lower()
    if not norm:
        return False
    return norm in _load(account_path)


def add_engaged(
    username: str,
    account_path: str,
    source: str = "manual",
    note: Optional[str] = None,
) -> bool:
    """Aggiunge un username alla lista engaged. Idempotente.

    Ritorna True se l'utente e' stato aggiunto (o aggiornato), False su
    errore. Usato da script esterni / hot-add manuale. Non chiamato dal
    flow principale del bot (per ora).
    """
    if not username or not account_path:
        return False
    p = _path(account_path)
    try:
        os.makedirs(account_path, exist_ok=True)
    except OSError as e:
        logger.warning(f"[engagement-protect] cannot mkdir {account_path}: {e}")
        return False
    data: Dict = {}
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                data = raw
            elif isinstance(raw, list):
                data = {str(u).lstrip("@").lower(): {"source": "legacy"} for u in raw}
        except (OSError, json.JSONDecodeError):
            data = {}
    key = username.strip().lstrip("@").lower()
    entry = data.get(key, {})
    entry.setdefault("first_seen", datetime.now().isoformat(timespec="seconds"))
    entry["source"] = source
    if note:
        entry["note"] = note
    data[key] = entry
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
    except OSError as e:
        logger.warning(f"[engagement-protect] cannot write {p}: {e}")
        return False
    # invalida cache: ricaricheremo al prossimo is_engaged
    with _lock:
        _cache.pop(account_path, None)
    return True


def size(account_path: str) -> int:
    """Quanti utenti engaged hai (per logging/reporting)."""
    return len(_load(account_path))

