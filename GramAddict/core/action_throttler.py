"""ActionThrottler — enforces minimum intervals between consecutive actions
of the same kind, regardless of where the action is triggered from.

Without this, the bot can occasionally fire 4 follows or 5 likes within a few
seconds (random_sleep happens to roll low values multiple times in a row).
That burst pattern is one of the easiest signals Instagram uses to flag
automation. Having a hard floor between same-typed actions converts the
"average rate" into a "maximum rate" and gives consistent anti-ban behavior.

Usage from anywhere in the codebase:
    from GramAddict.core.action_throttler import get_throttler, ActionType
    get_throttler().wait_if_needed(ActionType.FOLLOW)
    # ... now perform the actual click ...
    get_throttler().mark(ActionType.FOLLOW)

A singleton is initialized once per session via ``init_throttler(args)`` from
``bot_flow.py`` so all modules share the same state. ``mark`` is called
automatically by ``wait_if_needed`` if you don't explicitly track success;
splitting the two calls is only useful when the action might fail (then you
shouldn't mark it).
"""
from __future__ import annotations

import logging
from time import monotonic, sleep
from typing import Dict, Optional

from GramAddict.core.utils import get_value

logger = logging.getLogger(__name__)


class ActionType:
    FOLLOW = "follow"
    LIKE = "like"
    UNFOLLOW = "unfollow"
    COMMENT = "comment"
    PM = "pm"


# Conservative defaults aligned with anti-ban tables 2026 (followed/liked
# rates that human accounts typically don't exceed).
DEFAULT_MIN_INTERVALS = {
    ActionType.FOLLOW: "25-60",     # 1 follow / 25-60s -> max ~90/h, target ~60/h
    ActionType.LIKE: "8-20",        # 1 like / 8-20s -> max ~270/h, target ~150/h
    ActionType.UNFOLLOW: "20-45",
    ActionType.COMMENT: "60-180",
    ActionType.PM: "120-300",
}


class ActionThrottler:
    """Enforces a minimum interval between consecutive actions of the same kind."""

    def __init__(self, intervals: Dict[str, str], enabled: bool = True):
        self._intervals_raw = dict(intervals)
        self._last_ts: Dict[str, float] = {}
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _resolve_interval(self, action_type: str) -> float:
        """Resolve the raw interval spec ('25-60' or '30') to a concrete number."""
        raw = self._intervals_raw.get(action_type)
        if raw is None or str(raw) == "0":
            return 0.0
        try:
            n = get_value(str(raw), None, 0)
            return float(max(0, int(n)))
        except Exception:
            return 0.0

    def wait_if_needed(self, action_type: str) -> float:
        """Sleep just enough so that ``min_interval`` has elapsed since the last
        action of the same type. Returns the number of seconds slept (0 if no
        wait was needed). This call also updates the last-action timestamp on
        return; if the caller might fail to perform the action, use ``mark``
        explicitly only on success.
        """
        if not self._enabled:
            return 0.0
        min_interval = self._resolve_interval(action_type)
        slept = 0.0
        if min_interval > 0:
            last = self._last_ts.get(action_type)
            if last is not None:
                elapsed = monotonic() - last
                wait = min_interval - elapsed
                if wait > 0:
                    logger.info(
                        f"[throttle] {action_type}: enforcing min interval "
                        f"({min_interval:.1f}s, elapsed {elapsed:.1f}s) -> sleep {wait:.1f}s"
                    )
                    sleep(wait)
                    slept = wait
        self.mark(action_type)
        return slept

    def mark(self, action_type: str) -> None:
        """Record that an action of this type just happened (now)."""
        self._last_ts[action_type] = monotonic()


# --- Module-level singleton ---------------------------------------------------
_throttler: Optional[ActionThrottler] = None


def init_throttler(args) -> ActionThrottler:
    """Initialize (or replace) the singleton from CLI/YAML args.

    Arg names (kebab-case in YAML, snake_case on argparse):
      --action-throttle-enabled (bool, default true)
      --action-throttle-follow-min   (default '25-60')
      --action-throttle-like-min     (default '8-20')
      --action-throttle-unfollow-min (default '20-45')
      --action-throttle-comment-min  (default '60-180')
      --action-throttle-pm-min       (default '120-300')
    """
    enabled = bool(getattr(args, "action_throttle_enabled", True))
    intervals = dict(DEFAULT_MIN_INTERVALS)
    mapping = {
        ActionType.FOLLOW: "action_throttle_follow_min",
        ActionType.LIKE: "action_throttle_like_min",
        ActionType.UNFOLLOW: "action_throttle_unfollow_min",
        ActionType.COMMENT: "action_throttle_comment_min",
        ActionType.PM: "action_throttle_pm_min",
    }
    for action, attr in mapping.items():
        raw = getattr(args, attr, None)
        if raw is not None:
            intervals[action] = str(raw)

    global _throttler
    _throttler = ActionThrottler(intervals, enabled=enabled)
    if enabled:
        logger.info(
            "[throttle] ActionThrottler enabled. Min intervals (s): "
            + ", ".join(f"{k}={v}" for k, v in intervals.items())
        )
    else:
        logger.info("[throttle] ActionThrottler disabled by config.")
    return _throttler


def get_throttler() -> ActionThrottler:
    """Return the singleton. Returns a no-op throttler if init was never called
    (so importing this module from tests/scripts is safe)."""
    global _throttler
    if _throttler is None:
        _throttler = ActionThrottler({}, enabled=False)
    return _throttler

