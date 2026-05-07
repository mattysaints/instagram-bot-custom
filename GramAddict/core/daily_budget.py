"""Persistent daily action budget across sessions / bot restarts.

GramAddict session limits (totalLikes, totalFollowed, ...) live only in
``SessionState`` instances and are reset to 0 every time the bot starts a new
session. This is dangerous when the user manually restarts the bot multiple
times in a single day (e.g. closing the laptop and reopening later): each
restart would re-arm a full session quota, easily blowing past Instagram's
anti-spam thresholds.

This module provides a tiny JSON-backed counter, scoped per local calendar day
(Europe/local-time of the host machine), that is loaded at session start to
*clip* the current session's per-action limits to whatever budget is left for
the day, and updated at session end with the deltas actually performed.

File layout (per account):

``accounts/<username>/daily_budget.json``

.. code-block:: json

    {
      "version": 1,
      "date": "2026-05-04",
      "follows": 14,
      "likes": 30,
      "unfollows": 0,
      "comments": 0,
      "pms": 0,
      "sessions_today": 2,
      "last_update": "2026-05-04T13:33:48"
    }

Atomic writes via ``atomicwrites`` so a crash between read and write cannot
corrupt the file (worst case: lose a few actions of accounting, never get
double counted).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Dict, Optional

from atomicwrites import atomic_write

logger = logging.getLogger(__name__)

_FILENAME = "daily_budget.json"
_VERSION = 1

# Mapping action -> JSON key (kept short to avoid typos in callers).
ACTION_KEYS = ("follows", "likes", "unfollows", "comments", "pms")


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _empty_state() -> Dict:
    return {
        "version": _VERSION,
        "date": _today_str(),
        "follows": 0,
        "likes": 0,
        "unfollows": 0,
        "comments": 0,
        "pms": 0,
        "sessions_today": 0,
        "last_update": None,
    }


class DailyBudget:
    """Persistent per-day action counter for a single Instagram account."""

    def __init__(self, account_path: str):
        self.path = os.path.join(account_path, _FILENAME)
        self.state: Dict = self._load()

    # ------------------------------------------------------------------ I/O

    def _load(self) -> Dict:
        if not os.path.isfile(self.path):
            return _empty_state()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return _empty_state()
            # Day rollover: if stored date != today, reset counters.
            if data.get("date") != _today_str():
                logger.info(
                    f"[daily-budget] New day detected (was {data.get('date')}, "
                    f"now {_today_str()}). Resetting daily counters."
                )
                return _empty_state()
            # Backfill missing keys (forward-compat).
            base = _empty_state()
            base.update({k: data.get(k, base[k]) for k in base})
            base["version"] = _VERSION
            base["date"] = _today_str()
            return base
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(
                f"[daily-budget] Could not read {self.path} ({e}). Starting empty."
            )
            return _empty_state()

    def _save(self) -> None:
        self.state["last_update"] = datetime.now().isoformat(timespec="seconds")
        try:
            with atomic_write(self.path, overwrite=True, encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.warning(f"[daily-budget] Could not write {self.path}: {e}")

    # ------------------------------------------------------------- Queries

    def used(self, action: str) -> int:
        return int(self.state.get(action, 0) or 0)

    def remaining(self, action: str, daily_cap: Optional[int]) -> Optional[int]:
        """Return remaining quota for ``action`` today, or ``None`` if no cap."""
        if daily_cap is None or int(daily_cap) <= 0:
            return None
        return max(0, int(daily_cap) - self.used(action))

    def clip_session_limit(
        self, action: str, session_limit: int, daily_cap: Optional[int]
    ) -> int:
        """Return the smaller of ``session_limit`` and remaining daily quota.

        Never returns negative; returns 0 if the daily cap is fully used.
        """
        remaining = self.remaining(action, daily_cap)
        if remaining is None:
            return int(session_limit)
        clipped = min(int(session_limit), remaining)
        return max(0, clipped)

    def all_caps_reached(self, caps: Dict[str, Optional[int]]) -> bool:
        """True if every provided non-zero cap has remaining == 0."""
        any_cap = False
        for action, cap in caps.items():
            if cap is None or int(cap) <= 0:
                continue
            any_cap = True
            if self.remaining(action, cap) > 0:
                return False
        return any_cap  # only "all reached" if at least one cap was active

    # --------------------------------------------------------------- Writes

    def increment(self, action: str, amount: int) -> None:
        if amount <= 0 or action not in ACTION_KEYS:
            return
        # Force date rollover check on write too (long-running sessions).
        if self.state.get("date") != _today_str():
            self.state = _empty_state()
        self.state[action] = self.used(action) + int(amount)
        self._save()

    def add_session_totals(
        self,
        followed: int = 0,
        liked: int = 0,
        unfollowed: int = 0,
        commented: int = 0,
        pm: int = 0,
    ) -> None:
        """Bulk-increment from a finished session. Single atomic write."""
        if self.state.get("date") != _today_str():
            self.state = _empty_state()
        self.state["follows"] = self.used("follows") + max(0, int(followed))
        self.state["likes"] = self.used("likes") + max(0, int(liked))
        self.state["unfollows"] = self.used("unfollows") + max(0, int(unfollowed))
        self.state["comments"] = self.used("comments") + max(0, int(commented))
        self.state["pms"] = self.used("pms") + max(0, int(pm))
        self.state["sessions_today"] = int(self.state.get("sessions_today", 0)) + 1
        self._save()

    # ---------------------------------------------------------------- Misc

    def snapshot(self) -> Dict:
        """Return a shallow copy of the current state."""
        return dict(self.state)

