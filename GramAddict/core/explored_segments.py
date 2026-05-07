"""
Persistenza dei "segmenti esplorati" per ogni sorgente (job, target).

Memorizza un anchor username per ogni coppia (job_name, target) così la
sessione successiva può riprendere da dove la precedente ha lasciato,
evitando di rivedere i soliti utenti in cima alla lista.

Schema del file JSON (accounts/<username>/explored_segments.json):
{
  "version": 1,
  "sources": {
    "<job_name>": {
      "<target>": {
        "last_anchor": "username_or_composite",
        "anchors_history": ["...", "...", "..."],   # max 5
        "last_seen_at": "ISO-8601",
        "total_iterations": 42,
        "exhausted_at": "ISO-8601 or null",
        "consecutive_anchor_misses": 0
      }
    }
  }
}
"""
import json
import logging
import os
from datetime import datetime, timedelta
from typing import List, Optional

from atomicwrites import atomic_write

logger = logging.getLogger(__name__)

FILENAME_EXPLORED_SEGMENTS = "explored_segments.json"
SCHEMA_VERSION = 1
ANCHORS_HISTORY_MAX = 5
MAX_CONSECUTIVE_MISSES = 3
# Soglia minima di schermate scrollate prima di poter dichiarare una sorgente
# "esaurita". Sotto questo valore, un end-of-list e' quasi sicuramente uno
# stallo temporaneo di scroll (zona di utenti gia' visti, lag IG, ecc.) e NON
# va trattato come exhausted (altrimenti la sorgente entra in cooldown a torto).
MIN_ITERATIONS_FOR_EXHAUSTED = 50


class ExploredSegments:
    def __init__(self, account_path: Optional[str]):
        self._enabled = account_path is not None
        if not self._enabled:
            self._data = {"version": SCHEMA_VERSION, "sources": {}}
            self._path = None
            return
        self._path = os.path.join(account_path, FILENAME_EXPLORED_SEGMENTS)
        self._data = {"version": SCHEMA_VERSION, "sources": {}}
        if os.path.isfile(self._path):
            try:
                with open(self._path, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and "sources" in loaded:
                    self._data = loaded
                    if "version" not in self._data:
                        self._data["version"] = SCHEMA_VERSION
            except Exception as e:
                logger.warning(
                    f"Could not load {self._path}: {e}. Starting fresh (file will be overwritten)."
                )

    # --------- private helpers ---------

    def _get_record(self, job: str, target: str) -> dict:
        sources = self._data.setdefault("sources", {})
        job_dict = sources.setdefault(job, {})
        rec = job_dict.setdefault(
            target,
            {
                "last_anchor": None,
                "anchors_history": [],
                "last_seen_at": None,
                "total_iterations": 0,
                "exhausted_at": None,
                "consecutive_anchor_misses": 0,
            },
        )
        # backfill missing keys for forward-compat
        rec.setdefault("last_anchor", None)
        rec.setdefault("anchors_history", [])
        rec.setdefault("last_seen_at", None)
        rec.setdefault("total_iterations", 0)
        rec.setdefault("exhausted_at", None)
        rec.setdefault("consecutive_anchor_misses", 0)
        return rec

    def _flush(self) -> None:
        if not self._enabled or self._path is None:
            return
        try:
            with atomic_write(self._path, overwrite=True, encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Could not save {self._path}: {e}")

    # --------- public API ---------

    def get_anchor(self, job: str, target: str) -> Optional[str]:
        rec = self._get_record(job, target)
        return rec.get("last_anchor")

    def get_anchors_fallback(self, job: str, target: str) -> List[str]:
        """Anchor candidates in priority order (most recent first)."""
        rec = self._get_record(job, target)
        out: List[str] = []
        if rec.get("last_anchor"):
            out.append(rec["last_anchor"])
        # history dal piu' recente al piu' vecchio, evitando duplicati
        for a in reversed(rec.get("anchors_history", [])):
            if a and a not in out:
                out.append(a)
        return out

    def set_anchor(self, job: str, target: str, anchor: str) -> None:
        if not anchor:
            return
        rec = self._get_record(job, target)
        prev = rec.get("last_anchor")
        if prev and prev != anchor:
            hist = rec.get("anchors_history", [])
            hist.append(prev)
            # tieni solo gli ultimi N
            rec["anchors_history"] = hist[-ANCHORS_HISTORY_MAX:]
        rec["last_anchor"] = anchor
        rec["last_seen_at"] = datetime.now().isoformat(timespec="seconds")
        rec["total_iterations"] = int(rec.get("total_iterations", 0)) + 1
        # ogni volta che salviamo un anchor valido, l'eventuale exhausted decade
        rec["exhausted_at"] = None
        rec["consecutive_anchor_misses"] = 0
        # se la sorgente ha effettivamente caricato utenti, non e' piu' un
        # blogger fantasma: azzeriamo il contatore di empty-page
        rec["consecutive_empty_first_visits"] = 0
        self._flush()

    def mark_exhausted(self, job: str, target: str, force: bool = False) -> bool:
        """Marca la sorgente come esaurita.

        Per evitare falsi positivi, se ``force`` e' False (default) la
        marcatura avviene SOLO quando in questo passaggio sono state
        scrollate abbastanza schermate (>= MIN_ITERATIONS_FOR_EXHAUSTED).
        Restituisce True se la marcatura e' stata applicata, False se e'
        stata scartata come "stallo temporaneo".

        IMPORTANTE: l'anchor NON viene mai cancellato. La marca exhausted
        e' usata SOLO come segnale "questa sorgente in questa sessione e'
        andata in zona-calda, salta al prossimo job". Il `last_anchor`
        deve sopravvivere perche' alla prossima sessione vogliamo
        riprendere DA li' (non dalla testa della lista) -- altrimenti
        cadiamo per sempre nella stessa trappola di utenti gia' visti.
        Il flag `exhausted_at` viene letto da `should_resume` con un
        cooldown breve (default 1 giorno via config) per dare alla coda
        della lista il tempo di "rinfrescarsi" naturalmente.
        """
        rec = self._get_record(job, target)
        iters = int(rec.get("total_iterations", 0))
        # Guard hard: se NON abbiamo mai iterato (lista followers non caricata,
        # empty page al primo tentativo), e' SEMPRE un falso positivo, anche
        # con force=True. Senza questo guard la sorgente entrerebbe in
        # cooldown 14gg al primo lag di rete.
        if iters == 0:
            logger.info(
                f"[explored_segments] {job}/{target}: empty page con 0 iterazioni "
                f"-> falso positivo (lista non caricata). NON marcato exhausted."
            )
            return False
        if not force and iters < MIN_ITERATIONS_FOR_EXHAUSTED:
            logger.info(
                f"[explored_segments] {job}/{target}: end-of-list dopo solo "
                f"{iters} schermate (<{MIN_ITERATIONS_FOR_EXHAUSTED}). "
                f"Probabile stallo, NON marcato exhausted (anchor preservato)."
            )
            return False
        rec["exhausted_at"] = datetime.now().isoformat(timespec="seconds")
        # NON tocchiamo last_anchor / anchors_history: vogliamo riprendere da
        # quel punto al prossimo giro (zona-calda transitoria, non vera fine).
        rec["consecutive_anchor_misses"] = 0
        self._flush()
        return True

    def should_resume(self, job: str, target: str, cooldown_days: int = 14) -> bool:
        """True se conviene cercare un anchor (resume); False se siamo in
        cooldown post-exhaustion E NON abbiamo un anchor utile.

        Nuova semantica (dopo che mark_exhausted preserva l'anchor):
          - se c'e' un anchor: SEMPRE resume da li' (mai ignorare un anchor
            valido, anche se la sorgente era "exhausted" -- significa solo
            che era andata in zona-calda l'ultima volta, ma le cose evolvono).
          - se NON c'e' anchor (sorgente vergine o anchor cancellato da
            register_anchor_miss): rispetta il cooldown.
        """
        rec = self._get_record(job, target)
        # se abbiamo un anchor valido, usalo sempre: il flag exhausted e' solo
        # un avviso per la fase di sampling delle sorgenti, non un veto sul resume
        if rec.get("last_anchor"):
            return True
        exhausted_at = rec.get("exhausted_at")
        if not exhausted_at:
            return False  # niente anchor, niente cooldown -> sorgente vergine, fallback
        try:
            ts = datetime.fromisoformat(exhausted_at)
        except Exception:
            return True
        return datetime.now() - ts > timedelta(days=max(0, int(cooldown_days)))

    def register_anchor_miss(self, job: str, target: str) -> int:
        rec = self._get_record(job, target)
        rec["consecutive_anchor_misses"] = (
            int(rec.get("consecutive_anchor_misses", 0)) + 1
        )
        misses = rec["consecutive_anchor_misses"]
        if misses >= MAX_CONSECUTIVE_MISSES:
            # reset anchor: probabilmente l'utente non e' piu' nella lista
            logger.info(
                f"[explored_segments] {job}/{target}: {misses} miss consecutivi -> reset anchor."
            )
            rec["last_anchor"] = None
            rec["anchors_history"] = []
            rec["consecutive_anchor_misses"] = 0
        self._flush()
        return misses

    def reset_anchor_misses(self, job: str, target: str) -> None:
        rec = self._get_record(job, target)
        if rec.get("consecutive_anchor_misses"):
            rec["consecutive_anchor_misses"] = 0
            self._flush()

    def mark_first_visit_empty(self, job: str, target: str) -> int:
        """Tracks how many times a 'never-explored' source returned an empty
        page on the very first iteration. Caller can use the count to decide
        whether to disable scroll-skip-start (start from top) or to abandon
        the source as truly inaccessible.
        Returns the new counter value.
        """
        rec = self._get_record(job, target)
        rec["consecutive_empty_first_visits"] = (
            int(rec.get("consecutive_empty_first_visits", 0)) + 1
        )
        self._flush()
        return rec["consecutive_empty_first_visits"]

    def reset_first_visit_empty(self, job: str, target: str) -> None:
        rec = self._get_record(job, target)
        if rec.get("consecutive_empty_first_visits"):
            rec["consecutive_empty_first_visits"] = 0
            self._flush()

    def get_first_visit_empty(self, job: str, target: str) -> int:
        rec = self._get_record(job, target)
        return int(rec.get("consecutive_empty_first_visits", 0))


