"""Auto follow-back-rate (FBR) refresh.

The bot already biases source selection towards sources with a good
follow-back rate (see ``source_stats.SourceStats`` and ``utils.sample_sources``).
But that bias only kicks in once ``follow_back_rate`` is filled — and until now
that happened *only* by manually running ``tools/recompute_fbr.py`` against a
hand-exported followers list. As a result the whole optimization stayed
dormant for most users.

This module closes the loop: at the start of a session (gated by
``--auto-fbr-refresh`` and a configurable interval) the bot scrapes its own
"Followers" list once, compares it against the users it has followed, and
updates the per-source FBR in ``source_stats.json``. From that point on the
weighted source sampling is driven by *real* data and the bot self-optimizes
towards sources that actually follow back.

Correctness note: a *partial* scan would mark followed-but-not-yet-seen users
as "not following back", deflating the FBR. So if a scan cap is set and we hit
it before reaching the end of the list, we skip the recompute entirely rather
than persist corrupted numbers.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Set

from colorama import Fore

from GramAddict.core.device_facade import Timeout
from GramAddict.core.resources import ResourceID as resources
from GramAddict.core.utils import get_value, inspect_current_view, random_sleep
from GramAddict.core.views import (
    Direction,
    ProfileView,
    TabBarView,
)

logger = logging.getLogger(__name__)

# Hard safety ceiling on scroll iterations so a UI glitch can never spin
# forever on a huge followers list.
_MAX_SCROLLS = 4000


def maybe_refresh_fbr(device, configs, storage, session_state) -> None:
    """Entry point called once per session from bot_flow.

    Never raises: any failure is logged and swallowed so it can't break the
    session.
    """
    args = configs.args
    if not getattr(args, "auto_fbr_refresh", False):
        return
    stats = getattr(storage, "source_stats", None)
    if stats is None:
        logger.debug("[auto-fbr] SourceStats not available, skip.")
        return

    try:
        interval_hours = get_value(
            getattr(args, "auto_fbr_interval_hours", "24"), None, 24
        )
        last = stats.last_auto_fbr_check()
        if last is not None and interval_hours > 0:
            from datetime import datetime, timedelta

            if datetime.now() - last < timedelta(hours=interval_hours):
                logger.info(
                    f"[auto-fbr] Last refresh was {last:%Y-%m-%d %H:%M}, "
                    f"within {interval_hours}h interval. Skip.",
                    extra={"color": f"{Fore.CYAN}"},
                )
                return

        max_scan = get_value(getattr(args, "auto_fbr_max_scan", "0"), None, 0)
        ResourceID = resources(args.app_id)

        logger.info(
            "[auto-fbr] Refreshing follow-back-rate from your followers list...",
            extra={"color": f"{Fore.CYAN}"},
        )

        followers, complete, entered = _collect_followers(
            device, ResourceID, session_state.my_username, max_scan
        )

        # Leave the followers list (back to profile) only if we actually opened it.
        if entered:
            device.back()

        if not followers:
            logger.warning("[auto-fbr] Collected 0 followers, skip recompute.")
            return

        if not complete:
            logger.warning(
                f"[auto-fbr] Scan hit the cap of {max_scan} before the end of the "
                f"list ({len(followers)} collected). A partial scan would deflate "
                "the FBR, so the recompute is SKIPPED. Raise --auto-fbr-max-scan "
                "(or set it to 0 for a full scan) to enable it."
            )
            return

        per_source = stats.recompute_fbr_from_followers_set(
            storage.interacted_users, followers
        )
        stats.mark_auto_fbr_check()

        logger.info(
            f"[auto-fbr] Recomputed FBR over {len(followers)} followers "
            f"across {len(per_source)} source(s).",
            extra={"color": f"{Fore.GREEN}"},
        )
        _log_summary(stats)

    except Exception as e:
        logger.warning(f"[auto-fbr] Refresh failed (non-fatal): {e}")
        # Best effort: don't leave the bot stranded inside the followers list.
        try:
            device.back()
        except Exception:
            pass


def _collect_followers(
    device, ResourceID, my_username: str, max_scan: int
) -> tuple[Set[str], bool, bool]:
    """Scrape the bot's own "Followers" list into a set of usernames.

    Returns ``(usernames, complete, entered)`` where ``complete`` is False if
    the scan was stopped early because it hit ``max_scan`` (``max_scan == 0``
    means no cap, always complete on a clean end-of-list), and ``entered`` is
    True if we actually opened the followers list (so the caller knows whether
    a ``back()`` is needed to return to the profile).
    """
    followers: Set[str] = set()

    # Make sure we're on our own profile, then open Followers.
    if ProfileView(device).getUsername() != my_username:
        TabBarView(device).navigateToProfile()
    if not ProfileView(device).navigateToFollowers():
        logger.warning("[auto-fbr] Could not open the followers list.")
        return followers, False, False

    user_list = device.find(resourceIdMatches=ResourceID.USER_LIST_CONTAINER)
    if not user_list.exists(Timeout.LONG):
        logger.warning("[auto-fbr] Followers list did not render.")
        return followers, False, True

    prev_screen: List[str] = []
    scrolls = 0
    capped = False
    while scrolls < _MAX_SCROLLS:
        screen: List[str] = []
        user_list = device.find(resourceIdMatches=ResourceID.USER_LIST_CONTAINER)
        row_height, _ = inspect_current_view(user_list)
        try:
            for item in user_list:
                if item.get_height() < row_height:
                    continue
                user_info_view = item.child(index=1)
                user_name_view = user_info_view.child(index=0).child()
                if not user_name_view.exists():
                    break
                username = (user_name_view.get_text() or "").strip()
                if not username:
                    continue
                screen.append(username)
                if username.casefold() != (my_username or "").casefold():
                    followers.add(username)
        except IndexError:
            pass

        if max_scan and len(followers) >= max_scan:
            capped = True
            break

        if screen != prev_screen:
            prev_screen = screen
            list_view = device.find(resourceId=ResourceID.LIST)
            list_view.scroll(Direction.DOWN)
            scrolls += 1
            random_sleep(0.5, 1.2, modulable=False, log=False)
        else:
            load_more = device.find(resourceId=ResourceID.ROW_LOAD_MORE_BUTTON)
            if load_more.exists():
                load_more.click()
                random_sleep(1, 2, modulable=False, log=False)
                list_view = device.find(resourceId=ResourceID.LIST)
                list_view.scroll(Direction.DOWN)
                scrolls += 1
            else:
                logger.info(
                    f"[auto-fbr] Reached end of followers list "
                    f"({len(followers)} collected).",
                    extra={"color": f"{Fore.GREEN}"},
                )
                break

    complete = not capped and scrolls < _MAX_SCROLLS
    return followers, complete, True


def _log_summary(stats, top: int = 10) -> None:
    rows = stats.summary_rows()
    if not rows:
        return
    logger.info(
        "[auto-fbr] Top sources by FBR (source | followed | verified back/sample | rate). "
        "'*' = enough sample to bias selection (>=10).",
        extra={"color": f"{Fore.CYAN}"},
    )
    for key, done, sample, back, rate in rows[:top]:
        rate_str = f"{rate*100:5.1f}%" if rate is not None else "  n/a"
        trusted = "*" if sample >= 10 and rate is not None else " "
        logger.info(
            f"[auto-fbr] {trusted} {key:<52} fdone={done:>4}  {back:>3}/{sample:<4} {rate_str}"
        )
