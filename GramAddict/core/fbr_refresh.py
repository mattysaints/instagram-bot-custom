"""Auto follow-back-rate (FBR) refresh.

The bot biases source selection towards sources with a good follow-back rate
(see ``source_stats.SourceStats`` and ``utils.sample_sources``). This module
keeps that data fresh: once per session (gated by ``--auto-fbr-refresh`` and an
interval) it scans the bot's own "Followers" list, compares it to the users it
has followed, and recomputes the per-source FBR in ``source_stats.json``.

Scan strategy (perf):
  * FULL scan: scroll the whole followers list. Accurate but slow on big
    accounts (minutes). Run only every ``--auto-fbr-full-rescan-days`` days, or
    the first time (no snapshot).
  * INCREMENTAL scan (default between full rescans): IG shows followers
    newest-first, so we only scroll the TOP until we hit a run of already-known
    followers (from the saved snapshot), then stop. Usually seconds. The new
    followers are unioned with the snapshot and the FBR is recomputed on the
    union — so it stays accurate without re-scrolling thousands of rows.
    A periodic full rescan corrects drift (followers who left).

Correctness: a *partial FULL* scan would mark not-yet-seen follows as
"not following back", deflating FBR -> in that case we skip the recompute.
Incremental scans are safe because the union already covers the deep part.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import List, Optional, Set, Tuple

from colorama import Fore

from GramAddict.core.device_facade import Timeout
from GramAddict.core.resources import ResourceID as resources
from GramAddict.core.utils import get_value, inspect_current_view, random_sleep
from GramAddict.core.views import Direction, ProfileView, TabBarView

logger = logging.getLogger(__name__)

_MAX_SCROLLS = 4000               # hard ceiling: a UI glitch can't spin forever
_SNAPSHOT_FILE = "followers_snapshot.json"
_STOP_AFTER_KNOWN_SCREENS = 2     # incremental: stop after N screens all-known


# ---------------------------------------------------------------- snapshot I/O
def _snapshot_path(storage) -> Optional[str]:
    ap = getattr(storage, "account_path", None)
    return os.path.join(ap, _SNAPSHOT_FILE) if ap else None


def _load_snapshot(storage) -> Optional[dict]:
    p = _snapshot_path(storage)
    if not p or not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[auto-fbr] snapshot load failed: {e}")
        return None


def _save_snapshot(storage, followers: Set[str], full_scan_at: Optional[str]) -> None:
    p = _snapshot_path(storage)
    if not p:
        return
    data = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "full_scan_at": full_scan_at,
        "followers": sorted(followers),
    }
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[auto-fbr] snapshot save failed: {e}")


# ---------------------------------------------------------------- entry point
def maybe_refresh_fbr(device, configs, storage, session_state) -> None:
    """Called once per session from bot_flow. Never raises."""
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
            if datetime.now() - last < timedelta(hours=interval_hours):
                logger.info(
                    f"[auto-fbr] Last refresh was {last:%Y-%m-%d %H:%M}, "
                    f"within {interval_hours}h interval. Skip.",
                    extra={"color": f"{Fore.CYAN}"},
                )
                return

        max_scan = get_value(getattr(args, "auto_fbr_max_scan", "0"), None, 0)
        full_rescan_days = get_value(
            getattr(args, "auto_fbr_full_rescan_days", "7"), None, 7
        )
        ResourceID = resources(args.app_id)

        # Decidi full vs incrementale in base allo snapshot salvato.
        snap = _load_snapshot(storage)
        known: Set[str] = set(snap["followers"]) if snap and snap.get("followers") else set()
        full_scan_at: Optional[str] = snap.get("full_scan_at") if snap else None
        need_full = True
        if known and full_scan_at:
            try:
                need_full = datetime.now() - datetime.fromisoformat(
                    full_scan_at
                ) >= timedelta(days=max(0, full_rescan_days))
            except Exception:
                need_full = True

        if need_full:
            logger.info(
                "[auto-fbr] FULL scan della lista follower...",
                extra={"color": f"{Fore.CYAN}"},
            )
            collected, complete, entered = _collect_followers(
                device, ResourceID, session_state.my_username, max_scan
            )
            if entered:
                device.back()
            if not collected:
                logger.warning("[auto-fbr] 0 follower raccolti, skip recompute.")
                return
            if not complete:
                logger.warning(
                    f"[auto-fbr] FULL scan interrotto al cap {max_scan} "
                    f"({len(collected)} raccolti). Scan parziale falserebbe l'FBR: "
                    "recompute SALTATO. Alza --auto-fbr-max-scan (o 0)."
                )
                return
            final_set = collected
            new_full_scan_at = datetime.now().isoformat(timespec="seconds")
        else:
            logger.info(
                f"[auto-fbr] Scan INCREMENTALE (snapshot: {len(known)} follower noti)...",
                extra={"color": f"{Fore.CYAN}"},
            )
            collected, _complete, entered = _collect_followers(
                device,
                ResourceID,
                session_state.my_username,
                max_scan,
                known=known,
                stop_after_known_screens=_STOP_AFTER_KNOWN_SCREENS,
            )
            if entered:
                device.back()
            fresh = collected - known
            final_set = known | collected
            new_full_scan_at = full_scan_at  # invariato fino al prossimo full
            logger.info(
                f"[auto-fbr] Incrementale: +{len(fresh)} nuovi follower, "
                f"totale {len(final_set)}.",
                extra={"color": f"{Fore.GREEN}"},
            )

        _save_snapshot(storage, final_set, new_full_scan_at)
        per_source = stats.recompute_fbr_from_followers_set(
            storage.interacted_users, final_set
        )
        stats.mark_auto_fbr_check()
        logger.info(
            f"[auto-fbr] FBR ricalcolato su {len(final_set)} follower "
            f"({len(per_source)} sorgenti).",
            extra={"color": f"{Fore.GREEN}"},
        )
        _log_summary(stats)

    except Exception as e:  # noqa: BLE001
        logger.warning(f"[auto-fbr] Refresh fallito (non fatale): {e}")
        try:
            device.back()
        except Exception:
            pass


# ---------------------------------------------------------------- scraping
def _collect_followers(
    device,
    ResourceID,
    my_username: str,
    max_scan: int,
    known: Optional[Set[str]] = None,
    stop_after_known_screens: int = 0,
) -> Tuple[Set[str], bool, bool]:
    """Scrape the followers list.

    Returns ``(usernames, complete, entered)``.
    - Full mode (``stop_after_known_screens == 0``): scrolls to the end;
      ``complete`` is False only if the ``max_scan`` cap was hit first.
    - Incremental mode (``known`` + ``stop_after_known_screens > 0``): stops
      after N consecutive screens whose users are ALL already in ``known``
      (we've reached the part already captured last time).
    """
    followers: Set[str] = set()
    incremental = bool(known is not None and stop_after_known_screens > 0)

    if ProfileView(device).getUsername() != my_username:
        TabBarView(device).navigateToProfile()
    if not ProfileView(device).navigateToFollowers():
        logger.warning("[auto-fbr] Impossibile aprire la lista follower.")
        return followers, False, False

    user_list = device.find(resourceIdMatches=ResourceID.USER_LIST_CONTAINER)
    if not user_list.exists(Timeout.LONG):
        logger.warning("[auto-fbr] Lista follower non renderizzata.")
        return followers, False, True

    prev_screen: List[str] = []
    scrolls = 0
    capped = False
    consecutive_known = 0
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
                    continue  # riga parziale/sticky: salta, non e' fine lista
                username = (user_name_view.get_text() or "").strip()
                if not username:
                    continue
                screen.append(username)
                if username.casefold() != (my_username or "").casefold():
                    followers.add(username)
        except IndexError:
            pass

        # Incrementale: se l'intera schermata e' gia' nota, siamo arrivati alla
        # parte gia' catturata -> dopo N schermate cosi' ci fermiamo.
        if incremental and screen:
            all_known = all(u in known for u in screen)
            consecutive_known = consecutive_known + 1 if all_known else 0
            if consecutive_known >= stop_after_known_screens:
                logger.info(
                    "[auto-fbr] Raggiunta la zona gia' nota, stop scan incrementale.",
                    extra={"color": f"{Fore.GREEN}"},
                )
                break

        if max_scan and len(followers) >= max_scan:
            capped = True
            break

        if screen != prev_screen:
            prev_screen = screen
            device.find(resourceId=ResourceID.LIST).scroll(Direction.DOWN)
            scrolls += 1
            random_sleep(0.5, 1.2, modulable=False, log=False)
        else:
            load_more = device.find(resourceId=ResourceID.ROW_LOAD_MORE_BUTTON)
            if load_more.exists():
                load_more.click()
                random_sleep(1, 2, modulable=False, log=False)
                device.find(resourceId=ResourceID.LIST).scroll(Direction.DOWN)
                scrolls += 1
            else:
                logger.info(
                    f"[auto-fbr] Fine lista follower ({len(followers)} raccolti).",
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
        "[auto-fbr] Top sorgenti per FBR (source | followed | back/sample | rate). "
        "'*' = campione sufficiente a pesare la selezione (>=10).",
        extra={"color": f"{Fore.CYAN}"},
    )
    for key, done, sample, back, rate in rows[:top]:
        rate_str = f"{rate*100:5.1f}%" if rate is not None else "  n/a"
        trusted = "*" if sample >= 10 and rate is not None else " "
        logger.info(
            f"[auto-fbr] {trusted} {key:<52} fdone={done:>4}  {back:>3}/{sample:<4} {rate_str}"
        )
