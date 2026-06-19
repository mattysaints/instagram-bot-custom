"""SourceStats — per-source tracker of follows performed and (optionally)
follow-back rates, persisted at ``accounts/<user>/source_stats.json``.

The bot uses these stats to weight the shuffle / truncation of source lists
so that high-quality sources (good follow-back rate) are picked more often
than dead ones. This is a soft penalty — sources are never permanently
removed by this module: the user keeps full control via the YAML config.

Filling ``follows_back`` accurately requires comparing the bot's "followed"
list against the account's current "Followers" list on Instagram; that's
expensive and is left to a separate offline command (``recompute-fbr``)
that the user can run periodically. In the meantime, ``follows_done`` and
``last_follow_at`` are updated automatically and are already useful for
basic source rotation logic.

JSON shape::

    {
      "version": 1,
      "sources": {
        "blogger-followers|gymitabody": {
          "follows_done": 35,
          "follows_back": 7,
          "follow_back_rate": 0.20,
          "last_follow_at": "2026-05-04T17:24:50",
          "last_fbr_check": "2026-05-04T08:00:00"
        }
      }
    }
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from atomicwrites import atomic_write

logger = logging.getLogger(__name__)

FILENAME = "source_stats.json"
SCHEMA_VERSION = 1


def _key(job: str, source: str) -> str:
    return f"{job}|{source}"


class SourceStats:
    def __init__(self, account_path: str):
        self.account_path = account_path
        self.path = os.path.join(account_path, FILENAME)
        self._data: Dict = {"version": SCHEMA_VERSION, "sources": {}}
        self._load()

    # -- IO -------------------------------------------------------------------
    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict) or "sources" not in payload:
                logger.warning(
                    f"[source-stats] {self.path} has unexpected shape, ignoring."
                )
                return
            self._data = payload
            self._data.setdefault("version", SCHEMA_VERSION)
            self._data.setdefault("sources", {})
        except Exception as e:
            logger.warning(f"[source-stats] Failed to load {self.path}: {e}")

    def _save(self) -> None:
        try:
            os.makedirs(self.account_path, exist_ok=True)
            with atomic_write(self.path, overwrite=True, encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, sort_keys=False, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"[source-stats] Failed to save {self.path}: {e}")

    # -- Updates --------------------------------------------------------------
    def _entry(self, job: str, source: str) -> Dict:
        sources = self._data.setdefault("sources", {})
        return sources.setdefault(
            _key(job, source),
            {
                "follows_done": 0,
                "follows_back": 0,
                "fbr_sample": 0,
                "follow_back_rate": None,
                "last_follow_at": None,
                "last_fbr_check": None,
            },
        )

    def register_follow(self, job: str, source: str) -> None:
        if not job or not source:
            return
        e = self._entry(job, source)
        e["follows_done"] = int(e.get("follows_done", 0)) + 1
        e["last_follow_at"] = datetime.now().isoformat(timespec="seconds")
        self._save()

    def update_fbr(
        self,
        job: str,
        source: str,
        follows_done: int,
        follows_back: int,
    ) -> None:
        """Update the follow-back stats from a recompute.

        ``follows_done`` here is the recompute's denominator: how many followed
        users for this source we could actually VERIFY against the current
        followers list (i.e. that still exist in interacted_users.json). We store
        it as ``fbr_sample`` — distinct from ``follows_done`` (the running count
        of follows performed, maintained by register_follow). The rate is based
        on the verifiable sample, and the weighting gates on that sample so a
        spurious 1/1=100% on a tiny sample can't bias source selection.
        """
        e = self._entry(job, source)
        # Do NOT clobber follows_done (the register_follow count) here.
        e["fbr_sample"] = int(follows_done)
        e["follows_back"] = int(follows_back)
        if follows_done > 0:
            e["follow_back_rate"] = round(follows_back / follows_done, 4)
        else:
            e["follow_back_rate"] = None
        e["last_fbr_check"] = datetime.now().isoformat(timespec="seconds")
        self._save()

    def fbr_sample(self, job: str, source: str) -> int:
        """How many follows for this source were verifiable in the last FBR
        recompute (the denominator the rate is based on)."""
        e = self._data.get("sources", {}).get(_key(job, source))
        return int(e.get("fbr_sample", 0)) if e else 0

    # -- Read API -------------------------------------------------------------
    def get(self, job: str, source: str) -> Dict:
        return dict(self._entry(job, source))

    def fbr(self, job: str, source: str) -> Optional[float]:
        """Return the cached follow_back_rate for the source, or None."""
        e = self._data.get("sources", {}).get(_key(job, source))
        if not e:
            return None
        return e.get("follow_back_rate")

    def follows_done(self, job: str, source: str) -> int:
        e = self._data.get("sources", {}).get(_key(job, source))
        return int(e.get("follows_done", 0)) if e else 0

    # -- Weighting helpers ----------------------------------------------------
    def weight(
        self,
        job: str,
        source: str,
        min_follows_for_signal: int = 10,
        low_fbr_threshold: float = 0.05,
        high_fbr_threshold: float = 0.15,
        low_factor: float = 0.3,
        high_factor: float = 1.5,
    ) -> float:
        """Return a multiplicative weight in [0.3, 1.5] used to bias the random
        selection of sources. Sources with too few signals get neutral weight
        (1.0). FBR < low_threshold → low_factor; FBR > high_threshold →
        high_factor. Linear interpolation in-between.
        """
        rate = self.fbr(job, source)
        # Gate on the VERIFIABLE sample (denominator the rate is based on), not
        # the raw follows_done count: a 1/1=100% on a tiny sample must stay
        # neutral instead of being treated as a top source.
        sample = self.fbr_sample(job, source)
        if rate is None or sample < min_follows_for_signal:
            return 1.0
        if rate <= low_fbr_threshold:
            return low_factor
        if rate >= high_fbr_threshold:
            return high_factor
        # linear interp
        span = high_fbr_threshold - low_fbr_threshold
        if span <= 0:
            return 1.0
        t = (rate - low_fbr_threshold) / span
        return low_factor + t * (high_factor - low_factor)

    def weighted_sample(
        self,
        job: str,
        sources: List[str],
        n: int,
    ) -> List[str]:
        """Pick ``n`` sources from ``sources`` without replacement, biased by
        per-source weight. Falls back to uniform if all weights are equal.
        """
        import random

        if n >= len(sources):
            return list(sources)
        weights = [max(self.weight(job, s), 0.01) for s in sources]
        # If all weights equal, just shuffle
        if len(set(round(w, 3) for w in weights)) == 1:
            return random.sample(sources, n)
        chosen: List[str] = []
        pool = list(sources)
        ws = list(weights)
        for _ in range(n):
            if not pool:
                break
            total = sum(ws)
            r = random.uniform(0, total)
            cum = 0.0
            idx = 0
            for i, w in enumerate(ws):
                cum += w
                if r <= cum:
                    idx = i
                    break
            chosen.append(pool.pop(idx))
            ws.pop(idx)
        return chosen

    # -- Auto-refresh bookkeeping ---------------------------------------------
    def last_auto_fbr_check(self) -> Optional[datetime]:
        """Datetime of the last automatic FBR refresh, or None if never run."""
        ts = self._data.get("last_auto_fbr_check")
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return None

    def mark_auto_fbr_check(self) -> None:
        """Stamp 'now' as the last automatic FBR refresh and persist."""
        self._data["last_auto_fbr_check"] = datetime.now().isoformat(timespec="seconds")
        self._save()

    def summary_rows(self) -> List[Tuple[str, int, int, int, Optional[float]]]:
        """Return [(source_key, follows_done, fbr_sample, back, rate)] sorted by
        rate desc, for logs. ``fbr_sample`` is the verifiable denominator the
        rate is based on; ``follows_done`` is the raw follows performed."""
        rows: List[Tuple[str, int, int, int, Optional[float]]] = []
        for key, e in self._data.get("sources", {}).items():
            rows.append(
                (
                    key,
                    int(e.get("follows_done", 0)),
                    int(e.get("fbr_sample", 0)),
                    int(e.get("follows_back", 0)),
                    e.get("follow_back_rate"),
                )
            )
        rows.sort(key=lambda r: (r[4] if r[4] is not None else -1.0), reverse=True)
        return rows

    # -- FBR recomputation utility --------------------------------------------
    def recompute_fbr_from_followers_set(
        self,
        interacted_users: Dict,
        my_followers_usernames: Iterable[str],
    ) -> Dict[str, Tuple[int, int]]:
        """Given the JSON of ``interacted_users.json`` and an iterable with the
        usernames currently following the account, recompute ``follows_back``
        and ``follow_back_rate`` for every (job, source) where at least one
        followed user is recorded with ``target`` and ``job_name``.

        Returns a per-source map ``{source_key: (done, back)}``.
        """
        followers_set = {u.lower() for u in my_followers_usernames}
        per_source: Dict[str, Tuple[int, int]] = {}
        for username, info in interacted_users.items():
            if not info or info.get("following_status") not in (
                "followed",
                "requested",
            ):
                continue
            target = info.get("target") or info.get("source")
            job = info.get("job_name") or info.get("job") or "unknown"
            if not target:
                continue
            key = f"{job}|{target}"
            done, back = per_source.get(key, (0, 0))
            done += 1
            if username.lower() in followers_set:
                back += 1
            per_source[key] = (done, back)

        for key, (done, back) in per_source.items():
            try:
                job, source = key.split("|", 1)
            except ValueError:
                continue
            self.update_fbr(job, source, done, back)
        return per_source

